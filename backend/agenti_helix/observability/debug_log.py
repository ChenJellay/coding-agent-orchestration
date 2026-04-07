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
    if os.environ.get("AGENTI_HELIX_DISABLE_LOGGING", "").strip().lower() in {"1", "true", "yes"}:
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

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

