from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from agenti_helix.verification.checkpointing import Checkpoint, EditTaskSpec, VerificationStatus
from agenti_helix.orchestration import orchestrator as orch
from agenti_helix.orchestration.orchestrator import DagNodeSpec, DagSpec, persist_dag_spec


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


def _build_demo_dag(repo_root: Path, *, dag_id: str) -> DagSpec:
    """Build a 3-node linear demo DAG inline (no LLM, no deterministic compiler).

    Replaces the deleted ``compile_macro_intent_deterministic`` helper.
    """
    nodes_def: List[Tuple[str, str]] = [
        ("N1-change-color", "header-color-primary"),
        ("N2-refine-styles", "header-style-refine"),
        ("N3-verify-structure", "header-structure-verify"),
    ]
    nodes: Dict[str, DagNodeSpec] = {}
    for node_id, task_id in nodes_def:
        nodes[node_id] = DagNodeSpec(
            node_id=node_id,
            description=f"{node_id} description",
            task=EditTaskSpec(
                task_id=task_id,
                intent=f"Demo intent for {node_id}",
                target_file="src/components/header.js",
                acceptance_criteria=f"Acceptance for {node_id}",
                repo_path=str(repo_root),
            ),
        )
    edges = [
        ("N1-change-color", "N2-refine-styles"),
        ("N2-refine-styles", "N3-verify-structure"),
    ]
    spec = DagSpec(
        dag_id=dag_id,
        macro_intent="Demo macro intent.",
        nodes=nodes,
        edges=edges,
        user_intent_label="Demo macro intent.",
    )
    persist_dag_spec(spec)
    return spec


def test_execute_dag_runs_nodes_in_order_when_all_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "PATHS", HelixPaths(repo_root=tmp_path.resolve()))
    monkeypatch.setattr(orch, "try_load_dag_state", lambda _dag_id: None)
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")

    spec = _build_demo_dag(repo, dag_id=f"dag-all-pass-{uuid.uuid4().hex[:12]}")

    called_tasks: list[EditTaskSpec] = []

    def fake_run_verification_loop(task: EditTaskSpec, *args: Any, **kwargs: Any) -> Any:
        called_tasks.append(task)
        return DummyVerificationState(VerificationStatus.PASSED)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(orch, "run_verification_loop", fake_run_verification_loop)

    result = orch.execute_dag(spec)
    assert len(called_tasks) == len(spec.nodes)

    assert result.all_passed
    for node_id, state in result.node_states.items():
        assert state.status is orch.DagNodeStatus.PASSED_VERIFICATION
        assert state.verification_status is VerificationStatus.PASSED
        assert state.attempts == 1

    assert len(called_tasks) == len(spec.nodes)


def test_execute_dag_blocks_downstream_on_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch, "PATHS", HelixPaths(repo_root=tmp_path.resolve()))
    monkeypatch.setattr(orch, "try_load_dag_state", lambda _dag_id: None)
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")

    spec = _build_demo_dag(repo, dag_id=f"dag-with-failure-{uuid.uuid4().hex[:12]}")

    def fake_run_verification_loop(task: EditTaskSpec, *args: Any, **kwargs: Any) -> Any:
        if task.task_id == "header-style-refine":
            return DummyVerificationState(VerificationStatus.BLOCKED)
        return DummyVerificationState(VerificationStatus.PASSED)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(orch, "run_verification_loop", fake_run_verification_loop)

    result = orch.execute_dag(spec)
    # N1 and N2 should have attempted verification; N3 is blocked by predecessor.
    # (execute_dag is sequential and will never run N3 if N2 is blocked.)

    node1 = result.node_states["N1-change-color"]
    node2 = result.node_states["N2-refine-styles"]
    node3 = result.node_states["N3-verify-structure"]

    assert node1.status is orch.DagNodeStatus.PASSED_VERIFICATION
    assert node1.verification_status is VerificationStatus.PASSED

    assert node2.status is orch.DagNodeStatus.FAILED
    assert node2.verification_status is VerificationStatus.BLOCKED

    assert node3.status is orch.DagNodeStatus.FAILED
    assert node3.verification_status is None
