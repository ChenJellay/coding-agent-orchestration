from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Literal, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agenti_helix.agents.registry import get_agent_detail, list_agents
from agenti_helix.api.auth import Role, require_auth, require_editor
from agenti_helix.api.response_caches import (
    CACHE_AVAILABLE as _CACHE_AVAILABLE,
    FEATURES_CACHE as _FEATURES_CACHE,
    TRIAGE_CACHE as _TRIAGE_CACHE,
    invalidate_features_and_triage_caches,
)
from agenti_helix.api.task_commands_routes import router as task_commands_router
from agenti_helix.core.repo_map import generate_repo_map


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


FeatureColumn = Literal[
    "SCOPING",
    "ORCHESTRATING",
    "BLOCKED",
    "VERIFYING",
    "READY_FOR_REVIEW",
    "SUCCESSFUL_COMMIT",
]


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

    # If a merge has been performed for this feature, treat it as COMPLETE.
    # The merge endpoint logs: "Merged to main" (with optional simulated note).
    if any(
        (e.get("runId") == dag_id)
        and isinstance(e.get("message"), str)
        and str(e.get("message")).startswith("Merged to main")
        for e in events
    ):
        return "COMPLETE"

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
        "Judge PASS — staged post-state; workspace rolled back pending manual sign-off",
        "Rolled back and scheduled retry",
        "Marked checkpoint BLOCKED (retries exhausted)",
    }
    if any(e.get("runId") == dag_id and e.get("message") in judge_msgs for e in events):
        if any(s in {"RUNNING"} for s in statuses):
            return "VERIFYING"

    # All nodes passed verification (human sign-off applied for patch pipeline, or direct pass for build).
    if statuses and all(s == "PASSED_VERIFICATION" for s in statuses):
        return "SUCCESSFUL_COMMIT"

    # AWAITING_SIGNOFF = judge approved, workspace staged, waiting for human sign-off.
    if any(s == "AWAITING_SIGNOFF" for s in statuses):
        return "READY_FOR_REVIEW"

    return "ORCHESTRATING"


def _confidence_score(counts: Dict[str, int]) -> float:
    """Blend of DAG progress and outcomes (0–1).

    Previously this was ``PASSED_VERIFICATION / total``, which is **0** for most of a run because
    nodes spend time in ``RUNNING``, ``PENDING``, or ``AWAITING_SIGNOFF``. We now weight each
    status so in-flight work earns partial credit, while ``FAILED`` / ``ESCALATED`` pull the score down.
    """
    total = sum(counts.values())
    if total <= 0:
        return 0.42

    weights = {
        "PASSED_VERIFICATION": 1.0,
        "AWAITING_SIGNOFF": 0.9,
        "RUNNING": 0.48,
        "PENDING": 0.1,
        "FAILED": 0.0,
        "ESCALATED": 0.05,
    }
    weighted = 0.0
    for status, n in counts.items():
        key = str(status)
        w = weights.get(key, 0.2)
        weighted += n * w

    fail_n = counts.get("FAILED", 0) + counts.get("ESCALATED", 0)
    fail_penalty = min(0.5, 0.45 * (fail_n / total))

    score = (weighted / total) - fail_penalty
    return max(0.06, min(1.0, score))


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
        user_label = (spec or {}).get("user_intent_label") or ""

        features.append(
            {
                "feature_id": dag_id,
                "dag_id": dag_id,
                "title": (user_label or macro_intent or dag_id),
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
        "SUCCESSFUL_COMMIT": 5,
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
        # Try to surface the most relevant node/task pointer so the UI can deep-link.
        # Prefer a recent event that is tied to a specific node (hypothesisId == node_id).
        node_event = next((e for e in latest if isinstance(e.get("hypothesisId"), str) and e.get("hypothesisId")), None)
        node_id = str(node_event.get("hypothesisId")) if node_event else None
        task_id: Optional[str] = None
        data = node_event.get("data") if isinstance(node_event, dict) else None
        if isinstance(data, dict) and isinstance(data.get("task_id"), str):
            task_id = str(data.get("task_id"))
        items.append(
            {
                "feature_id": f["feature_id"],
                "dag_id": f["dag_id"],
                "title": f["title"],
                "severity": "HIGH",
                "summary": latest_msg.get("message") if latest_msg else "Blocked (details unavailable)",
                "timestamp": latest_msg.get("timestamp") if latest_msg else None,
                "node_id": node_id,
                "task_id": task_id,
            }
        )
    items.sort(key=lambda x: (x.get("timestamp") or 0), reverse=True)
    return items


def _validate_feature_id_param(feature_id: str) -> None:
    if not feature_id or not str(feature_id).strip():
        raise HTTPException(status_code=400, detail="Invalid feature id")
    if ".." in feature_id or "/" in feature_id or "\\" in feature_id:
        raise HTTPException(status_code=400, detail="Invalid feature id")


def _remove_dag_from_system(dag_id: str) -> None:
    """Remove persisted DAG spec/state and best-effort cleanup of checkpoints and merge records."""
    for suffix in (f"{dag_id}.json", f"{dag_id}_state.json"):
        p = PATHS.dags_dir / suffix
        if p.exists():
            p.unlink()

    task_prefix = f"{dag_id}:"
    cdir = PATHS.checkpoints_dir
    if cdir.exists():
        for p in cdir.glob("*.json"):
            try:
                data = _try_read_json(p)
                if isinstance(data, dict) and isinstance(data.get("task_id"), str):
                    if data["task_id"].startswith(task_prefix):
                        p.unlink()
            except OSError:
                continue

    merges = PATHS.agenti_root / "merges"
    if merges.exists():
        for p in merges.glob("*.json"):
            try:
                data = _try_read_json(p)
                if isinstance(data, dict) and data.get("dag_id") == dag_id:
                    p.unlink()
            except OSError:
                continue


def create_app() -> FastAPI:
    app = FastAPI(title="Agenti-Helix API", version="0.1.0")

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:  # type: ignore[name-defined]
        # Ensure consistent error shape for the frontend:
        #   { ok: false, error: { code, message } }
        if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": {"code": "http_error", "message": str(exc.detail)}},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        # D1: Include Authorization header in CORS to allow Bearer token from browser.
        allow_headers=["Content-Type", "Authorization"],
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
        traceId: Optional[str] = None,
        dagId: Optional[str] = None,
        sinceTs: Optional[int] = None,
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for e in _iter_jsonl(PATHS.events_path):
            if runId is not None and e.get("runId") != runId:
                continue
            if hypothesisId is not None and e.get("hypothesisId") != hypothesisId:
                continue
            if traceId is not None and e.get("traceId") != traceId:
                continue
            if dagId is not None and e.get("dagId") != dagId:
                continue
            ts = e.get("timestamp")
            if sinceTs is not None and isinstance(ts, int) and ts < sinceTs:
                continue
            items.append(e)
        items.sort(key=lambda x: x.get("timestamp", 0))
        return items[-limit:]

    def _event_key(e: Dict[str, Any]) -> str:
        ts = e.get("timestamp") or 0
        rid = e.get("runId") or ""
        msg = e.get("message") or ""
        agent = ""
        data = e.get("data")
        if isinstance(data, dict):
            agent = str(data.get("agent_id") or "")
        return f"{ts}:{rid}:{msg}:{agent}"

    @app.get("/api/events/stream")
    def stream_events(
        runId: Optional[str] = None,
        hypothesisId: Optional[str] = None,
        traceId: Optional[str] = None,
        dagId: Optional[str] = None,
        sinceTs: Optional[int] = None,
        heartbeatSeconds: float = Query(default=15.0, ge=1.0, le=60.0),
    ) -> StreamingResponse:
        """
        Server-sent events stream of events.jsonl.

        This is intentionally "best-effort" (polls the file periodically) to
        provide near-real-time UI updates without adding a persistent queue.
        """

        def gen() -> Iterator[bytes]:
            last_heartbeat = time.time()
            last_seen_ts = sinceTs if sinceTs is not None else None
            # Keep a small sliding window to dedupe repeats across polls.
            recent_keys: List[str] = []

            # Initial hello so the client knows the stream is alive.
            yield b": ok\n\n"

            while True:
                now = time.time()
                if now - last_heartbeat >= heartbeatSeconds:
                    last_heartbeat = now
                    yield b": heartbeat\n\n"

                batch: List[Dict[str, Any]] = []
                for e in _iter_jsonl(PATHS.events_path):
                    if runId is not None and e.get("runId") != runId:
                        continue
                    if hypothesisId is not None and e.get("hypothesisId") != hypothesisId:
                        continue
                    if traceId is not None and e.get("traceId") != traceId:
                        continue
                    if dagId is not None and e.get("dagId") != dagId:
                        continue
                    ts = e.get("timestamp")
                    if last_seen_ts is not None and isinstance(ts, int) and ts < last_seen_ts:
                        continue
                    batch.append(e)

                batch.sort(key=lambda x: x.get("timestamp", 0))

                for e in batch[-500:]:
                    k = _event_key(e)
                    if k in recent_keys:
                        continue
                    recent_keys.append(k)
                    if len(recent_keys) > 250:
                        recent_keys = recent_keys[-250:]

                    ts = e.get("timestamp")
                    if isinstance(ts, int):
                        # Move the cursor forward; allow equal timestamps to still stream via dedupe.
                        last_seen_ts = ts

                    payload = json.dumps(e, ensure_ascii=False)
                    yield f"event: event\ndata: {payload}\n\n".encode("utf-8")

                time.sleep(0.8)

        return StreamingResponse(gen(), media_type="text/event-stream")

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
        # D5: 5-second TTL cache reduces disk I/O under polling load.
        cache_key = f"features:{limit}"
        if _CACHE_AVAILABLE and cache_key in _FEATURES_CACHE:
            return _FEATURES_CACHE[cache_key]  # type: ignore[index]
        result = _derive_features(limit=limit)
        if _CACHE_AVAILABLE:
            _FEATURES_CACHE[cache_key] = result  # type: ignore[index]
        return result

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

    @app.delete("/api/features/{feature_id}")
    def delete_feature(feature_id: str, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
        """Remove DAG spec, execution state, and related checkpoints/merge records from disk."""
        _validate_feature_id_param(feature_id)
        spec_path = PATHS.dags_dir / f"{feature_id}.json"
        state_path = PATHS.dags_dir / f"{feature_id}_state.json"
        if not spec_path.exists() and not state_path.exists():
            raise HTTPException(status_code=404, detail="Feature not found")
        _remove_dag_from_system(feature_id)
        invalidate_features_and_triage_caches()
        return {"ok": True}

    @app.get("/api/triage")
    def get_triage(limit: int = Query(default=200, ge=1, le=2000)) -> Dict[str, Any]:
        # D5: 5-second TTL cache to reduce disk I/O under polling load.
        cache_key = f"triage:{limit}"
        if _CACHE_AVAILABLE and cache_key in _TRIAGE_CACHE:
            return {"items": _TRIAGE_CACHE[cache_key]}  # type: ignore[index]
        items = _derive_triage(limit=limit)
        if _CACHE_AVAILABLE:
            _TRIAGE_CACHE[cache_key] = items  # type: ignore[index]
        return {"items": items}

    @app.get("/api/agents")
    def get_agents() -> Dict[str, Any]:
        return {"agents": list_agents()}

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str) -> Dict[str, Any]:
        try:
            return get_agent_detail(agent_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Agent not found")

    @app.put("/api/agents/{agent_id}/prompt")
    def update_agent_prompt_api(
        agent_id: str,
        body: Dict[str, Any],
        _role: Role = Depends(require_editor),  # D1: editor-only mutation
    ) -> Dict[str, Any]:
        prompt = body.get("prompt")
        if not isinstance(prompt, str):
            raise HTTPException(status_code=400, detail="Body must contain string field 'prompt'")
        from agenti_helix.agents.registry import update_agent_prompt as _update

        try:
            _update(agent_id, prompt)
        except KeyError:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"ok": True}

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
        live = generate_repo_map(PATHS.repo_root)
        return JSONResponse(
            content={
                "path": f"(generated live) {PATHS.repo_root}",
                "format": "json",
                "content": live.to_json(),
            }
        )

    @app.get("/api/rules")
    def get_rules() -> Dict[str, Any]:
        rules = _try_read_json(PATHS.rules_path)
        return rules if isinstance(rules, dict) else {"rules": []}

    @app.get("/api/blame")
    def get_blame(
        file: str = Query(..., description="Repo-relative file path to blame"),
        line: int = Query(..., ge=1, description="1-based line number"),
    ) -> Dict[str, Any]:
        """§4.6 — Semantic Git Blame.

        Returns the commit that last touched a given line and embeds any
        Trace-Id / Dag-Id trailers so the UI can link to the originating DAG.
        """
        from agenti_helix.api.git_ops import git_blame_line

        result = git_blame_line(
            repo_path=str(PATHS.repo_root),
            file_path=file,
            line=line,
        )

        if not result.get("found"):
            # Surface merge-record blame as a fallback when the file isn't git-tracked.
            merges_dir = PATHS.agenti_root / "merges"
            if merges_dir.exists():
                for merge_file in sorted(merges_dir.glob("*.json"), reverse=True):
                    try:
                        record = json.loads(merge_file.read_text(encoding="utf-8"))
                        diff_obj = json.loads(record.get("diff") or "{}")
                        if diff_obj.get("filePath") == file:
                            return {
                                "found": True,
                                "source": "merge_record",
                                "task_id": record.get("task_id"),
                                "dag_id": record.get("dag_id"),
                                "commit_sha": record.get("commit_sha"),
                                "commit_message": record.get("commit_message"),
                                "created_at": record.get("created_at"),
                            }
                    except Exception:
                        continue

        return result

    app.include_router(task_commands_router)
    return app


app = create_app()

