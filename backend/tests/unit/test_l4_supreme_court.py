"""§4.4 — Supreme Court Consensus Router unit tests.

Tests:
- supreme_court_v1 is registered in the agent registry
- SupremeCourtOutput Pydantic model validates resolved / unresolved payloads
- node_supreme_court sets BLOCKED when agent returns resolved=false
- node_supreme_court applies patch when resolved=true
- VerificationConfig.supreme_court_enabled flag is respected in routing
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agenti_helix.agents.models import SupremeCourtOutput
from agenti_helix.agents.registry import get_agent
from agenti_helix.verification.config import VerificationConfig
from agenti_helix.verification.checkpointing import (
    EditTaskSpec,
    VerificationStatus,
    Checkpoint,
)
from agenti_helix.verification.verification_loop import (
    VerificationState,
    node_supreme_court,
)


def _make_task(repo_path: str = "/tmp/repo") -> EditTaskSpec:
    return EditTaskSpec(
        task_id="t-sc",
        repo_path=repo_path,
        target_file="src/app.py",
        intent="fix the login bug",
        acceptance_criteria="login works",
    )


def _make_checkpoint(task: EditTaskSpec, tmp_path: Path) -> Checkpoint:
    from agenti_helix.verification.checkpointing import create_pre_checkpoint, snapshot_file
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def login(): pass\n")
    return create_pre_checkpoint(
        EditTaskSpec(
            task_id=task.task_id,
            repo_path=str(tmp_path),
            target_file="src/app.py",
            intent=task.intent,
            acceptance_criteria=task.acceptance_criteria,
        ),
        "def login(): pass\n",
    )


# ---------------------------------------------------------------------------
# §4.4.1 — Agent registration
# ---------------------------------------------------------------------------

def test_supreme_court_v1_is_registered():
    spec = get_agent("supreme_court_v1")
    assert spec.agent_id == "supreme_court_v1"
    assert spec.backend_type == "mlx_local"


# ---------------------------------------------------------------------------
# §4.4.2 — Pydantic model validation
# ---------------------------------------------------------------------------

def test_supreme_court_output_resolved():
    obj = SupremeCourtOutput(
        resolved=True,
        reasoning="The coder forgot to handle None inputs.",
        filePath="src/app.py",
        startLine=1,
        endLine=1,
        replacementLines=["def login(user=None): return user is not None"],
        compromise_summary="Added None guard",
    )
    assert obj.resolved is True
    assert obj.filePath == "src/app.py"


def test_supreme_court_output_unresolved():
    obj = SupremeCourtOutput(
        resolved=False,
        reasoning="The intent contradicts the acceptance criteria.",
    )
    assert obj.resolved is False
    assert obj.filePath is None


# ---------------------------------------------------------------------------
# §4.4.3 — node_supreme_court transitions
# ---------------------------------------------------------------------------

def test_node_supreme_court_marks_blocked_when_not_resolved(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def login(): pass\n")

    task = EditTaskSpec(
        task_id="t-sc",
        repo_path=str(tmp_path),
        target_file="src/app.py",
        intent="fix login",
        acceptance_criteria="works",
    )
    checkpoint = Checkpoint(
        checkpoint_id="ckpt-1",
        task_id="t-sc",
        pre_state_ref="def login(): pass\n",
        status=VerificationStatus.RUNNING,
        created_at=0,
    )
    state = VerificationState(task=task, checkpoint=checkpoint)
    state.error_history = ["Attempt 1: FAIL"]

    unresolved = {"resolved": False, "reasoning": "Cannot determine correct fix"}

    with patch("agenti_helix.runtime.agent_runtime.run_agent", return_value=unresolved), \
         patch("agenti_helix.verification.verification_loop.record_post_state") as mock_record, \
         patch("agenti_helix.verification.verification_loop.save_checkpoint"):
        result = node_supreme_court(state)

    assert result.supreme_court_invoked is True
    mock_record.assert_called_once()
    _, kwargs = mock_record.call_args
    assert kwargs["status"] == VerificationStatus.BLOCKED


def test_node_supreme_court_applies_patch_when_resolved(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def login(): pass\n")

    task = EditTaskSpec(
        task_id="t-sc",
        repo_path=str(tmp_path),
        target_file="src/app.py",
        intent="fix login",
        acceptance_criteria="works",
    )
    checkpoint = Checkpoint(
        checkpoint_id="ckpt-2",
        task_id="t-sc",
        pre_state_ref="def login(): pass\n",
        status=VerificationStatus.RUNNING,
        created_at=0,
    )
    state = VerificationState(task=task, checkpoint=checkpoint)
    state.error_history = []

    resolved_patch = {
        "resolved": True,
        "reasoning": "Added None guard",
        "filePath": "src/app.py",
        "startLine": 1,
        "endLine": 1,
        "replacementLines": ["def login(user=None): return True"],
    }

    with patch("agenti_helix.runtime.agent_runtime.run_agent", return_value=resolved_patch), \
         patch("agenti_helix.runtime.tools.tool_apply_line_patch_and_validate", return_value={"ok": True}):
        result = node_supreme_court(state)

    assert result.supreme_court_invoked is True
    assert result.diff_json is not None
    assert result.diff_json["startLine"] == 1
    # No BLOCKED status — checkpoint should remain RUNNING
    assert result.checkpoint.status == VerificationStatus.RUNNING


# ---------------------------------------------------------------------------
# §4.4.4 — supreme_court_enabled flag in VerificationConfig
# ---------------------------------------------------------------------------

def test_supreme_court_enabled_default_true():
    cfg = VerificationConfig()
    assert cfg.supreme_court_enabled is True


def test_supreme_court_disabled_flag():
    cfg = VerificationConfig(supreme_court_enabled=False)
    assert cfg.supreme_court_enabled is False
