"""§4.5 & §4.6 — Hybrid Escalation and Semantic Git Blame unit tests.

§4.5 tests:
- tool_escalate_to_human returns correct signal structure
- bandit security check is included in checks_run for Python files
- _check_bandit_security returns empty list when bandit is not installed

§4.6 tests:
- real_git_commit returns simulated=True when AGENTI_HELIX_GIT_COMMIT_ENABLED unset
- git_blame_line returns found=False when gitpython is absent
- _extract_trailer correctly parses Trace-Id and Dag-Id from commit messages
- GET /api/blame endpoint falls back to merge records
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agenti_helix.runtime.tools import tool_escalate_to_human
from agenti_helix.verification.checkpointing import EditTaskSpec, VerificationStatus, Checkpoint
from agenti_helix.verification.verification_loop import (
    VerificationState,
    _check_bandit_security,
)
from agenti_helix.api.git_ops import _extract_trailer, real_git_commit, git_blame_line


def _make_task(repo_path: str = "/tmp/repo") -> EditTaskSpec:
    return EditTaskSpec(
        task_id="t-esc",
        repo_path=repo_path,
        target_file="src/main.py",
        intent="add rate limiter",
        acceptance_criteria="rate limiting works",
    )


# ---------------------------------------------------------------------------
# §4.5.1 — tool_escalate_to_human
# ---------------------------------------------------------------------------

def test_tool_escalate_to_human_returns_signal():
    result = tool_escalate_to_human(
        reason="Scope too broad",
        blocker_summary="The intent references three different modules",
    )
    assert result["escalation_requested"] is True
    assert "Scope too broad" in result["reason"]
    assert "blocker_summary" in result


def test_tool_escalate_to_human_in_registry():
    from agenti_helix.runtime.tools import TOOL_REGISTRY
    assert "escalate_to_human" in TOOL_REGISTRY
    fn = TOOL_REGISTRY["escalate_to_human"]
    out = fn(reason="test", blocker_summary="summary")
    assert out["escalation_requested"] is True


# ---------------------------------------------------------------------------
# §4.5.3 — Bandit security scan
# ---------------------------------------------------------------------------

def test_check_bandit_security_skips_when_bandit_not_installed(tmp_path):
    target = tmp_path / "bad.py"
    target.write_text("import os\nos.system('rm -rf /')\n")

    with patch("subprocess.run", side_effect=FileNotFoundError("bandit not found")):
        errors = _check_bandit_security(target)

    # Must not raise — graceful skip
    assert errors == []


def test_check_bandit_security_returns_empty_for_clean_file(tmp_path):
    target = tmp_path / "clean.py"
    target.write_text("def add(a, b):\n    return a + b\n")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "No issues identified."
    with patch("subprocess.run", return_value=mock_result):
        errors = _check_bandit_security(target)

    assert errors == []


def test_run_static_checks_includes_bandit_for_python(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("def hello(): pass\n")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        from agenti_helix.verification.verification_loop import _run_static_checks
        result = _run_static_checks(tmp_path, "app.py")

    assert "bandit" in result["checks_run"]
    assert "security_blocked" in result


# ---------------------------------------------------------------------------
# §4.6.1 — real_git_commit simulation mode
# ---------------------------------------------------------------------------

def test_real_git_commit_simulated_when_env_unset():
    env = {k: v for k, v in os.environ.items() if k != "AGENTI_HELIX_GIT_COMMIT_ENABLED"}
    with patch.dict(os.environ, env, clear=True):
        result = real_git_commit(
            repo_path="/tmp/any",
            target_files=["src/main.py"],
            commit_message="feat: test",
        )

    assert result["ok"] is True
    assert result["simulated"] is True
    assert result["sha"] is None


# ---------------------------------------------------------------------------
# §4.6.2 — git_blame_line graceful fallback
# ---------------------------------------------------------------------------

def test_git_blame_line_returns_not_found_when_gitpython_absent():
    with patch.dict("sys.modules", {"git": None}):
        result = git_blame_line(repo_path="/tmp/nope", file_path="src/a.py", line=1)

    assert result["found"] is False


# ---------------------------------------------------------------------------
# §4.6.3 — _extract_trailer
# ---------------------------------------------------------------------------

def test_extract_trailer_finds_trace_id():
    msg = "feat: add login\n\nTrace-Id: abc-123\nDag-Id: dag-456"
    assert _extract_trailer(msg, "Trace-Id") == "abc-123"
    assert _extract_trailer(msg, "Dag-Id") == "dag-456"


def test_extract_trailer_returns_none_when_missing():
    msg = "fix: typo"
    assert _extract_trailer(msg, "Trace-Id") is None


# ---------------------------------------------------------------------------
# §4.6.4 — CoderPatchOutput escalation fields
# ---------------------------------------------------------------------------

def test_coder_patch_output_accepts_escalation_fields():
    from agenti_helix.agents.models import CoderPatchOutput
    obj = CoderPatchOutput(
        filePath="",
        startLine=0,
        endLine=0,
        replacementLines=[],
        escalate_to_human=True,
        escalation_reason="The intent is ambiguous",
    )
    assert obj.escalate_to_human is True
    assert "ambiguous" in obj.escalation_reason
