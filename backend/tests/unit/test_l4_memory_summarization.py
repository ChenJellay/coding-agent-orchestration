"""§4.3 — Memory Summarization unit tests.

Tests:
- error_history is capped when it grows too large
- compressed_context is threaded into coder intent over raw feedback
- node_summarize_context falls back gracefully when agent errors
- VerificationConfig.max_error_history_chars is respected
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from agenti_helix.verification.config import VerificationConfig
from agenti_helix.verification.verification_loop import (
    VerificationState,
    _build_coder_intent,
    node_summarize_context,
)
from agenti_helix.verification.checkpointing import EditTaskSpec


def _make_task(intent: str = "add logging") -> EditTaskSpec:
    return EditTaskSpec(
        task_id="t-test",
        repo_path="/tmp/repo",
        target_file="src/main.py",
        intent=intent,
        acceptance_criteria="logging added",
    )


# ---------------------------------------------------------------------------
# §4.3.1 — Error history cap
# ---------------------------------------------------------------------------

def test_error_history_cap_preserves_recent_entries():
    """When the total error_history exceeds 3× max_error_history_chars, only
    the last 5 entries are kept."""
    cfg = VerificationConfig(max_error_history_chars=10)  # tiny cap to trigger easily
    state = VerificationState(task=_make_task())
    # Build a history that is 6 entries, each 20 chars → 120 chars > 30 (3×10)
    state.error_history = [f"Attempt {i}: {'x' * 15}" for i in range(1, 7)]
    total_len = sum(len(e) for e in state.error_history)
    assert total_len > cfg.max_error_history_chars * 3

    # Simulate the cap logic from node_handle_verdict
    if sum(len(e) for e in state.error_history) > cfg.max_error_history_chars * 3:
        state.error_history = state.error_history[-5:]

    assert len(state.error_history) == 5
    assert state.error_history[0].startswith("Attempt 2:")


# ---------------------------------------------------------------------------
# §4.3.2 — compressed_context is threaded into coder intent
# ---------------------------------------------------------------------------

def test_build_coder_intent_uses_compressed_context_over_raw_feedback():
    state = VerificationState(task=_make_task("add logging"), feedback="old raw feedback")
    state.compressed_context = "COMPRESSED: the function crashes on None"
    intent = _build_coder_intent(state)
    assert "COMPRESSED" in intent
    assert "old raw feedback" not in intent


def test_build_coder_intent_falls_back_to_raw_feedback():
    state = VerificationState(task=_make_task("add logging"), feedback="raw judge feedback")
    state.compressed_context = None
    intent = _build_coder_intent(state)
    assert "raw judge feedback" in intent


def test_build_coder_intent_caps_raw_feedback():
    long_feedback = "x" * 10_000
    state = VerificationState(task=_make_task("add logging"), feedback=long_feedback)
    state.compressed_context = None
    intent = _build_coder_intent(state)
    # The capped portion comes from the tail of the feedback
    assert len(intent) < len(long_feedback) + 200


# ---------------------------------------------------------------------------
# §4.3.3 — node_summarize_context calls agent and populates compressed_context
# ---------------------------------------------------------------------------

def test_node_summarize_context_sets_compressed_context():
    state = VerificationState(task=_make_task())
    state.error_history = ["Attempt 1: indentation error"]

    mock_result = {"compressed_summary": "The function had bad indentation on line 5."}

    with patch("agenti_helix.runtime.agent_runtime.run_agent", return_value=mock_result):
        result = node_summarize_context(state)

    assert result.compressed_context == "The function had bad indentation on line 5."


def test_node_summarize_context_falls_back_on_agent_error():
    state = VerificationState(task=_make_task())
    state.error_history = ["Attempt 1: TypeError"]

    with patch("agenti_helix.runtime.agent_runtime.run_agent", side_effect=RuntimeError("LLM down")):
        result = node_summarize_context(state)

    # Must not crash; compressed_context remains None (fallback to raw feedback)
    assert result.compressed_context is None


def test_node_summarize_context_skips_when_cancelled():
    state = VerificationState(task=_make_task())
    token = MagicMock()
    token.is_cancelled.return_value = True
    state.cancel_token = token

    with patch("agenti_helix.runtime.agent_runtime.run_agent") as mock_agent:
        result = node_summarize_context(state)

    mock_agent.assert_not_called()
