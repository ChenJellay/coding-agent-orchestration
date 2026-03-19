from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from agenti_helix.observability.debug_log import log_event
from agenti_helix.verification.checkpointing import EditTaskSpec, VerificationStatus
from agenti_helix.verification.verification_loop import run_verification_loop


class DagNodeStatus(str, Enum):
    """Execution status for a DAG node."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED_VERIFICATION = "PASSED_VERIFICATION"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


@dataclass
class DagNodeSpec:
    """Specification for a single DAG node."""

    node_id: str
    description: str
    task: EditTaskSpec


@dataclass
class DagSpec:
    """A small DAG of edit tasks."""

    dag_id: str
    macro_intent: str
    nodes: Dict[str, DagNodeSpec] = field(default_factory=dict)
    edges: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class DagNodeExecutionState:
    """Runtime state for a node during DAG execution."""

    node_id: str
    status: DagNodeStatus = DagNodeStatus.PENDING
    attempts: int = 0
    verification_status: Optional[VerificationStatus] = None


@dataclass
class DagExecutionResult:
    """Summary of a completed DAG run."""

    dag_id: str
    node_states: Dict[str, DagNodeExecutionState]
    failed_nodes: List[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return not self.failed_nodes and all(
            s.status is DagNodeStatus.PASSED_VERIFICATION for s in self.node_states.values()
        )


def _dag_dir() -> Path:
    root = Path(".").resolve()
    return root / ".agenti_helix" / "dags"


def _ensure_dag_dir() -> Path:
    ddir = _dag_dir()
    os.makedirs(ddir, exist_ok=True)
    return ddir


def _dag_path(dag_id: str) -> Path:
    return _dag_dir() / f"{dag_id}.json"


def persist_dag_spec(spec: DagSpec) -> None:
    """Persist a DAG definition to disk for observability."""
    _ensure_dag_dir()
    path = _dag_path(spec.dag_id)

    data = {
        "dag_id": spec.dag_id,
        "macro_intent": spec.macro_intent,
        "nodes": {
            node_id: {
                "node_id": node.node_id,
                "description": node.description,
                "task": asdict(node.task),
            }
            for node_id, node in spec.nodes.items()
        },
        "edges": list(spec.edges),
    }
    path.write_text(json.dumps(data, indent=2))


def persist_dag_execution_state(
    dag_id: str,
    node_states: Dict[str, DagNodeExecutionState],
) -> None:
    """Persist only the execution state snapshot for a DAG."""
    _ensure_dag_dir()
    path = _dag_path(f"{dag_id}_state")
    data = {
        "dag_id": dag_id,
        "nodes": {
            node_id: {
                "node_id": state.node_id,
                "status": state.status.value,
                "attempts": state.attempts,
                "verification_status": state.verification_status.value if state.verification_status is not None else None,
            }
            for node_id, state in node_states.items()
        },
    }
    path.write_text(json.dumps(data, indent=2))


def _topological_order(spec: DagSpec) -> List[str]:
    """Return a deterministic node order consistent with the DAG edges."""
    incoming: Dict[str, Set[str]] = {node_id: set() for node_id in spec.nodes}
    outgoing: Dict[str, Set[str]] = {node_id: set() for node_id in spec.nodes}

    for src, dst in spec.edges:
        incoming.setdefault(dst, set()).add(src)
        outgoing.setdefault(src, set()).add(dst)

    ready = sorted([n for n, preds in incoming.items() if not preds])
    order: List[str] = []

    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for dst in sorted(outgoing.get(node_id, ())):
            preds = incoming.get(dst)
            if preds is None:
                continue
            preds.discard(node_id)
            if not preds:
                ready.append(dst)

    if len(order) != len(spec.nodes):
        remaining = sorted(set(spec.nodes) - set(order))
        order.extend(remaining)
    return order


def execute_dag(spec: DagSpec) -> DagExecutionResult:
    """
    Execute a DAG by routing each node through the verification loop.

    Nodes only run when all predecessors have PASSED_VERIFICATION.
    """
    persist_dag_spec(spec)

    node_states: Dict[str, DagNodeExecutionState] = {
        node_id: DagNodeExecutionState(node_id=node_id) for node_id in spec.nodes
    }

    order = _topological_order(spec)
    predecessors: Dict[str, Set[str]] = {node_id: set() for node_id in spec.nodes}
    for src, dst in spec.edges:
        predecessors.setdefault(dst, set()).add(src)

    failed_nodes: List[str] = []

    log_event(
        run_id=spec.dag_id,
        hypothesis_id="DAG",
        location="agenti_helix/orchestration/orchestrator.py:execute_dag",
        message="Starting DAG execution",
        data={"dag_id": spec.dag_id, "macro_intent": spec.macro_intent, "nodes": list(spec.nodes.keys()), "edges": spec.edges},
    )

    for node_id in order:
        node_state = node_states[node_id]
        if node_state.status is not DagNodeStatus.PENDING:
            continue

        preds = predecessors.get(node_id, set())
        if any(node_states[p].status is not DagNodeStatus.PASSED_VERIFICATION for p in preds):
            node_state.status = DagNodeStatus.FAILED
            failed_nodes.append(node_id)
            continue

        node_state.status = DagNodeStatus.RUNNING
        node_state.attempts += 1

        node_spec = spec.nodes[node_id]
        log_event(
            run_id=spec.dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/orchestration/orchestrator.py:execute_dag",
            message="Starting node execution",
            data={"node_id": node_id, "description": node_spec.description, "task_id": node_spec.task.task_id},
        )

        cp_status: Optional[VerificationStatus] = None
        try:
            final_state = run_verification_loop(node_spec.task)
            cp = final_state.checkpoint if getattr(final_state, "checkpoint", None) else None
            cp_status = getattr(cp, "status", None)
            node_state.verification_status = cp_status

            if cp_status is VerificationStatus.PASSED:
                node_state.status = DagNodeStatus.PASSED_VERIFICATION
            else:
                node_state.status = DagNodeStatus.FAILED
                failed_nodes.append(node_id)
        except Exception:
            node_state.status = DagNodeStatus.FAILED
            node_state.verification_status = None
            failed_nodes.append(node_id)

        log_event(
            run_id=spec.dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/orchestration/orchestrator.py:execute_dag",
            message="Finished node execution",
            data={
                "node_id": node_id,
                "task_id": node_spec.task.task_id,
                "verification_status": cp_status.value if cp_status else None,
                "status": node_state.status.value,
            },
        )

        persist_dag_execution_state(spec.dag_id, node_states)

    log_event(
        run_id=spec.dag_id,
        hypothesis_id="DAG",
        location="agenti_helix/orchestration/orchestrator.py:execute_dag",
        message="Finished DAG execution",
        data={"dag_id": spec.dag_id, "failed_nodes": failed_nodes},
    )

    return DagExecutionResult(
        dag_id=spec.dag_id,
        node_states=node_states,
        failed_nodes=failed_nodes,
    )

