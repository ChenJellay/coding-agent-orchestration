from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from agenti_helix.api.paths import PATHS


class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    # Judge approved patch pipeline output; workspace rolled back until human sign-off applies it.
    PASSED_PENDING_SIGNOFF = "PASSED_PENDING_SIGNOFF"
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

    # Optional plug-and-play execution configuration.
    # When omitted, the verification loop uses default coder/judge chains.
    coder_chain: Optional[Dict[str, Any]] = None
    judge_chain: Optional[Dict[str, Any]] = None
    # Execution pipeline hint: "patch" (fast, single-file) or "build" (full TDD pipeline).
    # Used by master_orchestrator.resolve_coder_chain / resolve_judge_chain when chains are not explicit.
    pipeline_mode: str = "patch"
    # Optional bespoke workflow — ordered list of agent_ids. When set, master_orchestrator
    # synthesizes coder+judge chains dynamically, ignoring `pipeline_mode` presets.
    workflow: Optional[List[str]] = None


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
    # Important: keep persistence rooted at `AGENTI_HELIX_REPO_ROOT` so the
    # control-plane server can be launched from any working directory.
    return PATHS.checkpoints_dir


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
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # New-file tasks should be able to start from an empty pre-state.
        return ""


def restore_file_from_snapshot(path: Path, snapshot: str) -> None:
    """Restore a file to a previous snapshot."""
    path.write_text(snapshot)


def apply_signed_off_checkpoint(*, task: EditTaskSpec, checkpoint: Checkpoint, signed_by: Optional[str] = None) -> None:
    """
    Materialize judge-approved content from a staged checkpoint onto disk.

    Expects ``PASSED_PENDING_SIGNOFF`` with ``post_state_ref`` holding the full proposed file body
    (patch pipeline), or a JSON reference for multi-file build pipeline outputs.
    """
    if checkpoint.status is not VerificationStatus.PASSED_PENDING_SIGNOFF:
        raise ValueError(f"Checkpoint must be PASSED_PENDING_SIGNOFF, got {checkpoint.status!r}")
    if not checkpoint.post_state_ref:
        raise ValueError("Checkpoint has no post_state_ref to apply")

    repo_root = Path(task.repo_path).resolve()

    # Build pipeline: multi-file sign-off applies all post snapshots captured by write_all_files.
    try:
        ref = json.loads(checkpoint.post_state_ref)
    except Exception:
        ref = None
    if isinstance(ref, dict) and ref.get("kind") == "multi_file" and isinstance(ref.get("snapshots_dir"), str):
        snapshots_dir = (repo_root / str(ref["snapshots_dir"])).resolve()
        snapshots_path = snapshots_dir / "snapshots.json"
        if not snapshots_path.exists():
            raise ValueError(f"Missing snapshots.json at {snapshots_path}")
        snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))
        post = snapshots.get("post") if isinstance(snapshots, dict) else None
        if not isinstance(post, dict) or not post:
            raise ValueError("snapshots.json missing 'post' map")
        for rel_path, content in post.items():
            if not isinstance(rel_path, str) or not rel_path:
                continue
            if not isinstance(content, str):
                continue
            p = (repo_root / rel_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    else:
        # Patch pipeline: single target_file apply.
        target_path = repo_root / task.target_file
        target_path.write_text(checkpoint.post_state_ref, encoding="utf-8")

    logs = dict(checkpoint.tool_logs or {})
    logs["manual_signoff"] = {"signed_by": signed_by or "anonymous", "applied_at": time.time()}
    checkpoint.tool_logs = logs
    checkpoint.status = VerificationStatus.PASSED
    save_checkpoint(checkpoint)


def rollback_to_checkpoint(
    task: EditTaskSpec,
    checkpoint: Checkpoint,
    *,
    original_content: Optional[str] = None,
) -> None:
    """
    Roll back the target file to the pre-state for this checkpoint.

    - If original_content is provided, write it back.
    - Otherwise, interpret pre_state_ref as file content and restore it.

    The checkpoint status is reset to RUNNING so the next verification
    attempt starts from a clean slate, and the updated checkpoint is
    persisted to disk so external observers (e.g. the DAG state API) see
    the transition correctly.
    """
    target_path = Path(task.repo_path).resolve() / task.target_file
    if original_content is not None:
        restore_file_from_snapshot(target_path, original_content)
    else:
        restore_file_from_snapshot(target_path, checkpoint.pre_state_ref)

    checkpoint.status = VerificationStatus.RUNNING
    checkpoint.post_state_ref = None
    checkpoint.diff = None
    save_checkpoint(checkpoint)

