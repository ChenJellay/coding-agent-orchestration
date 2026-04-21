"""Regression tests for the per-agent output budgets and prompt invariants
introduced after the judge_v1 over-thinking incident.

Two layers of defence are tested:

1. ``AgentSpec.max_output_tokens`` is the authoritative ceiling per agent and
   ``run_agent`` must clamp any chain-DSL ``max_tokens`` down to it.
2. Single-shot structured-output prompts must not instruct the model to emit
   a ``<think>`` block — that fights the MLX ``enable_thinking=False`` chat
   template and the structured-output schema preamble, and is what caused the
   200 s judge stall in production.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from agenti_helix.agents.registry import _AGENTS, get_agent
from agenti_helix.runtime import agent_runtime as ar


# ---------------------------------------------------------------------------
# AgentSpec budgets
# ---------------------------------------------------------------------------


# Hard ceilings audited per agent.  These exist so that an accidental edit
# (e.g. someone bumps judge_v1 back to 6144) trips a test instead of a
# 3-minute production stall.  Numbers mirror registry.py.
EXPECTED_AGENT_BUDGETS: Dict[str, int] = {
    "coder_patch_v1": 1024,
    "intent_compiler_v1": 4096,
    "context_librarian_v1": 2048,
    "sdet_v1": 4096,
    "coder_builder_v1": 8192,
    "security_governor_v1": 1536,
    "judge_evaluator_v1": 2048,
    "judge_v1": 768,
    "doc_fetcher_v1": 2048,
    "diff_validator_v1": 1536,
    "linter_v1": 2048,
    "type_checker_v1": 2048,
    "memory_summarizer_v1": 1024,
    "supreme_court_v1": 1536,
}


def test_every_registered_agent_has_a_budget() -> None:
    """No agent should slip in without an explicit budget."""
    missing = [a for a in _AGENTS if a not in EXPECTED_AGENT_BUDGETS]
    assert not missing, (
        f"Agents missing from EXPECTED_AGENT_BUDGETS: {missing}. "
        "Add an entry to test_agent_budgets.py and pick a tight cap."
    )


@pytest.mark.parametrize("agent_id,expected_cap", sorted(EXPECTED_AGENT_BUDGETS.items()))
def test_agent_spec_budget_matches_audit(agent_id: str, expected_cap: int) -> None:
    spec = get_agent(agent_id)
    assert spec.max_output_tokens == expected_cap


def test_judge_v1_cap_is_under_one_thousand() -> None:
    """The judge produces PASS/FAIL + one sentence — anything bigger is over-thinking."""
    assert get_agent("judge_v1").max_output_tokens < 1024


# ---------------------------------------------------------------------------
# Runtime clamping
# ---------------------------------------------------------------------------


class _CapturingBackend:
    """Backend stub that records the max_tokens it was asked to generate with."""

    def __init__(self, response: str = '{"verdict":"PASS","justification":"ok","problematic_lines":[]}') -> None:
        self.response = response
        self.calls: List[Dict[str, Any]] = []

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: Any,
        temperature: float,
        on_progress: Any = None,
    ) -> str:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature})
        return self.response


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> _CapturingBackend:
    backend = _CapturingBackend()
    monkeypatch.setattr(ar, "get_default_inference_backend", lambda _cfg: backend)
    return backend


_JUDGE_INPUT = {
    "repo_path": "/r",
    "target_file": "f",
    "acceptance_criteria": "ok",
    "original_snippet": "",
    "edited_snippet": "",
    "language": "python",
    "tool_logs_json": "{}",
}


def test_runtime_clamps_chain_max_tokens_to_spec_ceiling(fake_backend: _CapturingBackend) -> None:
    """Chain DSL passing 6144 must be clamped down to judge_v1's 768 ceiling."""
    ar.run_agent(
        agent_id="judge_v1",
        raw_input=_JUDGE_INPUT,
        runtime={"max_tokens": 6144, "temperature": 0.0},
    )
    assert fake_backend.calls, "backend.generate was never invoked"
    assert fake_backend.calls[0]["max_tokens"] == 768


def test_runtime_uses_spec_cap_when_chain_omits_max_tokens(fake_backend: _CapturingBackend) -> None:
    ar.run_agent(agent_id="judge_v1", raw_input=_JUDGE_INPUT, runtime={"temperature": 0.0})
    assert fake_backend.calls[0]["max_tokens"] == 768


def test_runtime_keeps_smaller_chain_max_tokens(fake_backend: _CapturingBackend) -> None:
    """If a chain step intentionally tightens the budget below the spec, honour it."""
    ar.run_agent(agent_id="judge_v1", raw_input=_JUDGE_INPUT, runtime={"max_tokens": 256})
    assert fake_backend.calls[0]["max_tokens"] == 256


# ---------------------------------------------------------------------------
# Truncation heuristic
# ---------------------------------------------------------------------------


def test_looks_truncated_flags_explicit_parser_message() -> None:
    """The JSON extractor's "never closes" message is a high-confidence signal."""
    assert ar._looks_truncated(
        raw='{"verdict": "PASS", "justification": "this goes on forever',
        max_tokens=512,
        error_message="Model output appears to start a JSON object but never closes it.",
    ) is True


def test_looks_truncated_flags_long_unbalanced_output() -> None:
    """Output close to the budget that still has more opens than closes → truncated."""
    # 1800 chars ≈ ~515 tokens at 3.5 chars/token; budget=512 → >85%.
    body = '{"a": {"b": {"c": "' + ("x" * 1800)
    assert ar._looks_truncated(
        raw=body,
        max_tokens=512,
        error_message="some other parse error",
    ) is True


def test_looks_truncated_ignores_short_failures() -> None:
    """A well-below-budget parse failure should NOT be flagged truncated."""
    assert ar._looks_truncated(
        raw='not json at all',
        max_tokens=1024,
        error_message="Model output did not contain a JSON object (no '{' found).",
    ) is False


def test_looks_truncated_ignores_balanced_output() -> None:
    """Even at high length, balanced braces mean the object closed — not truncated."""
    body = "{" + ('"k": "v", ' * 500) + '"final": "x"}'  # long but balanced
    assert ar._looks_truncated(
        raw=body,
        max_tokens=512,
        error_message="something about commas",
    ) is False


# ---------------------------------------------------------------------------
# run_agent raises with truncated=True when the heuristic matches
# ---------------------------------------------------------------------------


class _FixedBackend:
    """Returns a fixed raw string regardless of max_tokens."""

    def __init__(self, raw: str) -> None:
        self.raw = raw

    def generate(self, prompt: str, *, max_tokens: Any, temperature: float, on_progress: Any = None) -> str:
        return self.raw


# Use ``prompt_override`` to bypass prompt rendering (tests don't need to
# supply every template variable for the agent), and use intent_compiler_v1
# for truncation round-trip tests because judge_v1 has a regex-based fallback
# (``try_fallback_snippet_judge_dict``) that turns mid-sentence cutoffs into
# a recovered verdict — masking truncation.
def test_run_agent_marks_truncated_parse_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mid-JSON cutoff response must raise StructuredOutputError(truncated=True)."""
    from agenti_helix.runtime.agent_runtime import StructuredOutputError

    truncated_raw = '{"dag_id": "d1", "nodes": [{"task": {"intent": "this got cut'
    monkeypatch.setattr(ar, "get_default_inference_backend", lambda _cfg: _FixedBackend(truncated_raw))
    with pytest.raises(StructuredOutputError) as exc_info:
        ar.run_agent(
            agent_id="intent_compiler_v1",
            raw_input={},
            runtime={"max_tokens": 64},
            prompt_override="stub prompt",
        )
    assert exc_info.value.kind == "parse"
    assert exc_info.value.truncated is True


def test_run_agent_does_not_flag_validate_failures_as_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid JSON with a schema mismatch is NOT a truncation — repair could still help."""
    from agenti_helix.runtime.agent_runtime import StructuredOutputError

    # Valid JSON, but missing required intent_compiler fields so Pydantic rejects.
    valid_but_wrong = '{"foo": "bar"}'
    monkeypatch.setattr(ar, "get_default_inference_backend", lambda _cfg: _FixedBackend(valid_but_wrong))
    with pytest.raises(StructuredOutputError) as exc_info:
        ar.run_agent(
            agent_id="intent_compiler_v1",
            raw_input={},
            runtime={"max_tokens": 64},
            prompt_override="stub prompt",
        )
    assert exc_info.value.kind == "validate"
    assert exc_info.value.truncated is False


# ---------------------------------------------------------------------------
# Prompt invariants
# ---------------------------------------------------------------------------


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "agenti_helix" / "agents" / "prompts"


# Every registered agent is now routed through ``run_agent_structured`` + the
# JSON schema preamble (see ``structured_output.STRUCTURED_AGENT_IDS`` ==
# ``_AGENTS.keys()``). The schema preamble explicitly forbids both ``<think>``
# and ``<redacted_thinking>`` blocks, so prompt bodies must agree — otherwise
# local quant models resolve the contradiction by burning the entire token
# budget on prose. The "bounded thinking" carve-out for coder_builder and
# sdet was removed after the SDET over-thinking incident: 4-bit MLX models
# do not respect the bound.
PROMPTS_THAT_MUST_NOT_REQUEST_THINK: List[str] = [
    "judge.md",
    "intent_compiler.md",
    "coder_patch.md",
    "judge_evaluator.md",
    "security_governor.md",
    "doc_fetcher.md",
    "diff_validator.md",
    "linter.md",
    "type_checker.md",
    "context_librarian_scout.md",
    "code_searcher.md",
    "memory_summarizer.md",
    "supreme_court.md",
    "sdet_test_writer.md",
    "coder_builder.md",
]


@pytest.mark.parametrize("filename", PROMPTS_THAT_MUST_NOT_REQUEST_THINK)
def test_prompt_does_not_instruct_think_block(filename: str) -> None:
    """Single-shot prompts must not ask the model to first reason inside a hidden block.

    The MLX chat template sets ``enable_thinking=False`` and the structured-
    output preamble forbids ``<think>`` / ``<redacted_thinking>`` — a prompt
    asking for one is a direct contradiction that local quant models resolve
    by emitting very long reasoning chains.
    """
    text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")

    # We deliberately KEEP phrases like "no `<think>` block" as a negative
    # deterrent — those are fine. What we forbid are *positive* instructions
    # to produce one (in either tag variant).
    forbidden_phrases = [
        # `<think>` variants
        "reason step-by-step inside `<think>",
        "reason step-by-step inside <think>",
        "wrap your reasoning inside `<think>",
        "wrap your reasoning inside <think>",
        "think step-by-step before producing",
        "First, reason step-by-step inside `<think>",
        "After your `<think>",
        "after `</think>`",
        "AFTER the closing `</think>`",
        # `<redacted_thinking>` variants (this codebase's stripped-by-the-parser tag)
        "reason step-by-step inside `<redacted_thinking>",
        "reason step-by-step inside <redacted_thinking>",
        "wrap your reasoning inside `<redacted_thinking>",
        "wrap your reasoning inside <redacted_thinking>",
        "First, reason step-by-step inside `<redacted_thinking>",
        "First, reason step-by-step inside <redacted_thinking>",
        "After your `<redacted_thinking>",
        "after `</redacted_thinking>`",
        "AFTER the closing `</redacted_thinking>`",
        "next line after `</redacted_thinking>`",
        "next line after </redacted_thinking>",
    ]
    lowered = text.lower()
    for pat in forbidden_phrases:
        assert pat.lower() not in lowered, (
            f"{filename}: prompt still contains an instruction that triggers a hidden "
            f"reasoning block: {pat!r}. Strip the CoT instruction; reasoning belongs "
            f"inside the JSON output fields (testing_strategy, implementation_logic, "
            f"audit_reasoning, evaluation_reasoning, search_strategy, …)."
        )


# Every agent prompt should also explicitly route reasoning into a JSON field,
# not into a hidden block. We check this by asserting that the prompt names
# at least one of the canonical reasoning fields. This catches a subtle
# regression: if someone strips the `<think>` instruction but forgets to point
# the model at the JSON reasoning field, the model just drops the reasoning
# entirely (silently bad outputs).
_REASONING_FIELDS_BY_PROMPT: Dict[str, str] = {
    "judge.md": "justification",
    "intent_compiler.md": "description",
    "coder_patch.md": "filePath",  # patch agents reason via the diff itself
    "judge_evaluator.md": "evaluation_reasoning",
    "security_governor.md": "audit_reasoning",
    "doc_fetcher.md": "task_relevance_summary",
    "diff_validator.md": "summary",
    "linter.md": "summary",
    "type_checker.md": "summary",
    "context_librarian_scout.md": "search_strategy",
    "memory_summarizer.md": "actionable_hint",
    "supreme_court.md": "justification",
    "sdet_test_writer.md": "testing_strategy",
    "coder_builder.md": "implementation_logic",
}


@pytest.mark.parametrize("filename,reasoning_field", sorted(_REASONING_FIELDS_BY_PROMPT.items()))
def test_prompt_routes_reasoning_into_json_field(filename: str, reasoning_field: str) -> None:
    """Each prompt must mention the JSON field where reasoning belongs.

    This is the positive counterpart to ``test_prompt_does_not_instruct_think_block``:
    we want reasoning to land *somewhere* observable + budgeted, not silently
    dropped because we removed the only instruction that asked for it.
    """
    text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    assert reasoning_field in text, (
        f"{filename}: expected the prompt to reference the JSON reasoning field "
        f"{reasoning_field!r} so the model knows where to put its rationale. "
        "Without this anchor, stripping the <think> instruction causes the model "
        "to drop reasoning entirely."
    )
