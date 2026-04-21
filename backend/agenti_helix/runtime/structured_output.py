"""
JSON-mode + repair wrapper. **Every** registered agent is routed through this
path; there is no longer a "tolerant prose" fast lane.

Local MLX models do not support real schema-constrained generation, so the
best we can do is:

1. Inject the agent's Pydantic JSON schema into the system prompt up front.
2. On parse/validation failure, retry once with a "repair" prompt that
   includes the failed raw output, the validator error, and the schema.

OpenAI-class backends could in principle use ``response_format=json_schema``
for true constrained decoding; that's a future enhancement gated by
``backend_type``. The repair loop is the floor.

History: we used to opt agents in one-by-one (``coder_patch_v1``,
``intent_compiler_v1``, ``judge_v1``). The remaining "tolerant" agents
(SDET, builder, governor, librarian, doc_fetcher, …) all kept slipping
into over-thinking — they emit a free-form ``<redacted_thinking>`` block
which the parser strips but which still consumes minutes of wall-clock.
Routing every agent through the schema preamble closed that escape hatch:
the model sees a structural reminder + an explicit "no thinking blocks"
directive on every call, and any malformed output triggers exactly one
deterministic repair attempt.
"""
from __future__ import annotations

import json
from typing import Any, Dict, FrozenSet, Optional

from agenti_helix.agents.registry import _AGENTS, get_agent
from agenti_helix.observability.debug_log import log_event
from agenti_helix.runtime.agent_runtime import StructuredOutputError, run_agent

# Derived from the registry so that *adding a new agent* automatically opts
# it in — there is no manual list to forget. ``frozenset`` keeps the public
# API ``STRUCTURED_AGENT_IDS`` immutable for callers.
STRUCTURED_AGENT_IDS: FrozenSet[str] = frozenset(_AGENTS.keys())

_DEFAULT_MAX_ATTEMPTS = 2
_REPAIR_RAW_CLIP = 4000  # chars of failed raw output to feed back


def _schema_preamble(agent_id: str) -> str:
    """JSON-schema reminder appended to the first-attempt prompt.

    Prompts already include human-written schema docs, but local 4-bit
    models drift; pinning the Pydantic-derived schema directly into the
    prompt cuts down on field renames / wrapping objects.

    The anti-thinking directive covers both ``<think>`` (Qwen3 / DeepSeek-R1
    convention) and ``<redacted_thinking>`` (this codebase's stripped-by-the-
    parser convention) so that long-form agents can't sneak a multi-paragraph
    deliberation block in front of the JSON. Any reasoning the model needs to
    do should land inside the schema's reasoning field (``testing_strategy``,
    ``implementation_logic``, ``audit_reasoning``, …), where it counts toward
    the per-agent token budget.
    """
    schema = get_agent(agent_id).output_model.model_json_schema()
    return (
        "\n\n---\n\n"
        "STRUCTURED OUTPUT — your response MUST be a single JSON object that satisfies this schema:\n"
        "```json\n"
        f"{json.dumps(schema, indent=2)}\n"
        "```\n"
        "Hard constraints:\n"
        "- No prose before or after the JSON.\n"
        "- No markdown fences around the JSON object itself.\n"
        "- No `<think>` block. No `<redacted_thinking>` block. No hidden reasoning.\n"
        "- Put any reasoning **inside** the JSON, in the schema's reasoning field "
        "(e.g. `testing_strategy`, `implementation_logic`, `audit_reasoning`, "
        "`evaluation_reasoning`, `search_strategy`).\n"
    )


def _repair_addendum(*, prev_raw: str, error: str, agent_id: str) -> str:
    """Compose the repair-prompt suffix appended to the original prompt."""
    schema = get_agent(agent_id).output_model.model_json_schema()
    clipped = prev_raw if len(prev_raw) <= _REPAIR_RAW_CLIP else (prev_raw[:_REPAIR_RAW_CLIP] + " …[truncated]")
    return (
        "\n\n---\n\n"
        "REPAIR ATTEMPT — your previous response did not conform to the required JSON schema.\n"
        f"Validator error: {error}\n\n"
        "Your previous output was:\n"
        "```\n"
        f"{clipped}\n"
        "```\n\n"
        "Required JSON schema (Pydantic-derived):\n"
        "```json\n"
        f"{json.dumps(schema, indent=2)}\n"
        "```\n\n"
        "Return ONLY a single valid JSON object that satisfies the schema.\n"
        "No prose, no markdown fences, no <think> blocks, no commentary.\n"
    )


def is_structured_agent(agent_id: str) -> bool:
    return agent_id in STRUCTURED_AGENT_IDS


def run_agent_structured(
    *,
    agent_id: str,
    raw_input: Dict[str, Any],
    runtime: Optional[dict[str, Any]] = None,
    cancel_token: Any | None = None,
    observe: Optional[Dict[str, Any]] = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> Dict[str, Any]:
    """``run_agent`` + JSON-repair retry on parse / validation failure.

    The first attempt uses the agent's normal rendered prompt. On a
    ``StructuredOutputError``, subsequent attempts append a repair addendum
    that includes the failed raw output, the validator error, and the JSON
    schema, then re-renders by overriding the prompt directly (so we don't
    require every prompt template to know about a magic addendum field).

    **Truncation short-circuit**: when the first failure is flagged as
    ``truncated`` by ``run_agent`` (model hit ``max_tokens`` mid-JSON), we
    skip the repair retry. Appending a longer repair addendum would only
    make the next attempt truncate sooner at the same cap — wasting budget
    and, with local quant backends, wall-clock time.
    """
    agent = get_agent(agent_id)
    base_prompt = agent.render(raw_input) + _schema_preamble(agent_id)

    last_exc: StructuredOutputError | None = None

    for attempt in range(1, max_attempts + 1):
        prompt_override: Optional[str] = base_prompt
        if attempt > 1 and last_exc is not None:
            prompt_override = base_prompt + _repair_addendum(
                prev_raw=last_exc.raw_output,
                error=str(last_exc),
                agent_id=agent_id,
            )

        try:
            return run_agent(
                agent_id=agent_id,
                raw_input=raw_input,
                runtime=runtime,
                cancel_token=cancel_token,
                observe=observe,
                prompt_override=prompt_override,
            )
        except StructuredOutputError as exc:
            last_exc = exc
            obs = observe or {}
            is_last_attempt = attempt >= max_attempts
            skip_repair = bool(exc.truncated) and not is_last_attempt
            if skip_repair:
                message = (
                    f"Structured output hit max_tokens (attempt {attempt}/{max_attempts}); "
                    "skipping repair — a longer prompt would only truncate sooner"
                )
            elif not is_last_attempt:
                message = f"Structured output failed (attempt {attempt}/{max_attempts}); will repair"
            else:
                message = "Structured output exhausted repair attempts"
            log_event(
                run_id=str(obs.get("run_id") or "_llm"),
                hypothesis_id=str(obs.get("hypothesis_id") or agent_id),
                location=str(obs.get("location") or "agent_runtime:run_agent_structured"),
                message=message,
                data={
                    "kind": "structured_repair",
                    "agent_id": agent_id,
                    "attempt": attempt,
                    "failure_kind": exc.kind,
                    "truncated": bool(exc.truncated),
                    "error": str(exc),
                },
                trace_id=obs.get("trace_id") if isinstance(obs.get("trace_id"), str) else None,
                dag_id=obs.get("dag_id") if isinstance(obs.get("dag_id"), str) else None,
            )
            if skip_repair:
                break

    assert last_exc is not None
    raise last_exc
