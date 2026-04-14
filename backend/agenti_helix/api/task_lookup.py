from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from agenti_helix.api.paths import PATHS, iter_jsonl, read_json, try_read_json
from agenti_helix.verification.checkpointing import EditTaskSpec


@dataclass(frozen=True)
class TaskRef:
    dag_id: str
    node_id: str
    task: EditTaskSpec


def _iter_dag_spec_files() -> Iterator[Tuple[str, Path]]:
    if not PATHS.dags_dir.exists():
        return
    for p in PATHS.dags_dir.glob("*.json"):
        dag_id = p.stem
        if dag_id.endswith("_state"):
            continue
        yield dag_id, p


def iter_tasks() -> Iterator[TaskRef]:
    """
    Iterate all persisted DAG specs and yield (dag_id, node_id, EditTaskSpec).
    """
    for dag_id, spec_path in _iter_dag_spec_files():
        spec = read_json(spec_path)
        nodes = spec.get("nodes")
        if not isinstance(nodes, dict):
            continue
        for node_id, node_data in nodes.items():
            if not isinstance(node_data, dict):
                continue
            task_data = node_data.get("task")
            if not isinstance(task_data, dict):
                continue
            try:
                task = EditTaskSpec(**task_data)
            except Exception:
                continue
            yield TaskRef(dag_id=dag_id, node_id=str(node_id), task=task)


def find_task_ref(
    *,
    task_id: str,
    feature_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> TaskRef:
    """
    Find a task by its `task_id`.

    `feature_id` maps to `dag_id` in the current persistence format.
    """
    matches = []
    for ref in iter_tasks():
        if ref.task.task_id != task_id:
            continue
        if feature_id is not None and ref.dag_id != feature_id:
            continue
        if node_id is not None and ref.node_id != node_id:
            continue
        matches.append(ref)

    if not matches:
        raise KeyError(f"Unknown task_id={task_id!r}")
    if len(matches) > 1:
        # Prefer a unique match; ambiguous ids would be a persistence bug.
        raise RuntimeError(f"Ambiguous task_id={task_id!r} matched {len(matches)} tasks")
    return matches[0]


def dag_state_path(dag_id: str) -> Path:
    return PATHS.dags_dir / f"{dag_id}_state.json"


def try_load_dag_state(dag_id: str) -> Optional[Dict]:
    return try_read_json(dag_state_path(dag_id))


def load_dag_state(dag_id: str) -> Dict:
    state = try_load_dag_state(dag_id)
    if state is None:
        raise FileNotFoundError(f"DAG state not found for dag_id={dag_id!r}")
    return state


def persist_dag_state(dag_id: str, state: Dict) -> None:
    dag_state_path(dag_id).write_text(json.dumps(state, indent=2), encoding="utf-8")


def record_verification_cycle_snapshot(
    *,
    dag_id: Optional[str],
    task_id: str,
    verification_cycle: int,
    verification_status: Optional[str],
    code_evidence: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Merge verification-loop progress into ``{dag_id}_state.json`` while a node is still
    inside ``run_verification_loop`` (judge retries / coder re-attempts).

    Without this, the orchestrator only persists after the node completes, so the
    dashboard shows a frozen RUNNING node until the entire loop returns.
    """
    if not dag_id:
        return
    try:
        ref = find_task_ref(task_id=task_id, feature_id=dag_id)
    except (KeyError, RuntimeError):
        return
    node_id = ref.node_id
    cycle = max(1, int(verification_cycle))
    state = try_load_dag_state(dag_id)
    if not isinstance(state, dict):
        state = {"dag_id": dag_id, "nodes": {}}
    nodes = state.setdefault("nodes", {})
    if not isinstance(nodes, dict):
        nodes = {}
        state["nodes"] = nodes
    ns = nodes.get(node_id)
    if not isinstance(ns, dict):
        ns = {"node_id": node_id, "status": "RUNNING", "attempts": 1, "verification_status": None}
        nodes[node_id] = ns
    ns["verification_cycle"] = cycle
    if verification_status is not None:
        ns["verification_status"] = verification_status
    if code_evidence:
        ev = ns.get("code_evidence")
        if not isinstance(ev, dict):
            ev = {}
        merged = {**ev, **code_evidence}
        ns["code_evidence"] = merged
    persist_dag_state(dag_id, state)
    try:
        from agenti_helix.api.response_caches import invalidate_features_and_triage_caches

        invalidate_features_and_triage_caches()
    except Exception:
        pass

