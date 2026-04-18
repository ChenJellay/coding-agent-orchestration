from __future__ import annotations

from agenti_helix.agents.registry import get_agent
from agenti_helix.orchestration.intent_compiler import IntentNodeSpec
from agenti_helix.orchestration.master_orchestrator import resolve_coder_chain, resolve_judge_chain
from agenti_helix.runtime.pipeline_presets import PIPELINE_PRESETS
from agenti_helix.runtime.tools import TOOL_REGISTRY
from agenti_helix.verification.checkpointing import EditTaskSpec


def _task_for_mode(pipeline_mode: str) -> EditTaskSpec:
    return EditTaskSpec(
        task_id=f"task-{pipeline_mode}",
        intent="Implement the requested change.",
        target_file="src/example.ts",
        acceptance_criteria="The change behaves as requested.",
        repo_path="/tmp/repo",
        pipeline_mode=pipeline_mode,
    )


def _assert_chain_is_valid(chain: dict) -> None:
    steps = chain.get("steps")
    assert isinstance(steps, list)
    assert steps
    for step in steps:
        assert isinstance(step, dict)
        if step.get("type") == "tool":
            assert step["tool_name"] in TOOL_REGISTRY
        elif step.get("type") == "agent":
            get_agent(step["agent_id"])
        else:
            raise AssertionError(f"Unexpected step type: {step!r}")


def test_pipeline_presets_resolve_to_nonempty_valid_chains() -> None:
    for pipeline_mode in PIPELINE_PRESETS:
        task = _task_for_mode(pipeline_mode)
        coder_chain = resolve_coder_chain(task)
        judge_chain = resolve_judge_chain(task)
        _assert_chain_is_valid(coder_chain)
        _assert_chain_is_valid(judge_chain)


def test_pipeline_presets_only_reference_registered_agents() -> None:
    for workflow in PIPELINE_PRESETS.values():
        for agent_id in workflow:
            get_agent(agent_id)


def test_intent_node_spec_accepts_named_pipeline_preset() -> None:
    spec = IntentNodeSpec(
        node_id="N1",
        description="Use a named preset",
        target_file="src/example.ts",
        acceptance_criteria="Works",
        pipeline_mode="product_eng",
    )

    assert spec.pipeline_mode == "product_eng"
