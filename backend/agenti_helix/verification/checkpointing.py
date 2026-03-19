from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass
class EditTaskSpec:
    """Specification for a single edit task operated on by the verification loop."""

    task_id: str
    intent: str
    target_file: str
    acceptance_criteria: str
    repo_path: str


@dataclass
class Checkpoint:
    """Captures pre- and post-state around a single edit attempt."""

    checkpoint_id: str
    task_id: str
    status: VerificationStatus
    pre_state_ref: str
    post_state_ref: Optional[str] = None
    diff: Optional[str] = None
    tool_logs: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def _checkpoint_dir() -> Path:
    root = Path(".").resolve()
    return root / ".agenti_helix" / "checkpoints"


def _ensure_checkpoint_dir() -> Path:
    cdir = _checkpoint_dir()
    os.makedirs(cdir, exist_ok=True)
    return cdir


def _checkpoint_path(checkpoint_id: str) -> Path:
    return _checkpoint_dir() / f"{checkpoint_id}.json"


def save_checkpoint(checkpoint: Checkpoint) -> None:
    """Persist a checkpoint to disk as JSON."""
    _ensure_checkpoint_dir()
    checkpoint.updated_at = time.time()
    path = _checkpoint_path(checkpoint.checkpoint_id)
    data = asdict(checkpoint)
    data["status"] = checkpoint.status.value
    path.write_text(json.dumps(data, indent=2))


def load_checkpoint(checkpoint_id: str) -> Checkpoint:
    """Load a checkpoint from disk."""
    path = _checkpoint_path(checkpoint_id)
    raw = json.loads(path.read_text())
    raw["status"] = VerificationStatus(raw["status"])
    return Checkpoint(**raw)


def create_pre_checkpoint(task: EditTaskSpec, pre_state_ref: str) -> Checkpoint:
    """Create and persist a new pre-state checkpoint for a task."""
    checkpoint = Checkpoint(
        checkpoint_id=str(uuid.uuid4()),
        task_id=task.task_id,
        status=VerificationStatus.PENDING,
        pre_state_ref=pre_state_ref,
    )
    save_checkpoint(checkpoint)
    return checkpoint


def record_post_state(
    checkpoint: Checkpoint,
    *,
    post_state_ref: str,
    diff: Optional[str],
    tool_logs: Optional[Dict[str, Any]] = None,
    status: VerificationStatus,
) -> Checkpoint:
    """Update a checkpoint with post-state information and persist it."""
    checkpoint.post_state_ref = post_state_ref
    checkpoint.diff = diff
    checkpoint.tool_logs = tool_logs or {}
    checkpoint.status = status
    save_checkpoint(checkpoint)
    return checkpoint


def list_checkpoints_for_task(task_id: str) -> List[Checkpoint]:
    """Return all checkpoints for a given task id."""
    cdir = _checkpoint_dir()
    if not cdir.exists():
        return []
    checkpoints: List[Checkpoint] = []
    for path in cdir.glob("*.json"):
        raw = json.loads(path.read_text())
        if raw.get("task_id") == task_id:
            raw["status"] = VerificationStatus(raw["status"])
            checkpoints.append(Checkpoint(**raw))
    return checkpoints


def snapshot_file(path: Path) -> str:
    """
    Take a simple snapshot of a file by reading its contents.

    Returns the file contents which can be used as a pre/post state reference.
    """
    return path.read_text()


def restore_file_from_snapshot(path: Path, snapshot: str) -> None:
    """Restore a file to a previous snapshot."""
    path.write_text(snapshot)


def rollback_to_checkpoint(
    task: EditTaskSpec,
    checkpoint: Checkpoint,
    *,
    original_content: Optional[str] = None,
) -> None:
    """
    Roll back the target file to the pre-state for this checkpoint.

    For this demo we support a simple file-content rollback:
    - If original_content is provided, write it back.
    - Otherwise, interpret pre_state_ref as file content and restore it.
    """
    target_path = Path(task.repo_path).resolve() / task.target_file
    if original_content is not None:
        restore_file_from_snapshot(target_path, original_content)
    else:
        restore_file_from_snapshot(target_path, checkpoint.pre_state_ref)

