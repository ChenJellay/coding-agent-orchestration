from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


def _default_log_path() -> Path:
    repo_root = Path(os.environ.get("AGENTI_HELIX_REPO_ROOT", str(Path(".").resolve()))).resolve()
    return repo_root / ".agenti_helix" / "logs" / "events.jsonl"


_LOG_PATH = Path(os.environ.get("AGENTI_HELIX_LOG_PATH", str(_default_log_path())))
_SESSION_ID = os.environ.get("AGENTI_HELIX_SESSION_ID", "dev")


def log_event(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    dag_id: Optional[str] = None,
) -> None:
    """Append one JSON event to the events log.

    Honors ``AGENTI_HELIX_DISABLE_LOGGING`` and ``AGENTI_HELIX_LOG_PATH`` /
    ``AGENTI_HELIX_REPO_ROOT`` environment variables. Best-effort: silently
    swallows OS errors so logging never breaks a running pipeline.
    """
    if os.environ.get("AGENTI_HELIX_DISABLE_LOGGING", "").strip().lower() in {"1", "true", "yes"}:
        # #region agent log
        try:
            _p = Path("/Users/jerrychen/startup/coding-agent-orchestration/.cursor/debug-9274ce.log")
            _kind = (data or {}).get("kind") if isinstance(data, dict) else None
            _line = json.dumps(
                {
                    "sessionId": "9274ce",
                    "timestamp": int(time.time() * 1000),
                    "location": "debug_log.py:log_event",
                    "message": message,
                    "hypothesisId": "H3",
                    "data": {
                        "log_path": str(_LOG_PATH),
                        "kind": _kind,
                        "skip_reason": "AGENTI_HELIX_DISABLE_LOGGING",
                        "will_append": False,
                    },
                    "runId": run_id,
                }
            ) + "\n"
            _p.parent.mkdir(parents=True, exist_ok=True)
            with _p.open("a", encoding="utf-8") as _f:
                _f.write(_line)
        except Exception:
            pass
        # #endregion
        return

    # Logging policy:
    # - Default: persist system lifecycle events + llm_trace into events.jsonl (needed for evals/audits).
    # - Optional: set AGENTI_HELIX_LOG_LLM_ONLY=true to persist only llm_trace lines.
    kind = (data or {}).get("kind") if isinstance(data, dict) else None
    llm_only = os.environ.get("AGENTI_HELIX_LOG_LLM_ONLY", "").strip().lower() in {"1", "true", "yes"}
    should_write = (kind == "llm_trace") or (not llm_only)
    # #region agent log
    try:
        _p = Path("/Users/jerrychen/startup/coding-agent-orchestration/.cursor/debug-9274ce.log")
        _skip = None if should_write else "llm_only"
        _line = json.dumps(
            {
                "sessionId": "9274ce",
                "timestamp": int(time.time() * 1000),
                "location": "debug_log.py:log_event",
                "message": message,
                "hypothesisId": "H1",
                "data": {
                    "log_path": str(_LOG_PATH),
                    "kind": kind,
                    "skip_reason": _skip,
                    "will_append": _skip is None,
                },
                "runId": run_id,
            }
        ) + "\n"
        _p.parent.mkdir(parents=True, exist_ok=True)
        with _p.open("a", encoding="utf-8") as _f:
            _f.write(_line)
    except Exception:
        pass
    # #endregion
    if not should_write:
        return

    payload: Dict[str, Any] = {
        "sessionId": _SESSION_ID,
        "id": f"log_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": data or {},
        "runId": run_id,
        "hypothesisId": hypothesis_id,
    }
    if trace_id is not None:
        payload["traceId"] = trace_id
    if dag_id is not None:
        payload["dagId"] = dag_id

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError:
        pass
