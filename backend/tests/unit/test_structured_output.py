"""Unit tests for the structured-output repair wrapper."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

import pytest

from agenti_helix.runtime import structured_output as so
from agenti_helix.runtime.agent_runtime import StructuredOutputError


def _patch_run_agent(monkeypatch: pytest.MonkeyPatch, behaviours: List[Callable[..., Dict[str, Any]]]) -> List[str]:
    """Replace ``structured_output.run_agent`` with a sequence of behaviours.

    Returns a list that records the prompt_override on each call so tests can
    assert that the repair addendum was injected on attempt 2.
    """
    calls: List[str] = []
    cursor = {"i": 0}

    def fake_run_agent(*, prompt_override: str | None = None, **_kwargs: Any) -> Dict[str, Any]:
        calls.append(prompt_override or "")
        idx = cursor["i"]
        cursor["i"] += 1
        return behaviours[idx](prompt_override=prompt_override, **_kwargs)

    monkeypatch.setattr(so, "run_agent", fake_run_agent)
    return calls


def _ok(**_kwargs: Any) -> Dict[str, Any]:
    return {"verdict": "PASS", "justification": "looks good", "problematic_lines": []}


def _raise_validate(prompt_override: str | None = None, **_kwargs: Any) -> Dict[str, Any]:
    raise StructuredOutputError(
        message="missing field 'verdict'",
        raw_output='{"justification": "broken"}',
        agent_id="judge_v1",
        kind="validate",
    )


def test_first_attempt_success_does_not_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_run_agent(monkeypatch, [_ok])
    out = so.run_agent_structured(agent_id="judge_v1", raw_input={
        "repo_path": "/r", "target_file": "f", "acceptance_criteria": "ok",
        "original_snippet": "", "edited_snippet": "", "language": "python",
        "tool_logs_json": "{}",
    })
    assert out["verdict"] == "PASS"
    assert len(calls) == 1
    # First-attempt prompt includes the schema preamble.
    assert "STRUCTURED OUTPUT" in calls[0]
    assert "REPAIR ATTEMPT" not in calls[0]


def test_repair_attempt_appends_error_and_raw_output(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_run_agent(monkeypatch, [_raise_validate, _ok])
    out = so.run_agent_structured(agent_id="judge_v1", raw_input={
        "repo_path": "/r", "target_file": "f", "acceptance_criteria": "ok",
        "original_snippet": "", "edited_snippet": "", "language": "python",
        "tool_logs_json": "{}",
    })
    assert out["verdict"] == "PASS"
    assert len(calls) == 2
    repair_prompt = calls[1]
    assert "REPAIR ATTEMPT" in repair_prompt
    assert "missing field 'verdict'" in repair_prompt
    assert "broken" in repair_prompt  # raw_output snippet must be echoed back


def _raise_truncated(prompt_override: str | None = None, **_kwargs: Any) -> Dict[str, Any]:
    raise StructuredOutputError(
        message="Model output appears to start a JSON object but never closes it.",
        raw_output='{"verdict": "PASS", "justification": "this got cut off mid',
        agent_id="judge_v1",
        kind="parse",
        truncated=True,
    )


def test_truncation_short_circuits_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the first failure is flagged truncated, the wrapper must NOT retry.

    Rationale: the repair prompt is strictly longer (includes schema + prev
    raw output + error text). Retrying with a longer prompt at the same
    ``max_tokens`` just truncates sooner. We bail out immediately and let the
    caller's outer loop recover (or surface the failure).
    """
    calls = _patch_run_agent(monkeypatch, [_raise_truncated, _ok])
    with pytest.raises(StructuredOutputError) as exc_info:
        so.run_agent_structured(agent_id="judge_v1", raw_input={
            "repo_path": "/r", "target_file": "f", "acceptance_criteria": "ok",
            "original_snippet": "", "edited_snippet": "", "language": "python",
            "tool_logs_json": "{}",
        })
    # Exactly one attempt — the repair retry was skipped.
    assert len(calls) == 1
    assert exc_info.value.truncated is True
    assert exc_info.value.kind == "parse"


def test_truncation_on_last_attempt_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the LAST configured attempt also happens to be truncated, we don't
    re-enter the loop; we just raise (same shape as exhausted repair)."""
    calls = _patch_run_agent(monkeypatch, [_raise_validate, _raise_truncated])
    with pytest.raises(StructuredOutputError) as exc_info:
        so.run_agent_structured(
            agent_id="judge_v1",
            raw_input={
                "repo_path": "/r", "target_file": "f", "acceptance_criteria": "ok",
                "original_snippet": "", "edited_snippet": "", "language": "python",
                "tool_logs_json": "{}",
            },
            max_attempts=2,
        )
    assert len(calls) == 2
    assert exc_info.value.truncated is True


def test_exhausted_attempts_re_raises_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run_agent(monkeypatch, [_raise_validate, _raise_validate])
    with pytest.raises(StructuredOutputError) as exc_info:
        so.run_agent_structured(agent_id="judge_v1", raw_input={
            "repo_path": "/r", "target_file": "f", "acceptance_criteria": "ok",
            "original_snippet": "", "edited_snippet": "", "language": "python",
            "tool_logs_json": "{}",
        })
    assert exc_info.value.kind == "validate"


def test_every_registered_agent_is_structured() -> None:
    """The ``STRUCTURED_AGENT_IDS`` set must cover the entire agent registry.

    Routing every agent through the JSON-mode + repair loop closes the
    over-thinking escape hatch (a tolerant agent could otherwise emit a
    free-form ``<redacted_thinking>`` block and burn the entire token budget
    on prose). Adding a new agent to the registry must automatically opt it
    into structured output, so this is enforced as a registry equivalence.
    """
    from agenti_helix.agents.registry import _AGENTS

    missing = sorted(set(_AGENTS.keys()) - so.STRUCTURED_AGENT_IDS)
    assert not missing, (
        f"Agents missing from STRUCTURED_AGENT_IDS: {missing}. "
        "Make sure structured_output.STRUCTURED_AGENT_IDS is derived from the registry."
    )


@pytest.mark.parametrize("agent_id", [
    "coder_patch_v1",
    "intent_compiler_v1",
    "judge_v1",
    "memory_summarizer_v1",
    "supreme_court_v1",
    "context_librarian_v1",
    "sdet_v1",
    "coder_builder_v1",
    "security_governor_v1",
    "judge_evaluator_v1",
    "doc_fetcher_v1",
    "diff_validator_v1",
    "linter_v1",
    "type_checker_v1",
])
def test_is_structured_agent_membership(agent_id: str) -> None:
    """All registered agents must opt into the structured-output wrapper."""
    assert so.is_structured_agent(agent_id) is True


def test_chain_runtime_routes_every_agent_through_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_chain`` must call ``run_agent_structured`` for every agent.

    Previously, agents like ``doc_fetcher_v1`` fell through to plain
    ``run_agent``; that's the path that allowed SDET / coder_builder to
    over-think. We assert the universal-routing contract here so a future
    refactor can't accidentally re-introduce the fast-lane.
    """
    from agenti_helix.runtime import chain_runtime

    routed_via_structured: list[str] = []
    routed_via_plain: list[str] = []

    def fake_structured(*, agent_id: str, **_kwargs: Any) -> Dict[str, Any]:
        routed_via_structured.append(agent_id)
        return {"ok": True}

    def fake_plain(*, agent_id: str, **_kwargs: Any) -> Dict[str, Any]:
        routed_via_plain.append(agent_id)
        return {"ok": True}

    monkeypatch.setattr(chain_runtime, "run_agent_structured", fake_structured)
    monkeypatch.setattr(chain_runtime, "run_agent", fake_plain)

    chain = {
        "steps": [
            {"type": "agent", "id": "a", "output_key": "o1", "agent_id": "judge_v1", "input_bindings": {}},
            {"type": "agent", "id": "b", "output_key": "o2", "agent_id": "doc_fetcher_v1", "input_bindings": {}},
            {"type": "agent", "id": "c", "output_key": "o3", "agent_id": "coder_patch_v1", "input_bindings": {}},
            {"type": "agent", "id": "d", "output_key": "o4", "agent_id": "sdet_v1", "input_bindings": {}},
            {"type": "agent", "id": "e", "output_key": "o5", "agent_id": "security_governor_v1", "input_bindings": {}},
        ]
    }
    chain_runtime.run_chain(
        chain_spec=chain,
        initial_context={},
        cancel_token=None,
        run_id="t",
        hypothesis_id="h",
        location_prefix="test",
    )
    assert sorted(routed_via_structured) == [
        "coder_patch_v1",
        "doc_fetcher_v1",
        "judge_v1",
        "sdet_v1",
        "security_governor_v1",
    ]
    assert routed_via_plain == [], (
        "No agent should fall through to plain run_agent — every agent must go through "
        "the JSON-mode + repair wrapper."
    )


def test_skipped_agent_emits_llm_trace_for_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``skip_if_nonempty_key`` fires, no inference runs — but we still log one ``llm_trace``."""
    from agenti_helix.runtime import chain_runtime

    log_calls: list[dict[str, Any]] = []

    def fake_log(**kwargs: Any) -> None:
        log_calls.append(kwargs)

    def never_structured(**_kwargs: Any) -> Dict[str, Any]:
        raise AssertionError("skipped agent must not call run_agent_structured")

    monkeypatch.setattr(chain_runtime, "log_event", fake_log)
    monkeypatch.setattr(chain_runtime, "run_agent_structured", never_structured)
    monkeypatch.setenv("AGENTI_HELIX_LLM_TRACE", "1")

    chain_runtime.run_chain(
        chain_spec={
            "steps": [
                {
                    "type": "agent",
                    "id": "judge_evaluator",
                    "output_key": "je_out",
                    "agent_id": "judge_evaluator_v1",
                    "skip_if_nonempty_key": "judge_response",
                    "input_bindings": {},
                }
            ]
        },
        initial_context={"judge_response": {"verdict": "FAIL"}},
        cancel_token=None,
        run_id="run",
        hypothesis_id="hyp",
        location_prefix="test_prefix",
    )

    traces = [c for c in log_calls if isinstance(c.get("data"), dict) and c["data"].get("kind") == "llm_trace"]
    assert len(traces) == 1
    d = traces[0]["data"]
    assert d.get("skipped") is True
    assert d.get("agent_id") == "judge_evaluator_v1"


def test_skipped_agent_no_synthetic_trace_when_llm_trace_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from agenti_helix.runtime import chain_runtime

    log_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(chain_runtime, "log_event", lambda **kwargs: log_calls.append(kwargs))
    monkeypatch.setattr(chain_runtime, "run_agent_structured", lambda **_k: {"ok": True})
    monkeypatch.setenv("AGENTI_HELIX_LLM_TRACE", "0")

    chain_runtime.run_chain(
        chain_spec={
            "steps": [
                {
                    "type": "agent",
                    "id": "judge_evaluator",
                    "output_key": "je_out",
                    "agent_id": "judge_evaluator_v1",
                    "skip_if_nonempty_key": "judge_response",
                    "input_bindings": {},
                }
            ]
        },
        initial_context={"judge_response": {"verdict": "FAIL"}},
        cancel_token=None,
        run_id="run",
        hypothesis_id="hyp",
        location_prefix="test_prefix",
    )

    traces = [c for c in log_calls if isinstance(c.get("data"), dict) and c["data"].get("kind") == "llm_trace"]
    assert traces == []
