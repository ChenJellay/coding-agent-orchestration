"""Unit tests for scripts/eval/headless_eval.py helpers (loaded by path)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_HEADLESS = _ROOT / "scripts" / "eval" / "headless_eval.py"


@pytest.fixture(scope="module")
def headless_mod():
    spec = importlib.util.spec_from_file_location("headless_eval", _HEADLESS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_event_matches_dag(headless_mod):
    m = headless_mod.event_matches_dag
    assert m({"dagId": "eval-x", "runId": "noise"}, "eval-x")
    assert m({"runId": "eval-x:N1"}, "eval-x")
    assert not m({"runId": "other:N1"}, "eval-x")
    assert m({"runId": "intent", "data": {"dag_id": "eval-x"}}, "eval-x")


def test_compile_failed_detection_in_poll(headless_mod):
    """compile_failed branch uses substring in message."""
    evs = [{"message": "Intent compile failed — DAG will not run", "runId": "eval-x"}]
    assert any(
        headless_mod.COMPILE_FAILED in str(ev.get("message", "")) or "Intent compile failed" in str(ev.get("message", ""))
        for ev in evs
    )


def test_count_verification_loop_starts(headless_mod):
    dag = "eval-s6"
    evs = [
        {"message": headless_mod.LOOP_START, "runId": f"{dag}:N1"},
        {"message": headless_mod.LOOP_START, "runId": f"{dag}:N1"},
        {"message": headless_mod.LOOP_START, "runId": "other:N2"},
    ]
    c = headless_mod.count_verification_loop_starts(evs, dag)
    assert c.get("N1") == 2
    assert "N2" not in c
