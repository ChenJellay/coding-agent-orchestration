"""
L3.4 — TaskCancelledError wiring tests.

Verifies that:
- chain_runtime raises TaskCancelledError (not RuntimeError) on cancel.
- agent_runtime raises TaskCancelledError before inference when cancelled.
"""
from __future__ import annotations

import pytest

from agenti_helix.api.job_registry import CancelToken, TaskCancelledError


class _AlreadyCancelledToken(CancelToken):
    def is_cancelled(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# chain_runtime
# ---------------------------------------------------------------------------

def test_chain_runtime_raises_task_cancelled_error():
    """run_chain should raise TaskCancelledError, not RuntimeError, on cancel."""
    from agenti_helix.runtime.chain_runtime import run_chain

    token = _AlreadyCancelledToken()
    chain = {
        "steps": [
            {
                "type": "tool",
                "id": "dummy",
                "output_key": "out",
                "tool_name": "build_repo_map_context",
                "input_bindings": {"repo_root": {"$ref": "repo_root"}},
            }
        ]
    }
    with pytest.raises(TaskCancelledError):
        run_chain(
            chain_spec=chain,
            initial_context={"repo_root": "/tmp"},
            cancel_token=token,
            run_id="test",
            hypothesis_id="t1",
            location_prefix="test",
        )


# ---------------------------------------------------------------------------
# agent_runtime
# ---------------------------------------------------------------------------

def test_agent_runtime_raises_task_cancelled_error_before_inference():
    """run_agent should raise TaskCancelledError immediately when cancelled."""
    from agenti_helix.runtime.agent_runtime import run_agent

    token = _AlreadyCancelledToken()
    with pytest.raises(TaskCancelledError):
        run_agent(
            agent_id="coder_patch_v1",
            raw_input={"repo_map_json": "[]", "intent": "test"},
            cancel_token=token,
        )
