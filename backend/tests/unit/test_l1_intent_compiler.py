"""
L1.2 / L1.3 — Intent compiler validation and retry tests.

Verifies that:
- compile_macro_intent_with_llm validates output against IntentCompilerOutput.
- It raises ValueError after retries on consistently bad output.
- compile_macro_intent_to_dag raises (not silently falls back) on LLM failure.
- _run_intent_chain feedback injection prepends correction instructions.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agenti_helix.orchestration.intent_compiler import (
    compile_macro_intent_with_llm,
    _run_intent_chain,
    _MAX_COMPILE_RETRIES,
)


# ---------------------------------------------------------------------------
# _run_intent_chain feedback injection
# ---------------------------------------------------------------------------

def test_run_intent_chain_injects_feedback(tmp_path):
    """When feedback is provided, it is prepended to the prompt intent."""
    captured_ctx = {}

    def fake_run_chain(*, chain_spec, initial_context, **kwargs):
        captured_ctx.update(initial_context)
        # Return a minimal valid output dict
        return {
            **initial_context,
            "intent_compiler_output": {
                "dag_id": "test",
                "nodes": [{"node_id": "N1", "description": "d", "target_file": "x.py", "acceptance_criteria": "ac"}],
                "edges": [],
            },
        }

    with patch("agenti_helix.orchestration.intent_compiler.run_chain", side_effect=fake_run_chain):
        _run_intent_chain("my intent", tmp_path, feedback="Fix the output format")

    assert "Fix the output format" in captured_ctx["macro_intent"]
    assert "my intent" in captured_ctx["macro_intent"]


def test_run_intent_chain_no_feedback(tmp_path):
    """Without feedback the prompt intent equals the original."""
    captured_ctx = {}

    def fake_run_chain(*, chain_spec, initial_context, **kwargs):
        captured_ctx.update(initial_context)
        return {**initial_context, "intent_compiler_output": {}}

    with patch("agenti_helix.orchestration.intent_compiler.run_chain", side_effect=fake_run_chain):
        _run_intent_chain("clean intent", tmp_path)

    assert captured_ctx["macro_intent"] == "clean intent"


# ---------------------------------------------------------------------------
# compile_macro_intent_with_llm retry + validation
# ---------------------------------------------------------------------------

def _valid_chain_output(tmp_path):
    """Return a fake run_chain that always yields a valid single-node DAG."""
    def fake(*, chain_spec, initial_context, **kwargs):
        return {
            **initial_context,
            "intent_compiler_output": {
                "dag_id": "dag-test",
                "nodes": [
                    {
                        "node_id": "N1",
                        "description": "Write a test",
                        "target_file": "src/test.py",
                        "acceptance_criteria": "tests pass",
                    }
                ],
                "edges": [],
            },
        }
    return fake


def test_compile_succeeds_on_first_attempt(tmp_path):
    """compile_macro_intent_with_llm returns a DagSpec on valid first output."""
    with patch("agenti_helix.orchestration.intent_compiler.run_chain", side_effect=_valid_chain_output(tmp_path)):
        spec = compile_macro_intent_with_llm("add a test", repo_path=str(tmp_path))

    assert spec.dag_id == "dag-test"
    assert "N1" in spec.nodes


def test_caller_dag_id_overrides_llm_dag_id(tmp_path):
    """Dashboard/CLI dag_id must not lose to an optional id emitted inside LLM JSON."""
    with patch("agenti_helix.orchestration.intent_compiler.run_chain", side_effect=_valid_chain_output(tmp_path)):
        spec = compile_macro_intent_with_llm(
            "add a test",
            repo_path=str(tmp_path),
            dag_id="dag-ui-run-fixed",
        )

    assert spec.dag_id == "dag-ui-run-fixed"
    assert spec.nodes["N1"].task.task_id.startswith("dag-ui-run-fixed:")


def test_compile_raises_after_max_retries_empty_nodes(tmp_path):
    """Should raise ValueError when all attempts return empty nodes."""
    def bad_chain(*, chain_spec, initial_context, **kwargs):
        return {
            **initial_context,
            "intent_compiler_output": {"dag_id": "x", "nodes": [], "edges": []},
        }

    with pytest.raises(ValueError, match="failed after"):
        with patch("agenti_helix.orchestration.intent_compiler.run_chain", side_effect=bad_chain):
            compile_macro_intent_with_llm("intent", repo_path=str(tmp_path))


def test_compile_raises_after_max_retries_invalid_output(tmp_path):
    """Should raise ValueError when all attempts fail schema validation."""
    def bad_chain(*, chain_spec, initial_context, **kwargs):
        # Missing required 'nodes' key
        return {**initial_context, "intent_compiler_output": {"dag_id": "x"}}

    with pytest.raises(ValueError, match="failed after"):
        with patch("agenti_helix.orchestration.intent_compiler.run_chain", side_effect=bad_chain):
            compile_macro_intent_with_llm("intent", repo_path=str(tmp_path))


def test_compile_retries_and_succeeds_on_second_attempt(tmp_path):
    """compile_macro_intent_with_llm retries and returns spec if second attempt succeeds."""
    call_count = [0]

    def mixed_chain(*, chain_spec, initial_context, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First attempt: bad output
            return {**initial_context, "intent_compiler_output": {"dag_id": "x", "nodes": [], "edges": []}}
        # Second attempt: valid
        return {
            **initial_context,
            "intent_compiler_output": {
                "dag_id": "dag-ok",
                "nodes": [{"node_id": "N1", "description": "d", "target_file": "f.py", "acceptance_criteria": "ac"}],
                "edges": [],
            },
        }

    with patch("agenti_helix.orchestration.intent_compiler.run_chain", side_effect=mixed_chain):
        spec = compile_macro_intent_with_llm("intent", repo_path=str(tmp_path))

    assert call_count[0] == 2
    assert "N1" in spec.nodes
