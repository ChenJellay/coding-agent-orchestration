from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from agenti_helix.verification.checkpointing import Checkpoint, EditTaskSpec, VerificationStatus
from agenti_helix.orchestration import intent_compiler, orchestrator as orch


class DummyVerificationState:
    def __init__(self, status: VerificationStatus) -> None:
        self.checkpoint = Checkpoint(
            checkpoint_id="cp",
            task_id="t",
            status=status,
            pre_state_ref="",
            post_state_ref=None,
            diff=None,
            tool_logs={},
        )


def test_compile_macro_intent_creates_linear_dag(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")

    macro_intent = "Update header button color, refactor styles, and update tests."
    dag_id = f"dag-test-{uuid.uuid4().hex[:12]}"
    spec = intent_compiler.compile_macro_intent_deterministic(
        macro_intent,
        repo_path=str(repo),
        dag_id=dag_id,
    )

    assert spec.dag_id == dag_id
    assert 3 <= len(spec.nodes) <= 5
    # Expect a simple linear chain N1 -> N2 -> N3 for the demo.
    assert ("N1-change-color", "N2-refine-styles") in spec.edges
    assert ("N2-refine-styles", "N3-verify-structure") in spec.edges


def test_execute_dag_runs_nodes_in_order_when_all_pass(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")

    macro_intent = "Demo macro intent."
    spec = intent_compiler.compile_macro_intent_deterministic(
        macro_intent,
        repo_path=str(repo),
        dag_id=f"dag-all-pass-{uuid.uuid4().hex[:12]}",
    )

    called_tasks: list[EditTaskSpec] = []

    def fake_run_verification_loop(task: EditTaskSpec, *args: Any, **kwargs: Any) -> Any:
        called_tasks.append(task)
        return DummyVerificationState(VerificationStatus.PASSED)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(orch, "run_verification_loop", fake_run_verification_loop)

    result = orch.execute_dag(spec)

    # All nodes should have passed verification.
    assert result.all_passed
    for node_id, state in result.node_states.items():
        assert state.status is orch.DagNodeStatus.PASSED_VERIFICATION
        assert state.verification_status is VerificationStatus.PASSED
        assert state.attempts == 1

    # Ensure we invoked Phase 2 once per node.
    assert len(called_tasks) == len(spec.nodes)


def test_execute_dag_blocks_downstream_on_failure(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")

    macro_intent = "Demo macro intent."
    spec = intent_compiler.compile_macro_intent_deterministic(
        macro_intent,
        repo_path=str(repo),
        dag_id=f"dag-with-failure-{uuid.uuid4().hex[:12]}",
    )

    def fake_run_verification_loop(task: EditTaskSpec, *args: Any, **kwargs: Any) -> Any:
        if task.task_id == "header-style-refine":
            return DummyVerificationState(VerificationStatus.BLOCKED)
        return DummyVerificationState(VerificationStatus.PASSED)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(orch, "run_verification_loop", fake_run_verification_loop)

    result = orch.execute_dag(spec)

    # First node should pass, second fail, third be marked failed due to predecessor.
    node1 = result.node_states["N1-change-color"]
    node2 = result.node_states["N2-refine-styles"]
    node3 = result.node_states["N3-verify-structure"]

    assert node1.status is orch.DagNodeStatus.PASSED_VERIFICATION
    assert node1.verification_status is VerificationStatus.PASSED

    assert node2.status is orch.DagNodeStatus.FAILED
    assert node2.verification_status is VerificationStatus.BLOCKED

    # Third node should not have a verification status because its predecessor failed.
    assert node3.status is orch.DagNodeStatus.FAILED
    assert node3.verification_status is None

