"""§4.2 sandbox gate — full Docker isolation is planned; this module documents the hook."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from agenti_helix.observability.debug_log import log_event


def _sandbox_enabled() -> bool:
    return os.environ.get("AGENTI_HELIX_SANDBOX_ENABLED", "").lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class SandboxManager:
    """Placeholder for per-task containers (see ``deployment-gaps.md`` §4.2)."""

    @staticmethod
    def is_enabled() -> bool:
        return _sandbox_enabled()

    @staticmethod
    def describe() -> str:
        if not _sandbox_enabled():
            return "disabled"
        return "requested (Docker executor not wired — runs still use host working tree)"


def log_sandbox_status_for_task(task_id: str, *, trace_id: Optional[str] = None, dag_id: Optional[str] = None) -> None:
    """Emit one structured event when sandbox is enabled so operators see the gap."""
    if not _sandbox_enabled():
        return
    log_event(
        run_id=task_id,
        hypothesis_id="sandbox",
        location="agenti_helix/sandbox/manager.py:log_sandbox_status_for_task",
        message=SandboxManager.describe(),
        data={"AGENTI_HELIX_SANDBOX_ENABLED": True, "note": "Implement EphemeralSandbox + docker SDK before relying on isolation."},
        trace_id=trace_id,
        dag_id=dag_id,
    )
