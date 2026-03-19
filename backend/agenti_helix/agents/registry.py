from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, List, Type

from pydantic import BaseModel

from . import models
from .render import load_prompt_template, render_judge_variables, render_prompt


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    description: str
    prompt_filename: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]

    def render(self, raw_input: Dict[str, Any]) -> str:
        inp = self.input_model.model_validate(raw_input)
        template = load_prompt_template(self.prompt_filename)

        if self.agent_id == "judge_v1":
            vars_dict = render_judge_variables(
                acceptance_criteria=getattr(inp, "acceptance_criteria"),
                original_snippet=getattr(inp, "original_snippet"),
                edited_snippet=getattr(inp, "edited_snippet"),
                language=getattr(inp, "language"),
                tool_logs=json.loads(getattr(inp, "tool_logs_json")),
                repo_path=getattr(inp, "repo_path"),
                target_file=getattr(inp, "target_file"),
            )
            return render_prompt(template, vars_dict)

        if self.agent_id == "coder_patch_v1":
            return render_prompt(
                template,
                {
                    "repo_map_json": getattr(inp, "repo_map_json"),
                    "intent": getattr(inp, "intent"),
                },
            )

        # The remaining roster agents are prompt-only at this layer today (not yet wired into
        # execution). Keep rendering minimal and let orchestrator-level code provide the exact
        # prompt variables when these agents are introduced to runtime.
        if self.agent_id in {
            "dag_generator_v1",
            "context_librarian_v1",
            "sdet_v1",
            "coder_builder_v1",
            "security_governor_v1",
            "judge_evaluator_v1",
            "scribe_v1",
        }:
            return render_prompt(template, raw_input)

        raise ValueError(f"AgentSpec.render not implemented for agent_id={self.agent_id!r}")


_AGENTS: Dict[str, AgentSpec] = {
    "dag_generator_v1": AgentSpec(
        agent_id="dag_generator_v1",
        description="Architect: translate Helix PRD + high-level repo map into a sequential DAG (nodes+edges).",
        prompt_filename="dag_generator_architect.md",
        input_model=BaseModel,
        output_model=models.DagGeneratorOutput,
    ),
    "coder_patch_v1": AgentSpec(
        agent_id="coder_patch_v1",
        description="Single-file coder that outputs a JSON line patch (filePath/startLine/endLine/replacementLines).",
        prompt_filename="coder_patch.md",
        input_model=models.CoderPatchInput,
        output_model=models.CoderPatchOutput,
    ),
    "intent_compiler_v1": AgentSpec(
        agent_id="intent_compiler_v1",
        description="Architect (legacy id): compiles a macro intent into a sequential DAG (nodes+edges) in JSON.",
        prompt_filename="intent_compiler.md",
        input_model=models.IntentCompilerInput,
        output_model=models.IntentCompilerOutput,
    ),
    "context_librarian_v1": AgentSpec(
        agent_id="context_librarian_v1",
        description="Scout: identify exact file paths and signatures needed for a DAG task.",
        prompt_filename="context_librarian_scout.md",
        input_model=BaseModel,
        output_model=models.LibrarianOutput,
    ),
    "sdet_v1": AgentSpec(
        agent_id="sdet_v1",
        description="SDET: write tests first for a DAG task using provided context and framework standards.",
        prompt_filename="sdet_test_writer.md",
        input_model=BaseModel,
        output_model=models.SdetOutput,
    ),
    "coder_builder_v1": AgentSpec(
        agent_id="coder_builder_v1",
        description="Builder: implement code diffs for a DAG task using provided file contexts.",
        prompt_filename="coder_builder.md",
        input_model=BaseModel,
        output_model=models.CoderOutput,
    ),
    "security_governor_v1": AgentSpec(
        agent_id="security_governor_v1",
        description="Governor: fast lint/security audit of generated diffs vs repo rules.",
        prompt_filename="security_governor.md",
        input_model=BaseModel,
        output_model=models.GovernorOutput,
    ),
    "judge_evaluator_v1": AgentSpec(
        agent_id="judge_evaluator_v1",
        description="Evaluator: final PASS/FAIL based on DAG task, diffs, tests, and terminal logs.",
        prompt_filename="judge_evaluator.md",
        input_model=BaseModel,
        output_model=models.JudgeOutput,
    ),
    "scribe_v1": AgentSpec(
        agent_id="scribe_v1",
        description="Scribe: produce conventional commit message and semantic trace log.",
        prompt_filename="scribe_documenter.md",
        input_model=BaseModel,
        output_model=models.ScribeOutput,
    ),
    "judge_v1": AgentSpec(
        agent_id="judge_v1",
        description='Strict judge that returns PASS/FAIL with justification and optional problematic lines.',
        prompt_filename="judge.md",
        input_model=models.SnippetJudgeInput,
        output_model=models.SnippetJudgeOutput,
    ),
}


def get_agent(agent_id: str) -> AgentSpec:
    try:
        return _AGENTS[agent_id]
    except KeyError as e:
        raise KeyError(f"Unknown agent id: {agent_id}") from e


def list_agents() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in _AGENTS.values():
        out.append(
            {
                "id": a.agent_id,
                "description": a.description,
                "prompt": a.prompt_filename,
                "input_model": a.input_model.__name__,
                "output_model": a.output_model.__name__,
            }
        )
    out.sort(key=lambda x: str(x["id"]))
    return out

