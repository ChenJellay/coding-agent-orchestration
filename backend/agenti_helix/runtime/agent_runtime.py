from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from pydantic import BaseModel

from agenti_helix.agents.registry import get_agent
from agenti_helix.api.job_registry import TaskCancelledError
from agenti_helix.observability.debug_log import log_event, write_cursor_debug_ndjson
from agenti_helix.runtime.inference_backends import get_default_inference_backend
from agenti_helix.runtime.json_utils import extract_first_json_object, try_fallback_snippet_judge_dict

import re

_THINK_EXTRACT_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _extract_thinking(raw: str) -> str | None:
    """Pull out the concatenated contents of all ``<think>`` blocks, or None."""
    matches = _THINK_EXTRACT_RE.findall(raw)
    if not matches:
        return None
    return "\n---\n".join(m.strip() for m in matches if m.strip()) or None


def _llm_trace_enabled() -> bool:
    v = os.environ.get("AGENTI_HELIX_LLM_TRACE", "1").strip().lower()
    return v not in {"0", "false", "no", "off"}


def _llm_trace_max_chars() -> int:
    raw = os.environ.get("AGENTI_HELIX_LLM_TRACE_MAX_CHARS", "250000").strip()
    try:
        return max(4096, int(raw))
    except ValueError:
        return 250000


def _clip_trace_text(text: str) -> tuple[str, bool]:
    lim = _llm_trace_max_chars()
    if len(text) <= lim:
        return text, False
    return text[:lim] + "\n\n… [truncated by AGENTI_HELIX_LLM_TRACE_MAX_CHARS]", True


def _is_cancelled(cancel_token: Any | None) -> bool:
    if cancel_token is None:
        return False
    fn = getattr(cancel_token, "is_cancelled", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            return False
    return False


def run_agent(
    *,
    agent_id: str,
    raw_input: Dict[str, Any],
    runtime: Optional[dict[str, Any]] = None,
    cancel_token: Any | None = None,
    observe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Render `agent_id` prompt + run inference + parse/validate JSON output.

    Returns a JSON-serializable dict (the validated output model dump).
    Raises `TaskCancelledError` if `cancel_token` is set before or after inference.
    """
    if _is_cancelled(cancel_token):
        raise TaskCancelledError("Agent run cancelled before inference")

    agent = get_agent(agent_id)
    prompt = agent.render(raw_input)

    runtime_cfg = runtime or {}
    # Omit max_tokens for local MLX: backend applies a large ceiling (no practical cap).
    if "max_tokens" in runtime_cfg and runtime_cfg["max_tokens"] is not None:
        max_tokens = int(runtime_cfg["max_tokens"])
    else:
        max_tokens = None
    temperature = float(runtime_cfg.get("temperature") or 0.0)

    # Build backend config: merge agent-level hint (backend_type) with any
    # runtime overrides supplied by the chain DSL, letting the chain win.
    inference_backend_cfg: dict[str, Any] = {}
    if agent.backend_type:
        inference_backend_cfg["backend_type"] = agent.backend_type
    chain_backend_cfg = runtime_cfg.get("inference_backend") or {}
    if isinstance(chain_backend_cfg, dict):
        inference_backend_cfg.update(chain_backend_cfg)
    # If the chain DSL passed backend_type at the top level, that also wins.
    if "backend_type" in runtime_cfg:
        inference_backend_cfg["backend_type"] = runtime_cfg["backend_type"]

    obs = observe or {}
    run_id_log = str(obs.get("run_id") or "_llm")
    hyp_log = str(obs.get("hypothesis_id") or agent_id)
    loc_log = str(obs.get("location") or "agent_runtime:run_agent")
    trace_id = obs.get("trace_id") if isinstance(obs.get("trace_id"), str) else None
    dag_id = obs.get("dag_id") if isinstance(obs.get("dag_id"), str) else None

    backend = get_default_inference_backend(inference_backend_cfg)

    # Optional: MLX only — on_progress fires every AGENTI_HELIX_MLX_PROGRESS_INTERVAL
    # tokens (default 0 = disabled) and writes kind=llm_progress for live UI.
    def _on_progress(token_count: int, tps: float, snippet: str) -> None:
        if not _llm_trace_enabled():
            return
        log_event(
            run_id=run_id_log,
            hypothesis_id=hyp_log,
            location=loc_log,
            message="LLM inference in progress",
            data={
                "kind": "llm_progress",
                "agent_id": agent_id,
                "token_count": token_count,
                "tokens_per_second": round(tps, 1),
                "partial_tail": snippet,
            },
            trace_id=trace_id,
            dag_id=dag_id,
        )

    if _llm_trace_enabled():
        log_event(
            run_id=run_id_log,
            hypothesis_id=hyp_log,
            location=loc_log,
            message="LLM inference started",
            data={
                "kind": "llm_start",
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "backend_type": str(
                    inference_backend_cfg.get("backend_type")
                    or os.environ.get("AGENTI_HELIX_BACKEND_TYPE")
                    or "mlx_local"
                ),
            },
            trace_id=trace_id,
            dag_id=dag_id,
        )

    raw = backend.generate(prompt, max_tokens=max_tokens, temperature=temperature, on_progress=_on_progress)

    if _is_cancelled(cancel_token):
        raise TaskCancelledError("Agent run cancelled after inference")

    # Extract thinking content for trace logging (before JSON extraction strips it).
    thinking_content = _extract_thinking(raw)

    def _trace_payload(**extra: Any) -> Dict[str, Any]:
        p, p_trunc = _clip_trace_text(prompt)
        r_out, r_trunc = _clip_trace_text(raw)
        payload: Dict[str, Any] = {
            "kind": "llm_trace",
            "agent_id": agent_id,
            "prompt": p,
            "prompt_truncated": p_trunc,
            "raw_output": r_out,
            "raw_output_truncated": r_trunc,
        }
        if thinking_content:
            t, t_trunc = _clip_trace_text(thinking_content)
            payload["thinking"] = t
            payload["thinking_truncated"] = t_trunc
        payload.update(extra)
        return payload

    data: Dict[str, Any] | None = None
    parse_exc: Exception | None = None
    try:
        data = extract_first_json_object(raw)
    except Exception as exc:
        parse_exc = exc
        if agent_id == "judge_v1":
            fb = try_fallback_snippet_judge_dict(raw)
            if fb is not None:
                data = fb
                parse_exc = None
                # #region agent log
                write_cursor_debug_ndjson(
                    location="agent_runtime.py:run_agent",
                    message="judge_v1_json_fallback_used",
                    hypothesis_id="H3",
                    data={"verdict": fb.get("verdict"), "agent_id": agent_id},
                    run_id=run_id_log,
                )
                # #endregion

    if parse_exc is not None:
        # #region agent log
        write_cursor_debug_ndjson(
            location="agent_runtime.py:run_agent",
            message="json_extract_failed",
            hypothesis_id="H4",
            data={
                "agent_id": agent_id,
                "error": str(parse_exc)[:500],
            },
            run_id=run_id_log,
        )
        # #endregion
        if _llm_trace_enabled():
            log_event(
                run_id=run_id_log,
                hypothesis_id=hyp_log,
                location=loc_log,
                message="LLM inference (parse/validation failed)",
                data=_trace_payload(
                    error=str(parse_exc),
                    parsed_output=None,
                ),
                trace_id=trace_id,
                dag_id=dag_id,
            )
        raise parse_exc

    assert data is not None

    try:
        output_model: type[BaseModel] = agent.output_model
        typed = output_model.model_validate(data)
        result = typed.model_dump()
    except Exception as exc:
        # #region agent log
        write_cursor_debug_ndjson(
            location="agent_runtime.py:run_agent",
            message="pydantic_validate_failed",
            hypothesis_id="H5",
            data={"agent_id": agent_id, "error": str(exc)[:500]},
            run_id=run_id_log,
        )
        # #endregion
        if _llm_trace_enabled():
            log_event(
                run_id=run_id_log,
                hypothesis_id=hyp_log,
                location=loc_log,
                message="LLM inference (parse/validation failed)",
                data=_trace_payload(
                    error=str(exc),
                    parsed_output=None,
                ),
                trace_id=trace_id,
                dag_id=dag_id,
            )
        raise

    if _llm_trace_enabled():
        parsed_json: Optional[str] = None
        parsed_truncated = False
        try:
            dumped = json.dumps(result, ensure_ascii=False, indent=2)
            parsed_json, parsed_truncated = _clip_trace_text(dumped)
        except (TypeError, ValueError):
            parsed_json = str(result)
            parsed_json, parsed_truncated = _clip_trace_text(parsed_json)

        log_event(
            run_id=run_id_log,
            hypothesis_id=hyp_log,
            location=loc_log,
            message="LLM inference",
            data=_trace_payload(
                parsed_output_json=parsed_json,
                parsed_output_truncated=parsed_truncated,
            ),
            trace_id=trace_id,
            dag_id=dag_id,
        )
    return result

