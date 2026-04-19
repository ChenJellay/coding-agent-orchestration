from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from agenti_helix.observability.debug_log import log_event, write_cursor_debug_ndjson
from agenti_helix.api.paths import PATHS
from agenti_helix.api.task_lookup import try_load_dag_state
from agenti_helix.verification.checkpointing import EditTaskSpec, VerificationStatus
from agenti_helix.verification.verification_loop import run_verification_loop


class DagNodeStatus(str, Enum):
    """Execution status for a DAG node."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED_VERIFICATION = "PASSED_VERIFICATION"
    # Patch pipeline: judge approved but workspace rolled back until POST sign-off apply.
    AWAITING_SIGNOFF = "AWAITING_SIGNOFF"
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
    paused_for_signoff: bool = False

    @property
    def all_passed(self) -> bool:
        return (
            not self.failed_nodes
            and not self.paused_for_signoff
            and all(s.status is DagNodeStatus.PASSED_VERIFICATION for s in self.node_states.values())
        )


def _dag_dir() -> Path:
    # Important: keep persistence rooted at `AGENTI_HELIX_REPO_ROOT` so the
    # control-plane server can be launched from any working directory.
    return PATHS.dags_dir


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
    prior = try_load_dag_state(dag_id)
    prior_nodes = prior.get("nodes") if isinstance(prior, dict) else None
    merged_nodes: Dict[str, Any] = {}
    for node_id, state in node_states.items():
        base: Dict[str, Any] = {
            "node_id": state.node_id,
            "status": state.status.value,
            "attempts": state.attempts,
            "verification_status": state.verification_status.value if state.verification_status is not None else None,
        }
        if isinstance(prior_nodes, dict):
            prev = prior_nodes.get(node_id) or prior_nodes.get(str(node_id))
            if isinstance(prev, dict):
                for k, v in prev.items():
                    if k in {"node_id", "status", "attempts", "verification_status"}:
                        continue
                    base[k] = v
        merged_nodes[node_id] = base
    data = {"dag_id": dag_id, "nodes": merged_nodes}
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


def _dag_node_status_from_value(raw: Optional[str]) -> Optional[DagNodeStatus]:
    if not raw:
        return None
    try:
        return DagNodeStatus(str(raw))
    except ValueError:
        return None


def _verification_status_from_value(raw: Optional[str]) -> Optional[VerificationStatus]:
    if not raw:
        return None
    try:
        return VerificationStatus(str(raw))
    except ValueError:
        return None


def _initial_node_states_from_disk(spec: DagSpec) -> Dict[str, DagNodeExecutionState]:
    """Seed node execution state from a persisted ``*_state.json`` when resuming a DAG."""
    existing = try_load_dag_state(spec.dag_id)
    out: Dict[str, DagNodeExecutionState] = {}
    nodes_raw = (existing or {}).get("nodes") if isinstance(existing, dict) else None
    for node_id in spec.nodes:
        base = DagNodeExecutionState(node_id=node_id)
        if isinstance(nodes_raw, dict):
            raw_ns = nodes_raw.get(node_id) or nodes_raw.get(str(node_id))
            if isinstance(raw_ns, dict):
                st = _dag_node_status_from_value(raw_ns.get("status"))  # type: ignore[arg-type]
                vs = _verification_status_from_value(raw_ns.get("verification_status"))  # type: ignore[arg-type]
                if st is not None:
                    base.status = st
                if vs is not None:
                    base.verification_status = vs
                try:
                    base.attempts = int(raw_ns.get("attempts") or 0)
                except (TypeError, ValueError):
                    base.attempts = 0
        out[node_id] = base
    return out


def _requeue_retryable_nodes(
    node_states: Dict[str, DagNodeExecutionState],
) -> list[str]:
    """
    New ``execute_dag`` invocations should retry nodes that failed or escalated on a
    prior run. Persisted ``*_state.json`` otherwise leaves them ``FAILED``, and the
    main loop would skip them forever (instant no-op DAG completion).
    """
    reset_ids: list[str] = []
    for node_id, ns in node_states.items():
        if ns.status in (DagNodeStatus.FAILED, DagNodeStatus.ESCALATED):
            ns.status = DagNodeStatus.PENDING
            ns.verification_status = None
            reset_ids.append(node_id)
    return reset_ids


def execute_dag(spec: DagSpec) -> DagExecutionResult:
    """
    Execute a DAG by routing each node through the verification loop.

    Nodes only run when all predecessors have PASSED_VERIFICATION.
    A unique `trace_id` is generated per DAG run and propagated to every
    verification loop so all events can be correlated.
    """
    persist_dag_spec(spec)

    trace_id = str(uuid.uuid4())

    node_states = _initial_node_states_from_disk(spec)
    requeued = _requeue_retryable_nodes(node_states)
    # #region agent log
    write_cursor_debug_ndjson(
        location="orchestrator.py:execute_dag",
        message="requeue_retryable_nodes",
        hypothesis_id="H1",
        data={"dag_id": spec.dag_id, "requeued_node_ids": requeued},
        run_id=spec.dag_id,
    )
    # #endregion
    if requeued:
        log_event(
            run_id=spec.dag_id,
            hypothesis_id="dag_requeue",
            location="agenti_helix/orchestration/orchestrator.py:execute_dag",
            message="Re-queued failed/escalated nodes for retry (persisted state from prior run)",
            data={"dag_id": spec.dag_id, "node_ids": requeued},
            trace_id=trace_id,
            dag_id=spec.dag_id,
        )
        persist_dag_execution_state(spec.dag_id, node_states)

    order = _topological_order(spec)
    predecessors: Dict[str, Set[str]] = {node_id: set() for node_id in spec.nodes}
    for src, dst in spec.edges:
        predecessors.setdefault(dst, set()).add(src)

    failed_nodes: List[str] = []
    paused_for_signoff = False

    log_event(
        run_id=spec.dag_id,
        hypothesis_id="dag_start",
        location="agenti_helix/orchestration/orchestrator.py:execute_dag",
        message="Starting DAG execution",
        data={"dag_id": spec.dag_id, "macro_intent": spec.macro_intent, "nodes": list(spec.nodes.keys()), "edges": spec.edges},
        trace_id=trace_id,
        dag_id=spec.dag_id,
    )

    for node_id in order:
        node_state = node_states[node_id]
        if node_state.status is DagNodeStatus.RUNNING:
            node_state.status = DagNodeStatus.PENDING
        if node_state.status in (DagNodeStatus.PASSED_VERIFICATION, DagNodeStatus.AWAITING_SIGNOFF):
            # #region agent log
            write_cursor_debug_ndjson(
                location="orchestrator.py:execute_dag:skip",
                message="node_skip_already_verified",
                hypothesis_id="H2",
                data={"node_id": node_id, "status": node_state.status.value},
                run_id=spec.dag_id,
            )
            # #endregion
            continue
        if node_state.status is DagNodeStatus.FAILED:
            # #region agent log
            write_cursor_debug_ndjson(
                location="orchestrator.py:execute_dag:skip",
                message="node_skip_stale_failed",
                hypothesis_id="H2",
                data={"node_id": node_id, "status": node_state.status.value},
                run_id=spec.dag_id,
            )
            # #endregion
            continue
        if node_state.status is not DagNodeStatus.PENDING:
            # #region agent log
            write_cursor_debug_ndjson(
                location="orchestrator.py:execute_dag:skip",
                message="node_skip_non_pending",
                hypothesis_id="H2",
                data={"node_id": node_id, "status": node_state.status.value},
                run_id=spec.dag_id,
            )
            # #endregion
            continue

        preds = predecessors.get(node_id, set())
        if any(node_states[p].status is DagNodeStatus.FAILED for p in preds):
            node_state.status = DagNodeStatus.FAILED
            failed_nodes.append(node_id)
            continue
        if any(node_states[p].status is not DagNodeStatus.PASSED_VERIFICATION for p in preds):
            # Upstream still running, pending, or awaiting human sign-off — do not fail this node yet.
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
            trace_id=trace_id,
            dag_id=spec.dag_id,
        )

        # Persist RUNNING before the verification loop: the loop may run for a long time
        # (judge/coder retries) and the UI polls ``*_state.json``.
        persist_dag_execution_state(spec.dag_id, node_states)

        cp_status: Optional[VerificationStatus] = None
        try:
            final_state = run_verification_loop(
                node_spec.task,
                trace_id=trace_id,
                dag_id=spec.dag_id,
            )
            cp = final_state.checkpoint if getattr(final_state, "checkpoint", None) else None
            cp_status = getattr(cp, "status", None)
            node_state.verification_status = cp_status

            if cp_status is VerificationStatus.PASSED:
                node_state.status = DagNodeStatus.PASSED_VERIFICATION
            elif cp_status is VerificationStatus.PASSED_PENDING_SIGNOFF:
                node_state.status = DagNodeStatus.AWAITING_SIGNOFF
                node_state.verification_status = VerificationStatus.PASSED_PENDING_SIGNOFF
                paused_for_signoff = True
                log_event(
                    run_id=spec.dag_id,
                    hypothesis_id=node_id,
                    location="agenti_helix/orchestration/orchestrator.py:execute_dag",
                    message="DAG paused — judge-approved patch staged; awaiting manual sign-off before downstream nodes",
                    data={"node_id": node_id, "task_id": node_spec.task.task_id},
                    trace_id=trace_id,
                    dag_id=spec.dag_id,
                )
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
            trace_id=trace_id,
            dag_id=spec.dag_id,
        )

        persist_dag_execution_state(spec.dag_id, node_states)

        if paused_for_signoff:
            break

    log_event(
        run_id=spec.dag_id,
        hypothesis_id="dag_end",
        location="agenti_helix/orchestration/orchestrator.py:execute_dag",
        message="Finished DAG execution",
        data={"dag_id": spec.dag_id, "failed_nodes": failed_nodes, "trace_id": trace_id},
        trace_id=trace_id,
        dag_id=spec.dag_id,
    )

    return DagExecutionResult(
        dag_id=spec.dag_id,
        node_states=node_states,
        failed_nodes=failed_nodes,
        paused_for_signoff=paused_for_signoff,
    )


def load_dag_spec(dag_id: str) -> DagSpec:
    """
    Load a persisted DAG spec JSON file into a typed `DagSpec`.

    This is used by the control-plane when users update node execution
    configuration (e.g. custom coder/judge chains).
    """
    path = PATHS.dags_dir / f"{dag_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))

    macro_intent = str(raw.get("macro_intent") or "")
    dag_identifier = str(raw.get("dag_id") or dag_id)

    nodes_out: Dict[str, DagNodeSpec] = {}
    nodes_raw = raw.get("nodes") or {}
    if isinstance(nodes_raw, dict):
        for node_key, node_data_any in nodes_raw.items():
            if not isinstance(node_data_any, dict):
                continue
            task_data = node_data_any.get("task") or {}
            if not isinstance(task_data, dict):
                continue

            node_id = str(node_data_any.get("node_id") or node_key)
            description = str(node_data_any.get("description") or "")
            task = EditTaskSpec(**task_data)
            nodes_out[str(node_id)] = DagNodeSpec(node_id=node_id, description=description, task=task)

    edges_out: List[Tuple[str, str]] = []
    edges_raw = raw.get("edges") or []
    if isinstance(edges_raw, list):
        for e in edges_raw:
            if isinstance(e, (list, tuple)) and len(e) == 2:
                edges_out.append((str(e[0]), str(e[1])))

    return DagSpec(dag_id=dag_identifier, macro_intent=macro_intent, nodes=nodes_out, edges=edges_out)

