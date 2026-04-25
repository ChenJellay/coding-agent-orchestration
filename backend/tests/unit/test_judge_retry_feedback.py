"""Judge → retry feedback: cumulative text, merged evaluator fields, governor audit."""

from __future__ import annotations

import pytest

from agenti_helix.runtime.tools import tool_map_evaluator_verdict
from agenti_helix.verification.checkpointing import Checkpoint, EditTaskSpec, VerificationStatus
from agenti_helix.verification import verification_loop as vl


def test_map_evaluator_merges_feedback_and_reasoning_on_fail() -> None:
    out = tool_map_evaluator_verdict(
        pass_tests=False,
        evaluation_reasoning="Root cause: import mismatch.",
        feedback_for_coder="Change export to default.",
        audit_reasoning="",
        is_safe=True,
    )
    assert out["verdict"] == "FAIL"
    j = out["justification"]
    assert "Change export to default." in j
    assert "import mismatch" in j
    assert "Action for next edit:" in j
    assert "Analysis:" in j


def test_map_evaluator_appends_governor_audit_when_safe_fail() -> None:
    out = tool_map_evaluator_verdict(
        pass_tests=False,
        evaluation_reasoning="Tests failed.",
        feedback_for_coder="Fix the assertion.",
        audit_reasoning="Checked for eval(); none found. " * 20,
        is_safe=True,
    )
    assert "Security governor (is_safe=true):" in out["justification"]
    assert "eval()" in out["justification"]


def test_map_evaluator_no_audit_append_when_unsafe() -> None:
    out = tool_map_evaluator_verdict(
        pass_tests=False,
        evaluation_reasoning="x",
        feedback_for_coder="y",
        audit_reasoning="should not appear",
        is_safe=False,
        violations=["eval() banned"],
    )
    assert "Security governor" not in out["justification"]
    assert "eval() banned" in out["justification"]


def test_prepare_retry_appends_rounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vl, "rollback_to_checkpoint", lambda *a, **k: None)

    task = EditTaskSpec(
        task_id="t-retry-fb",
        intent="do thing",
        target_file="src/x.js",
        acceptance_criteria="works",
        repo_path=str(tmp_path),
    )
    cp = Checkpoint(
        checkpoint_id="cp1",
        task_id=task.task_id,
        status=VerificationStatus.RUNNING,
        pre_state_ref="",
    )
    state = vl.VerificationState(
        task=task,
        checkpoint=cp,
        feedback="--- After judge round 1 ---\nFirst justification",
        retry_count=2,
        judge_response={"verdict": "FAIL", "justification": "Second justification"},
    )
    vl._prepare_retry(state)
    assert "First justification" in state.feedback
    assert "Second justification" in state.feedback
    assert "After judge round 2" in state.feedback
    assert state.feedback.index("First justification") < state.feedback.index("Second justification")
