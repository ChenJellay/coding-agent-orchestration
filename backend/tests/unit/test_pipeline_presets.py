from __future__ import annotations

import pytest

from agenti_helix.agents.registry import get_agent, list_agents
from agenti_helix.orchestration.master_orchestrator import resolve_coder_chain, resolve_judge_chain
from agenti_helix.runtime.chain_runtime import run_chain
from agenti_helix.runtime.pipeline_presets import PIPELINE_MODES, resolve_preset_chains
from agenti_helix.verification.checkpointing import EditTaskSpec


def _minimal_task(pipeline_mode: str) -> EditTaskSpec:
    return EditTaskSpec(
        task_id="unit-pipeline",
        intent="test intent",
        target_file="src/x.js",
        acceptance_criteria="works",
        repo_path="/tmp/repo",
        pipeline_mode=pipeline_mode,
    )


@pytest.mark.parametrize(
    "mode",
    sorted(PIPELINE_MODES),
)
def test_resolve_chains_non_empty(mode: str) -> None:
    task = _minimal_task(mode)
    c = resolve_coder_chain(task)
    j = resolve_judge_chain(task)
    assert isinstance(c.get("steps"), list) and len(c["steps"]) >= 1
    assert isinstance(j.get("steps"), list) and len(j["steps"]) >= 1


def test_product_eng_skip_doc_chain_prefix_omits_doc_fetcher() -> None:
    task = _minimal_task("product_eng")
    task.skip_doc_chain_prefix = True
    preset = resolve_preset_chains(task)
    assert preset is not None
    coder, _judge = preset
    step_ids = [s.get("id") for s in coder.get("steps") or []]
    assert "fetch_doc" not in step_ids
    assert "doc_fetcher" not in step_ids
    assert "merge_doc_intent" not in step_ids


def test_named_presets_match_registry_agents() -> None:
    """Every agent_id referenced in preset chains must be registered."""
    agent_ids = {a["id"] for a in list_agents()}
    for mode in ("product_eng", "diff_guard_patch", "secure_build_plus", "lint_type_gate"):
        task = _minimal_task(mode)
        preset = resolve_preset_chains(task)
        assert preset is not None
        for chain in preset:
            for step in chain.get("steps") or []:
                if step.get("type") == "agent":
                    aid = step.get("agent_id")
                    assert isinstance(aid, str) and aid in agent_ids, f"missing agent {aid}"


def test_chain_skip_if_nonempty_key() -> None:
    chain = {
        "steps": [
            {
                "type": "tool",
                "id": "set_flag",
                "output_key": "gate",
                "tool_name": "map_evaluator_verdict",
                "input_bindings": {
                    "pass_tests": True,
                    "evaluation_reasoning": "x",
                    "feedback_for_coder": "",
                    "is_safe": True,
                    "violations": [],
                },
            },
            {
                "type": "tool",
                "id": "second",
                "output_key": "later",
                "tool_name": "map_evaluator_verdict",
                "skip_if_nonempty_key": "gate",
                "input_bindings": {
                    "pass_tests": False,
                    "evaluation_reasoning": "should not run",
                    "feedback_for_coder": "",
                    "is_safe": True,
                    "violations": [],
                },
            },
        ]
    }
    ctx = run_chain(
        chain_spec=chain,
        initial_context={},
        cancel_token=None,
        run_id="t",
        hypothesis_id="h",
        location_prefix="test",
    )
    assert "later" not in ctx


def test_new_roster_agents_registered() -> None:
    for aid in ("doc_fetcher_v1", "diff_validator_v1", "linter_v1", "type_checker_v1"):
        get_agent(aid)
