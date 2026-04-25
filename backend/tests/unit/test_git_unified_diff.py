"""Tests for git unified diff capture in verification_loop (TDD / multi-file checkpoints)."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from agenti_helix.runtime.tools import tool_get_git_unified_diff
from agenti_helix.verification.checkpointing import EditTaskSpec
from agenti_helix.verification.verification_loop import (
    _git_unified_diff_for_paths,
    _paths_for_git_diff,
    _tool_logs_with_git_unified_diff,
)


@pytest.fixture
def workspace_tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A per-test scratch directory located *inside* the workspace.

    ``pytest``'s default ``tmp_path`` resolves to ``/var/folders/...`` on macOS,
    which some sandboxes (Cursor, CI tmpfs) mount read-only for child
    processes. Anchoring scratch dirs under the repo root keeps them writable
    in most environments (real CI, dev laptops) while preserving per-test
    isolation + cleanup. When the sandbox *also* blocks ``.git/`` writes
    (see ``_git_init``), we skip those tests entirely rather than fail.
    """
    repo_root = Path(__file__).resolve().parents[3]
    base = repo_root / ".pytest-git-tmp"
    base.mkdir(parents=True, exist_ok=True)
    scratch = base / f"{uuid.uuid4().hex}"
    scratch.mkdir()
    try:
        yield scratch
    finally:
        if os.environ.get("AGENTI_HELIX_KEEP_TEST_TMP") != "1":
            shutil.rmtree(scratch, ignore_errors=True)


_SANDBOX_PERMISSION_MARKERS = ("Operation not permitted", "Permission denied")


def _sandbox_blocks_git(stderr: str) -> bool:
    """Heuristic: did this failure come from a sandbox blocking ``.git/`` writes?

    The Cursor / CI sandboxes we care about reject every write under any
    ``.git/`` subpath. In those environments we want ``pytest.skip`` rather
    than a red test — the code path is still covered in real CI.
    """
    return any(marker in stderr for marker in _SANDBOX_PERMISSION_MARKERS)


def _run_git(args: list[str], *, cwd: Path) -> None:
    """Run a ``git`` command and surface stderr on failure.

    Sandboxes (Cursor, some CI tmpfs) block writes under ``.git/``; bubbling
    the stderr + skipping those tests keeps the suite green while still
    exercising the code path in unconstrained environments.
    """
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        if _sandbox_blocks_git(r.stderr):
            pytest.skip(
                f"Sandbox blocks 'git {' '.join(args)}' in {cwd} ({r.stderr.strip()}); "
                "these tests require an environment that permits .git/ writes."
            )
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {r.returncode}) in {cwd}:\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


def _git_init(path: Path) -> None:
    """Initialise a throwaway git repo for a test fixture.

    ``--template=""`` suppresses the default template-hooks copy, which some
    sandboxed filesystems disallow writes into. If the sandbox blocks the
    init itself, ``_run_git`` will ``pytest.skip`` rather than red-fail.
    Identity + gpg defaults are pinned so the subsequent commit never goes
    hunting for global config.
    """
    _run_git(["init", "--template="], cwd=path)
    _run_git(["config", "user.email", "t@t.t"], cwd=path)
    _run_git(["config", "user.name", "t"], cwd=path)
    _run_git(["config", "commit.gpgsign", "false"], cwd=path)


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


def test_git_unified_diff_tracked_and_untracked(workspace_tmp_path: Path) -> None:
    _git_init(workspace_tmp_path)

    tracked = workspace_tmp_path / "tracked.txt"
    tracked.write_text("v1\n", encoding="utf-8")
    _run_git(["add", "tracked.txt"], cwd=workspace_tmp_path)
    _run_git(["commit", "-m", "init"], cwd=workspace_tmp_path)

    tracked.write_text("v2\n", encoding="utf-8")
    newf = workspace_tmp_path / "new.txt"
    newf.write_text("hello\n", encoding="utf-8")

    out = _git_unified_diff_for_paths(workspace_tmp_path, ["tracked.txt", "new.txt"])
    assert "tracked.txt" in out or "v1" in out or "v2" in out
    assert "new.txt" in out
    assert "hello" in out


def test_tool_logs_merge_includes_git_when_non_empty(workspace_tmp_path: Path) -> None:
    _git_init(workspace_tmp_path)

    f = workspace_tmp_path / "a.txt"
    f.write_text("1", encoding="utf-8")
    _run_git(["add", "a.txt"], cwd=workspace_tmp_path)
    _run_git(["commit", "-m", "c"], cwd=workspace_tmp_path)
    f.write_text("2", encoding="utf-8")

    task = EditTaskSpec(
        task_id="t1",
        intent="x",
        target_file="a.txt",
        acceptance_criteria="y",
        repo_path=str(workspace_tmp_path),
    )
    logs = _tool_logs_with_git_unified_diff(
        repo_root=workspace_tmp_path,
        task=task,
        diff_json={"files_written": ["a.txt"]},
        base={"judge": {"verdict": "PASS"}},
    )
    assert "git_unified_diff" in logs
    assert "a.txt" in logs["git_unified_diff"]


def test_tool_get_git_unified_diff_includes_untracked_target(workspace_tmp_path: Path) -> None:
    """Regression: diff_validator used plain ``git diff HEAD``, which omits untracked files."""
    _git_init(workspace_tmp_path)
    root_readme = workspace_tmp_path / "README.md"
    root_readme.write_text("# x\n", encoding="utf-8")
    _run_git(["add", "README.md"], cwd=workspace_tmp_path)
    _run_git(["commit", "-m", "init"], cwd=workspace_tmp_path)

    new_js = workspace_tmp_path / "src" / "new.js"
    new_js.parent.mkdir(parents=True, exist_ok=True)
    new_js.write_text("export const x = 1;\n", encoding="utf-8")

    out = tool_get_git_unified_diff(
        repo_root=str(workspace_tmp_path),
        target_file="src/new.js",
        diff_json={},
    )
    assert out["git_ok"] is True
    assert out["git_diff"] != "(empty diff)"
    assert "new.js" in out["git_diff"]
    assert "export const x" in out["git_diff"]


def test_git_unified_diff_not_git_repo_returns_empty(tmp_path: Path) -> None:
    # This case only needs a readable non-repo directory, so the default
    # ``tmp_path`` is fine — we don't run ``git init`` here.
    (tmp_path / "x.txt").write_text("z", encoding="utf-8")
    assert _git_unified_diff_for_paths(tmp_path, ["x.txt"]) == ""
