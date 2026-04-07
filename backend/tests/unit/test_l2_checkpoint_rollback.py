"""
L2.2 — Checkpoint rollback tests.

Verifies that:
- rollback_to_checkpoint restores file content.
- rollback_to_checkpoint resets checkpoint.status to RUNNING.
- rollback_to_checkpoint clears post_state_ref and diff.
- The reset checkpoint is persisted to disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenti_helix.verification.checkpointing import (
    Checkpoint,
    EditTaskSpec,
    VerificationStatus,
    rollback_to_checkpoint,
    save_checkpoint,
    load_checkpoint,
)


def _make_task(repo_path: str, target_file: str = "target.py") -> EditTaskSpec:
    return EditTaskSpec(
        task_id="task-rollback-test",
        intent="test",
        target_file=target_file,
        acceptance_criteria="pass",
        repo_path=repo_path,
    )


def _make_checkpoint(task: EditTaskSpec, pre_content: str, cp_dir: Path) -> Checkpoint:
    import os, uuid, time
    os.makedirs(cp_dir, exist_ok=True)

    cp = Checkpoint(
        checkpoint_id=str(uuid.uuid4()),
        task_id=task.task_id,
        status=VerificationStatus.BLOCKED,
        pre_state_ref=pre_content,
        post_state_ref="post content",
        diff='{"filePath": "target.py"}',
    )
    # Write checkpoint to cp_dir
    (cp_dir / f"{cp.checkpoint_id}.json").write_text(
        json.dumps({
            "checkpoint_id": cp.checkpoint_id,
            "task_id": cp.task_id,
            "status": cp.status.value,
            "pre_state_ref": cp.pre_state_ref,
            "post_state_ref": cp.post_state_ref,
            "diff": cp.diff,
            "tool_logs": {},
            "created_at": cp.created_at,
            "updated_at": cp.updated_at,
        })
    )
    return cp


def test_rollback_restores_file_content(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTI_HELIX_REPO_ROOT", str(tmp_path))

    target = tmp_path / "target.py"
    target.write_text("# modified content")
    task = _make_task(str(tmp_path))
    cp = _make_checkpoint(task, "# original content", tmp_path / ".agenti_helix" / "checkpoints")

    rollback_to_checkpoint(task, cp, original_content="# original content")

    assert target.read_text() == "# original content"


def test_rollback_resets_checkpoint_status_to_running(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTI_HELIX_REPO_ROOT", str(tmp_path))

    target = tmp_path / "target.py"
    target.write_text("modified")
    task = _make_task(str(tmp_path))
    cp_dir = tmp_path / ".agenti_helix" / "checkpoints"
    cp = _make_checkpoint(task, "original", cp_dir)

    assert cp.status == VerificationStatus.BLOCKED

    rollback_to_checkpoint(task, cp)

    assert cp.status == VerificationStatus.RUNNING


def test_rollback_clears_post_state_and_diff(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTI_HELIX_REPO_ROOT", str(tmp_path))

    target = tmp_path / "target.py"
    target.write_text("modified")
    task = _make_task(str(tmp_path))
    cp_dir = tmp_path / ".agenti_helix" / "checkpoints"
    cp = _make_checkpoint(task, "original", cp_dir)

    assert cp.post_state_ref is not None
    assert cp.diff is not None

    rollback_to_checkpoint(task, cp)

    assert cp.post_state_ref is None
    assert cp.diff is None


def test_rollback_persists_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTI_HELIX_REPO_ROOT", str(tmp_path))

    target = tmp_path / "target.py"
    target.write_text("modified")
    task = _make_task(str(tmp_path))
    cp_dir = tmp_path / ".agenti_helix" / "checkpoints"
    cp = _make_checkpoint(task, "original", cp_dir)

    rollback_to_checkpoint(task, cp)

    # Reload from disk and verify the status was persisted
    reloaded = load_checkpoint(cp.checkpoint_id)
    assert reloaded.status == VerificationStatus.RUNNING
