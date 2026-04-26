from __future__ import annotations

import json
import os
import importlib
from pathlib import Path


def test_log_event_persists_system_events_by_default(tmp_path, monkeypatch) -> None:
    # Ensure module uses our temp events file.
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("AGENTI_HELIX_LOG_PATH", str(log_path))
    monkeypatch.delenv("AGENTI_HELIX_LOG_LLM_ONLY", raising=False)

    import agenti_helix.observability.debug_log as dl

    importlib.reload(dl)

    dl.log_event(
        run_id="r1",
        hypothesis_id="h1",
        location="x",
        message="Starting DAG execution",
        data={"kind": "system"},
        trace_id="t1",
        dag_id="d1",
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["message"] == "Starting DAG execution"
    assert ev["runId"] == "r1"
    assert ev["dagId"] == "d1"


def test_log_event_llm_only_mode_skips_system_events(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("AGENTI_HELIX_LOG_PATH", str(log_path))
    monkeypatch.setenv("AGENTI_HELIX_LOG_LLM_ONLY", "true")

    import agenti_helix.observability.debug_log as dl

    importlib.reload(dl)

    dl.log_event(
        run_id="r2",
        hypothesis_id="h2",
        location="x",
        message="Static checks completed",
        data={"kind": "system"},
    )
    assert not log_path.exists() or log_path.read_text(encoding="utf-8").strip() == ""

    dl.log_event(
        run_id="r3",
        hypothesis_id="h3",
        location="x",
        message="LLM inference",
        data={"kind": "llm_trace", "agent_id": "x", "prompt": "p", "raw_output": "o"},
    )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["message"] == "LLM inference"
    assert ev["data"]["kind"] == "llm_trace"

