from __future__ import annotations

from pathlib import Path

from agenti_helix.verification.checkpointing import (
    Checkpoint,
    EditTaskSpec,
    VerificationStatus,
    create_pre_checkpoint,
    list_checkpoints_for_task,
    record_post_state,
    rollback_to_checkpoint,
    snapshot_file,
)


def test_create_and_persist_checkpoint(tmp_path: Path, monkeypatch) -> None:
    # Use a temporary working directory for checkpoint files and target file.
    monkeypatch.chdir(tmp_path)

    repo = tmp_path / "demo-repo"
    repo.mkdir()
    target = repo / "file.txt"
    target.write_text("original")

    task = EditTaskSpec(
        task_id="t1",
        intent="change file",
        target_file="file.txt",
        acceptance_criteria="file content is 'edited'",
        repo_path=str(repo),
    )

    pre_snapshot = snapshot_file(target)
    cp = create_pre_checkpoint(task, pre_snapshot)
    assert cp.task_id == "t1"
    assert cp.pre_state_ref == "original"

    updated = record_post_state(
        cp,
        post_state_ref="edited",
        diff="{}",
        tool_logs={"tool": "ok"},
        status=VerificationStatus.PASSED,
    )
    assert updated.status is VerificationStatus.PASSED

    # Check that listing by task id returns the checkpoint.
    all_cps = list_checkpoints_for_task("t1")
    assert len(all_cps) == 1
    loaded = all_cps[0]
    assert loaded.task_id == "t1"
    assert loaded.status is VerificationStatus.PASSED


def test_rollback_restores_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    repo = tmp_path / "demo-repo"
    repo.mkdir()
    target = repo / "file.txt"
    target.write_text("before")

    task = EditTaskSpec(
        task_id="t2",
        intent="change file",
        target_file="file.txt",
        acceptance_criteria="",
        repo_path=str(repo),
    )

    pre_snapshot = snapshot_file(target)
    cp = create_pre_checkpoint(task, pre_snapshot)

    # Mutate the file.
    target.write_text("after")
    assert target.read_text() == "after"

    rollback_to_checkpoint(task, cp, original_content=pre_snapshot)
    assert target.read_text() == "before"

