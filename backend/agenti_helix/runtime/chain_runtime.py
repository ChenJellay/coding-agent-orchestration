from __future__ import annotations

import copy
import os
from typing import Any, Dict, Optional

from agenti_helix.api.job_registry import TaskCancelledError
from agenti_helix.observability.debug_log import log_event
from agenti_helix.runtime.agent_runtime import run_agent
from agenti_helix.runtime.structured_output import is_structured_agent, run_agent_structured
from agenti_helix.runtime.tools import TOOL_REGISTRY


def _llm_trace_logs_enabled() -> bool:
    """Match ``agent_runtime`` — skip-trace rows only emit when LLM tracing is on."""
    v = os.environ.get("AGENTI_HELIX_LLM_TRACE", "1").strip().lower()
    return v not in {"0", "false", "no", "off"}


def _is_cancelled(cancel_token: Any | None) -> bool:
    if cancel_token is None:
        return False
    is_cancelled_fn = getattr(cancel_token, "is_cancelled", None)
    if callable(is_cancelled_fn):
        try:
            return bool(is_cancelled_fn())
        except Exception:
            return False
    return False


def _resolve_binding(value: Any, ctx: Dict[str, Any]) -> Any:
    """
    Resolve a DSL binding into a concrete runtime value.

    Supported forms:
    - {"$ref": "some.key"} -> ctx["some"]["key"]...
    - any other JSON-safe value -> treated as literal
    """
    if isinstance(value, dict) and set(value.keys()) == {"$ref"}:
        ref = value["$ref"]
        cur: Any = ctx
        for part in str(ref).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise KeyError(f"Missing context ref {ref!r}")
        return cur

    if isinstance(value, dict):
        # Allow nested objects with refs inside.
        return {k: _resolve_binding(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_binding(v, ctx) for v in value]
    return value


def run_chain(
    *,
    chain_spec: Dict[str, Any],
    initial_context: Dict[str, Any],
    cancel_token: Any | None = None,
    run_id: str,
    hypothesis_id: str,
    location_prefix: str,
) -> Dict[str, Any]:
    """
    Execute a chain of `tool` and `agent` steps with a shared mutable context.

    Chain DSL (minimal):
      {
        "steps": [
          {
            "type": "tool" | "agent",
            "id": "...",
            "output_key": "some_ctx_key",
            "tool_name": "...",           # when type=tool
            "agent_id": "...",            # when type=agent
            "input_bindings": { ... }     # optional; supports {"$ref":"..."}
            "runtime": { ... }            # optional for type=agent
          }
        ]
      }
    """
    ctx: Dict[str, Any] = copy.deepcopy(initial_context)

    steps = chain_spec.get("steps")
    if not isinstance(steps, list):
        raise ValueError("chain_spec must contain a list field `steps`")

    for idx, step in enumerate(steps):
        if _is_cancelled(cancel_token):
            raise TaskCancelledError("Chain execution cancelled")

        if not isinstance(step, dict):
            raise ValueError(f"Invalid step at index={idx}: expected object")

        step_type = step.get("type")
        output_key = step.get("output_key")
        step_id = step.get("id") or f"step_{idx}"
        input_bindings = step.get("input_bindings") or {}

        if not isinstance(step_type, str) or step_type not in {"tool", "agent"}:
            raise ValueError(f"Invalid step.type at index={idx}: {step_type!r}")
        if not isinstance(output_key, str) or not output_key:
            raise ValueError(f"Invalid step.output_key at index={idx}")
        if not isinstance(input_bindings, dict):
            raise ValueError(f"Invalid step.input_bindings at index={idx}")

        skip_key = step.get("skip_if_nonempty_key")
        if isinstance(skip_key, str) and skip_key.strip() and ctx.get(skip_key):
            # Skipped agents produce no ``run_agent`` call, so the LLM I/O panel would
            # show a gap (e.g. diff_validator BLOCK → judge_evaluator never runs). Emit a
            # synthetic ``llm_trace`` so operators see the agent id and why it was skipped.
            if step_type == "agent" and _llm_trace_logs_enabled():
                agent_id = step.get("agent_id")
                if isinstance(agent_id, str) and agent_id:
                    tid = ctx.get("trace_id") if isinstance(ctx.get("trace_id"), str) else None
                    did = ctx.get("dag_id") if isinstance(ctx.get("dag_id"), str) else None
                    log_event(
                        run_id=run_id,
                        hypothesis_id=hypothesis_id,
                        location=f"{location_prefix}:{step_id}",
                        message="LLM inference (skipped)",
                        data={
                            "kind": "llm_trace",
                            "agent_id": agent_id,
                            "skipped": True,
                            "skip_key": skip_key,
                            "prompt": (
                                f"This agent step did not run because `{skip_key}` was already set in the "
                                "chain context. Typical case: `diff_validator_v1` returned verdict BLOCK, "
                                "so `apply_diff_validator_gate` wrote `judge_response` and downstream judge "
                                "agents (`judge_evaluator_v1`, etc.) are skipped by design."
                            ),
                            "raw_output": "",
                            "parsed_output_json": "null",
                        },
                        trace_id=tid,
                        dag_id=did,
                    )
            continue

        bound_inputs = _resolve_binding(input_bindings, ctx)

        if step_type == "tool":
            tool_name = step.get("tool_name")
            if not isinstance(tool_name, str) or tool_name not in TOOL_REGISTRY:
                raise ValueError(f"Unknown tool_name={tool_name!r} for step_id={step_id!r}")
            tool_fn = TOOL_REGISTRY[tool_name]

            log_event(
                run_id=run_id,
                hypothesis_id=hypothesis_id,
                location=f"{location_prefix}:{step_id}",
                message="Tool step started",
                data={"tool_name": tool_name, "step_index": idx},
            )
            out = tool_fn(**bound_inputs)

        else:
            agent_id = step.get("agent_id")
            if not isinstance(agent_id, str):
                raise ValueError(f"Missing/invalid agent_id for step_id={step_id!r}")

            runtime = step.get("runtime")
            if runtime is not None and not isinstance(runtime, dict):
                raise ValueError(f"Invalid runtime for step_id={step_id!r}: expected object")

            log_event(
                run_id=run_id,
                hypothesis_id=hypothesis_id,
                location=f"{location_prefix}:{step_id}",
                message="Agent step started",
                data={"agent_id": agent_id, "step_index": idx},
            )
            observe: Dict[str, Any] = {
                "run_id": run_id,
                "hypothesis_id": hypothesis_id,
                "location": f"{location_prefix}:{step_id}",
            }
            for key in ("trace_id", "dag_id"):
                v = ctx.get(key)
                if isinstance(v, str) and v:
                    observe[key] = v
            agent_caller = run_agent_structured if is_structured_agent(agent_id) else run_agent
            out = agent_caller(
                agent_id=agent_id,
                raw_input=bound_inputs,
                runtime=runtime,
                cancel_token=cancel_token,
                observe=observe,
            )

        ctx[output_key] = out

        log_event(
            run_id=run_id,
            hypothesis_id=hypothesis_id,
            location=f"{location_prefix}:{step_id}",
            message="Step finished",
            data={"step_type": step_type, "output_key": output_key},
        )

    return ctx

