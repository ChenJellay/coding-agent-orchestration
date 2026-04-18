from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Literal


JobStatus = Literal["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus = "RUNNING"
    cancel_token: CancelToken = field(default_factory=CancelToken)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class TaskCancelledError(RuntimeError):
    pass


_JOBS: Dict[str, JobRecord] = {}
_JOB_INDEX_BY_TASK_KEY: Dict[str, str] = {}
_LOCK = threading.Lock()

# region agent log
def _debug_write(payload: Dict[str, Any]) -> None:
    try:
        import json as _json
        from pathlib import Path as _Path

        p = _Path(__file__).resolve().parents[3] / ".cursor" / "debug-a3db40.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("a", encoding="utf-8").write(_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return
# endregion agent log


def create_job(*, meta: Optional[Dict[str, Any]] = None) -> JobRecord:
    job_id = f"job_{uuid.uuid4().hex}"
    rec = JobRecord(job_id=job_id, meta=meta or {})
    with _LOCK:
        _JOBS[job_id] = rec
    return rec


def get_job(job_id: str) -> Optional[JobRecord]:
    with _LOCK:
        return _JOBS.get(job_id)


def cancel_job(job_id: str) -> bool:
    with _LOCK:
        rec = _JOBS.get(job_id)
        if not rec:
            return False
        rec.cancel_token.cancel()
        return True


def _mark_finished(job: JobRecord, *, status: JobStatus, error: Optional[str] = None) -> None:
    with _LOCK:
        job.status = status
        job.finished_at = time.time()
        job.error = error


def start_background_job(
    *,
    meta: Optional[Dict[str, Any]],
    target: Callable[[CancelToken], Any],
    task_key: Optional[str] = None,
) -> JobRecord:
    """
    Start `target(cancel_token)` in a background thread and update JobRecord status.
    """
    rec = create_job(meta=meta)
    if task_key:
        with _LOCK:
            _JOB_INDEX_BY_TASK_KEY[task_key] = rec.job_id

    def _runner() -> None:
        try:
            # region agent log
            _debug_write(
                {
                    "sessionId": "a3db40",
                    "runId": "pre-fix",
                    "hypothesisId": "H6",
                    "location": "agenti_helix/api/job_registry.py:start_background_job",
                    "message": "Background job runner started",
                    "data": {"job_id": rec.job_id, "task_key": task_key, "meta": rec.meta},
                    "timestamp": int(time.time() * 1000),
                }
            )
            # endregion agent log
            target(rec.cancel_token)
        except TaskCancelledError:
            _mark_finished(rec, status="CANCELLED")
        except Exception as exc:
            _mark_finished(rec, status="FAILED", error=str(exc))
        else:
            _mark_finished(rec, status="SUCCEEDED")
        finally:
            # region agent log
            _debug_write(
                {
                    "sessionId": "a3db40",
                    "runId": "pre-fix",
                    "hypothesisId": "H7",
                    "location": "agenti_helix/api/job_registry.py:start_background_job",
                    "message": "Background job runner finished",
                    "data": {"job_id": rec.job_id, "status": rec.status, "error": rec.error},
                    "timestamp": int(time.time() * 1000),
                }
            )
            # endregion agent log

        if task_key:
            with _LOCK:
                # Only clear if the index still points at this job.
                if _JOB_INDEX_BY_TASK_KEY.get(task_key) == rec.job_id:
                    _JOB_INDEX_BY_TASK_KEY.pop(task_key, None)

    t = threading.Thread(target=_runner, name=f"agenti_job_{rec.job_id}", daemon=True)
    t.start()
    return rec


def _task_key(dag_id: str, node_id: str, task_id: str) -> str:
    return f"{dag_id}|{node_id}|{task_id}"


def cancel_running_job_for_task(*, dag_id: str, node_id: str, task_id: str) -> bool:
    key = _task_key(dag_id, node_id, task_id)
    with _LOCK:
        job_id = _JOB_INDEX_BY_TASK_KEY.get(key)
        if not job_id:
            return False
        rec = _JOBS.get(job_id)
        if not rec:
            _JOB_INDEX_BY_TASK_KEY.pop(key, None)
            return False
        rec.cancel_token.cancel()
        return True


