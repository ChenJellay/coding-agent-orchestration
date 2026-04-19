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
    materialize_passed_checkpoint_to_workspace,
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


def test_materialize_passed_checkpoint_writes_verified_body(tmp_path):
    repo = tmp_path / "mini"
    target_rel = "src/app.js"
    target_path = repo / target_rel
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("before", encoding="utf-8")

    task = _make_task(str(repo), target_file=target_rel)
    cp = Checkpoint(
        checkpoint_id="cp-mat",
        task_id=task.task_id,
        status=VerificationStatus.PASSED,
        pre_state_ref="before",
        post_state_ref="after merge body",
    )

    written = materialize_passed_checkpoint_to_workspace(task=task, checkpoint=cp)
    assert written.resolve() == target_path.resolve()
    assert target_path.read_text(encoding="utf-8") == "after merge body"


def test_materialize_raises_when_no_post_state(tmp_path):
    task = _make_task(str(tmp_path))
    cp = Checkpoint(
        checkpoint_id="cp-empty",
        task_id=task.task_id,
        status=VerificationStatus.PASSED,
        pre_state_ref="x",
        post_state_ref=None,
    )
    with pytest.raises(ValueError, match="post_state_ref"):
        materialize_passed_checkpoint_to_workspace(task=task, checkpoint=cp)
