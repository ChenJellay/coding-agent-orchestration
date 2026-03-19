from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agenti_helix.agents.registry import list_agents


@dataclass(frozen=True)
class HelixPaths:
    repo_root: Path

    @property
    def agenti_root(self) -> Path:
        return self.repo_root / ".agenti_helix"

    @property
    def dags_dir(self) -> Path:
        return self.agenti_root / "dags"

    @property
    def checkpoints_dir(self) -> Path:
        return self.agenti_root / "checkpoints"

    @property
    def logs_dir(self) -> Path:
        return self.agenti_root / "logs"

    @property
    def events_path(self) -> Path:
        return self.logs_dir / "events.jsonl"

    @property
    def rules_path(self) -> Path:
        return self.agenti_root / "rules.json"

    @property
    def repo_map_path(self) -> Path:
        return self.repo_root / "repo_map.json"


def _repo_root_from_env() -> Path:
    return Path(os.environ.get("AGENTI_HELIX_REPO_ROOT", Path(".").resolve())).resolve()


PATHS = HelixPaths(repo_root=_repo_root_from_env())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _try_read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    return _read_json(path)


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return ()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _list_dag_ids() -> List[str]:
    if not PATHS.dags_dir.exists():
        return []
    ids: List[str] = []
    for p in PATHS.dags_dir.glob("*.json"):
        name = p.stem
        if name.endswith("_state"):
            continue
        ids.append(name)
    return sorted(set(ids))


def _load_dag_spec(dag_id: str) -> Dict[str, Any]:
    path = PATHS.dags_dir / f"{dag_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="DAG not found")
    return _read_json(path)


def _load_dag_state(dag_id: str) -> Optional[Dict[str, Any]]:
    path = PATHS.dags_dir / f"{dag_id}_state.json"
    return _try_read_json(path)


def _list_checkpoints() -> List[Dict[str, Any]]:
    if not PATHS.checkpoints_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    for p in PATHS.checkpoints_dir.glob("*.json"):
        try:
            raw = _read_json(p)
            items.append(raw)
        except Exception:
            continue
    items.sort(key=lambda x: (x.get("updated_at", 0), x.get("created_at", 0)), reverse=True)
    return items


FeatureColumn = Literal["SCOPING", "ORCHESTRATING", "BLOCKED", "VERIFYING", "READY_FOR_REVIEW"]


def _node_status_counts(state: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not state:
        return {}
    nodes = (state.get("nodes") or {}) if isinstance(state, dict) else {}
    counts: Dict[str, int] = {}
    for _, node in nodes.items():
        status = None
        if isinstance(node, dict):
            status = node.get("status")
        if not status:
            continue
        counts[str(status)] = counts.get(str(status), 0) + 1
    return counts


def _feature_column_from_state(
    dag_id: str,
    spec: Optional[Dict[str, Any]],
    state: Optional[Dict[str, Any]],
    events: List[Dict[str, Any]],
) -> FeatureColumn:
    if spec is None:
        return "SCOPING"

    if state is None:
        return "ORCHESTRATING"

    nodes = state.get("nodes") if isinstance(state, dict) else None
    if not isinstance(nodes, dict) or not nodes:
        return "ORCHESTRATING"

    statuses = [n.get("status") for n in nodes.values() if isinstance(n, dict)]
    statuses = [s for s in statuses if isinstance(s, str)]

    if any(s in {"FAILED", "ESCALATED"} for s in statuses):
        return "BLOCKED"

    if any((isinstance(n, dict) and n.get("verification_status") == "BLOCKED") for n in nodes.values()):
        return "BLOCKED"

    judge_msgs = {
        "Judge evaluated edit",
        "Marked checkpoint PASSED",
        "Rolled back and scheduled retry",
        "Marked checkpoint BLOCKED (retries exhausted)",
    }
    if any(e.get("runId") == dag_id and e.get("message") in judge_msgs for e in events):
        if any(s in {"RUNNING"} for s in statuses):
            return "VERIFYING"

    if statuses and all(s == "PASSED_VERIFICATION" for s in statuses):
        return "READY_FOR_REVIEW"

    return "ORCHESTRATING"


def _confidence_score(counts: Dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    passed = counts.get("PASSED_VERIFICATION", 0)
    failed = counts.get("FAILED", 0)
    base = passed / total
    penalty = 0.35 if failed > 0 else 0.0
    return max(0.0, min(1.0, base - penalty))


def _eta_seconds(counts: Dict[str, int]) -> Optional[int]:
    total = sum(counts.values())
    if total <= 0:
        return None
    passed = counts.get("PASSED_VERIFICATION", 0)
    remaining = max(0, total - passed)
    return remaining * 90


def _derive_features(limit: int = 200) -> List[Dict[str, Any]]:
    dag_ids = _list_dag_ids()[:limit]
    events = list(_iter_jsonl(PATHS.events_path))

    features: List[Dict[str, Any]] = []
    for dag_id in dag_ids:
        spec = _try_read_json(PATHS.dags_dir / f"{dag_id}.json")
        state = _load_dag_state(dag_id)
        counts = _node_status_counts(state)
        column = _feature_column_from_state(dag_id, spec, state, events)
        macro_intent = (spec or {}).get("macro_intent")

        features.append(
            {
                "feature_id": dag_id,
                "dag_id": dag_id,
                "title": macro_intent or dag_id,
                "column": column,
                "node_status_counts": counts,
                "confidence": _confidence_score(counts),
                "eta_seconds": _eta_seconds(counts),
                "has_state": state is not None,
            }
        )

    col_order = {
        "SCOPING": 0,
        "ORCHESTRATING": 1,
        "BLOCKED": 2,
        "VERIFYING": 3,
        "READY_FOR_REVIEW": 4,
    }

    def _state_mtime(dag_id: str) -> float:
        p = PATHS.dags_dir / f"{dag_id}_state.json"
        if not p.exists():
            p = PATHS.dags_dir / f"{dag_id}.json"
        try:
            return p.stat().st_mtime
        except Exception:
            return 0.0

    features.sort(key=lambda f: (col_order.get(f["column"], 999), -_state_mtime(str(f["dag_id"]))),)
    return features


def _derive_triage(limit: int = 200) -> List[Dict[str, Any]]:
    features = _derive_features(limit=limit)
    blocked = [f for f in features if f["column"] == "BLOCKED"]
    items: List[Dict[str, Any]] = []

    events = list(_iter_jsonl(PATHS.events_path))
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        rid = e.get("runId")
        if not isinstance(rid, str):
            continue
        by_run.setdefault(rid, []).append(e)
    for rid in by_run:
        by_run[rid].sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    for f in blocked:
        rid = str(f["dag_id"])
        latest = (by_run.get(rid) or [])[:25]
        latest_msg = next((e for e in latest if isinstance(e.get("message"), str)), None)
        items.append(
            {
                "feature_id": f["feature_id"],
                "dag_id": f["dag_id"],
                "title": f["title"],
                "severity": "HIGH",
                "summary": latest_msg.get("message") if latest_msg else "Blocked (details unavailable)",
                "timestamp": latest_msg.get("timestamp") if latest_msg else None,
            }
        )
    items.sort(key=lambda x: (x.get("timestamp") or 0), reverse=True)
    return items


def create_app() -> FastAPI:
    app = FastAPI(title="Agenti-Helix API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "repo_root": str(PATHS.repo_root)}

    @app.get("/api/dags")
    def list_dags() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for dag_id in _list_dag_ids():
            spec = _try_read_json(PATHS.dags_dir / f"{dag_id}.json") or {}
            p = PATHS.dags_dir / f"{dag_id}.json"
            mtime = int(p.stat().st_mtime * 1000) if p.exists() else None
            out.append({"dag_id": dag_id, "macro_intent": spec.get("macro_intent"), "mtime": mtime})
        return out

    @app.get("/api/dags/{dag_id}")
    def get_dag(dag_id: str) -> Dict[str, Any]:
        return _load_dag_spec(dag_id)

    @app.get("/api/dags/{dag_id}/state")
    def get_dag_state(dag_id: str) -> Dict[str, Any]:
        state = _load_dag_state(dag_id)
        if state is None:
            raise HTTPException(status_code=404, detail="DAG state not found")
        return state

    @app.get("/api/events")
    def get_events(
        runId: Optional[str] = None,
        hypothesisId: Optional[str] = None,
        sinceTs: Optional[int] = None,
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for e in _iter_jsonl(PATHS.events_path):
            if runId is not None and e.get("runId") != runId:
                continue
            if hypothesisId is not None and e.get("hypothesisId") != hypothesisId:
                continue
            ts = e.get("timestamp")
            if sinceTs is not None and isinstance(ts, int) and ts < sinceTs:
                continue
            items.append(e)
        items.sort(key=lambda x: x.get("timestamp", 0))
        return items[-limit:]

    @app.get("/api/checkpoints")
    def get_checkpoints(task_id: Optional[str] = None, limit: int = Query(default=200, ge=1, le=2000)) -> List[Dict[str, Any]]:
        items = _list_checkpoints()
        if task_id is not None:
            items = [c for c in items if c.get("task_id") == task_id]
        return items[:limit]

    @app.get("/api/checkpoints/{checkpoint_id}")
    def get_checkpoint(checkpoint_id: str) -> Dict[str, Any]:
        path = PATHS.checkpoints_dir / f"{checkpoint_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Checkpoint not found")
        return _read_json(path)

    @app.get("/api/features")
    def get_features(limit: int = Query(default=200, ge=1, le=2000)) -> List[Dict[str, Any]]:
        return _derive_features(limit=limit)

    @app.get("/api/features/{feature_id}")
    def get_feature(feature_id: str) -> Dict[str, Any]:
        spec = _try_read_json(PATHS.dags_dir / f"{feature_id}.json")
        if spec is None:
            raise HTTPException(status_code=404, detail="Feature (DAG) not found")
        state = _load_dag_state(feature_id)
        counts = _node_status_counts(state)
        events = list(_iter_jsonl(PATHS.events_path))
        return {
            "feature_id": feature_id,
            "dag": spec,
            "state": state,
            "metrics": {
                "node_status_counts": counts,
                "confidence": _confidence_score(counts),
                "eta_seconds": _eta_seconds(counts),
                "column": _feature_column_from_state(feature_id, spec, state, events),
            },
        }

    @app.get("/api/triage")
    def get_triage(limit: int = Query(default=200, ge=1, le=2000)) -> Dict[str, Any]:
        return {"items": _derive_triage(limit=limit)}

    @app.get("/api/agents")
    def get_agents() -> Dict[str, Any]:
        return {"agents": list_agents()}

    @app.get("/api/compute")
    def get_compute() -> Dict[str, Any]:
        events = list(_iter_jsonl(PATHS.events_path))
        return {"event_count": len(events)}

    @app.get("/api/repo-map")
    def get_repo_map() -> JSONResponse:
        candidates = [
            PATHS.repo_root / "repo_map.json",
            PATHS.repo_root / "repo_map.jsonl",
            PATHS.repo_root / ".agenti_helix" / "repo_map.json",
        ]
        for p in candidates:
            if p.exists():
                return JSONResponse(
                    content={
                        "path": str(p),
                        "format": "jsonl" if p.suffix == ".jsonl" else "json",
                        "content": p.read_text(encoding="utf-8"),
                    }
                )
        raise HTTPException(status_code=404, detail="Repo map not found")

    @app.get("/api/rules")
    def get_rules() -> Dict[str, Any]:
        rules = _try_read_json(PATHS.rules_path)
        return rules if isinstance(rules, dict) else {"rules": []}

    return app


app = create_app()

