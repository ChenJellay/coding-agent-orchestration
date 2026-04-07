from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from agenti_helix.api.paths import PATHS


def _context_dir() -> Path:
    return PATHS.agenti_root / "task_context"


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    doc_url: Optional[str] = None
    notes: Optional[str] = None


def _context_path(task_id: str) -> Path:
    # Task ids are generated deterministically but may contain ':'.
    # Use a stable filename with JSON escaping handled by the filesystem.
    safe_id = task_id.replace("/", "_")
    return _context_dir() / f"{safe_id}.json"


def save_task_context(*, task_id: str, doc_url: Optional[str], notes: Optional[str]) -> TaskContext:
    ctx = TaskContext(task_id=task_id, doc_url=doc_url, notes=notes)
    _context_dir().mkdir(parents=True, exist_ok=True)
    _context_path(task_id).write_text(
        json.dumps({"task_id": task_id, "doc_url": doc_url, "notes": notes}, indent=2),
        encoding="utf-8",
    )
    return ctx


def load_task_context(task_id: str) -> Optional[TaskContext]:
    path = _context_path(task_id)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TaskContext(
        task_id=str(raw.get("task_id", task_id)),
        doc_url=raw.get("doc_url"),
        notes=raw.get("notes"),
    )


def render_task_context_feedback(ctx: Optional[TaskContext]) -> str:
    """
    Convert stored context into a short feedback string suitable for appending
    to the coder/judge intent.
    """
    if not ctx:
        return ""
    parts = []
    if ctx.doc_url:
        parts.append(f"Doc link: {ctx.doc_url}")
    if ctx.notes:
        parts.append(f"Notes: {ctx.notes}")
    return parts and ("\n".join(parts)).strip() or ""

