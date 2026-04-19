"""Tests for git unified diff capture in verification_loop (TDD / multi-file checkpoints)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agenti_helix.verification.checkpointing import EditTaskSpec
from agenti_helix.verification.verification_loop import (
    _git_unified_diff_for_paths,
    _paths_for_git_diff,
    _tool_logs_with_git_unified_diff,
)


def test_paths_for_git_diff_merges_target_and_lists() -> None:
    task = EditTaskSpec(
        task_id="t1",
        intent="x",
        target_file="src/a.py",
        acceptance_criteria="y",
        repo_path="/tmp",
    )
    dj = {
        "filePath": "src/a.py",
        "files_written": ["src/a.py", "lib/b.py"],
        "test_file_paths": ["tests/test_a.py"],
    }
    paths = _paths_for_git_diff(task, dj)
    assert paths == ["src/a.py", "lib/b.py", "tests/test_a.py"]


def test_git_unified_diff_tracked_and_untracked(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    tracked = tmp_path / "tracked.txt"
    tracked.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    tracked.write_text("v2\n", encoding="utf-8")
    newf = tmp_path / "new.txt"
    newf.write_text("hello\n", encoding="utf-8")

    out = _git_unified_diff_for_paths(tmp_path, ["tracked.txt", "new.txt"])
    assert "tracked.txt" in out or "v1" in out or "v2" in out
    assert "new.txt" in out
    assert "hello" in out


def test_tool_logs_merge_includes_git_when_non_empty(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    f = tmp_path / "a.txt"
    f.write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "c"], cwd=tmp_path, check=True)
    f.write_text("2", encoding="utf-8")

    task = EditTaskSpec(
        task_id="t1",
        intent="x",
        target_file="a.txt",
        acceptance_criteria="y",
        repo_path=str(tmp_path),
    )
    logs = _tool_logs_with_git_unified_diff(
        repo_root=tmp_path,
        task=task,
        diff_json={"files_written": ["a.txt"]},
        base={"judge": {"verdict": "PASS"}},
    )
    assert "git_unified_diff" in logs
    assert "a.txt" in logs["git_unified_diff"]


def test_git_unified_diff_not_git_repo_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("z", encoding="utf-8")
    assert _git_unified_diff_for_paths(tmp_path, ["x.txt"]) == ""
