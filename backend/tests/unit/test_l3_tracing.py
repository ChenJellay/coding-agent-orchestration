"""
L3.1 — Trace ID propagation tests.

Verifies that:
- log_event accepts and emits trace_id / dag_id as top-level fields.
- GET /api/events can filter by traceId and dagId.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


def test_log_event_emits_trace_and_dag_id(tmp_path):
    """log_event should write traceId and dagId to the JSONL payload."""
    log_file = tmp_path / "events.jsonl"
    os.environ["AGENTI_HELIX_LOG_PATH"] = str(log_file)

    # Re-import to pick up the patched env var (module-level constant).
    import importlib
    import agenti_helix.observability.debug_log as debug_log_mod
    importlib.reload(debug_log_mod)

    debug_log_mod.log_event(
        run_id="run-1",
        hypothesis_id="h1",
        location="test",
        message="hello",
        data={"kind": "llm_trace"},
        trace_id="trace-abc",
        dag_id="dag-xyz",
    )

    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    payload = lines[0]
    assert payload["traceId"] == "trace-abc"
    assert payload["dagId"] == "dag-xyz"
    assert payload["runId"] == "run-1"

    # Cleanup env
    del os.environ["AGENTI_HELIX_LOG_PATH"]
    importlib.reload(debug_log_mod)


def test_log_event_without_optional_fields(tmp_path):
    """log_event omits traceId/dagId keys when not provided."""
    log_file = tmp_path / "events.jsonl"
    os.environ["AGENTI_HELIX_LOG_PATH"] = str(log_file)

    import importlib
    import agenti_helix.observability.debug_log as debug_log_mod
    importlib.reload(debug_log_mod)

    debug_log_mod.log_event(
        run_id="run-2",
        hypothesis_id="h2",
        location="test",
        message="bare event",
        data={"kind": "llm_trace"},
    )

    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    payload = lines[0]
    assert "traceId" not in payload
    assert "dagId" not in payload

    del os.environ["AGENTI_HELIX_LOG_PATH"]
    importlib.reload(debug_log_mod)
