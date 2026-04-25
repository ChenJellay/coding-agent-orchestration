"""Serialize agent runs that touch the same workspace path (DAG + single-task retries)."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

_registry_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _resolved_key(repo_path: str) -> str:
    return str(Path(repo_path).expanduser().resolve())


def _lock_for_key(key: str) -> threading.Lock:
    with _registry_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


@contextmanager
def hold_repo_execution_lock(repo_paths: Sequence[str]) -> Iterator[None]:
    """Acquire locks for all distinct resolved repo roots (sorted to avoid deadlock)."""
    keys = sorted({_resolved_key(p) for p in repo_paths if (p or "").strip()})
    if not keys:
        yield
        return
    acquired: list[threading.Lock] = []
    try:
        for k in keys:
            lk = _lock_for_key(k)
            lk.acquire()
            acquired.append(lk)
        yield
    finally:
        for lk in reversed(acquired):
            lk.release()
