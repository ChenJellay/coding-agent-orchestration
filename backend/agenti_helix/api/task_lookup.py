from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

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

