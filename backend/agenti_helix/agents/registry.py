from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

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
    # Optional backend routing hint consumed by get_default_inference_backend.
    # None means "use the system default (mlx_local or AGENTI_HELIX_BACKEND_TYPE env)".
    backend_type: Optional[str] = None
    # Authoritative output-token ceiling for this agent. The runtime treats
    # this as an *upper bound*: if the chain DSL passes a smaller max_tokens,
    # the smaller value wins; if it passes a larger one (or omits it), the
    # spec ceiling is used.  This stops local quant models from running away
    # on classification-shaped prompts (e.g. judge_v1 emitting a 6 k-token
    # `<think>` block before a 50-token JSON answer).  Defaults to a generous
    # 4096 so unspecified agents do not behave any worse than the old
    # MLX-only runtime.
    max_output_tokens: int = 4096

    def render(self, raw_input: Dict[str, Any]) -> str:
        """Render the agent prompt from `raw_input`.

        Agents with specialised input schemas (judge_v1, coder_patch_v1,
        intent_compiler_v1) use dedicated renderers.  All other agents fall
        through to the generic `render_prompt(template, raw_input)` path so
        new roster agents are automatically supported without touching this
        method.
        """
        template = load_prompt_template(self.prompt_filename)

        if self.agent_id == "judge_v1":
            inp = self.input_model.model_validate(raw_input)
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
            inp = self.input_model.model_validate(raw_input)
            return render_prompt(
                template,
                {
                    "repo_map_json": getattr(inp, "repo_map_json"),
                    "intent": getattr(inp, "intent"),
                    "target_file": getattr(inp, "target_file", None),
                    "target_file_content": getattr(inp, "target_file_content", None),
                },
            )

        # All other agents (intent_compiler_v1 and every roster agent) use a
        # generic render: interpolate template placeholders directly from raw_input.
        return render_prompt(template, raw_input)


# Per-agent max_output_tokens budgets — set tight enough to keep local quant
# models from over-thinking, loose enough to fit the worst legitimate output
# (full file rewrites for coder_builder, multi-node DAGs for intent_compiler).
# Audit log: see `docs/phase2-verification-loop.md` for the rationale.
_AGENTS: Dict[str, AgentSpec] = {
    "coder_patch_v1": AgentSpec(
        agent_id="coder_patch_v1",
        description="Single-file coder that outputs a JSON line patch (filePath/startLine/endLine/replacementLines).",
        prompt_filename="coder_patch.md",
        input_model=models.CoderPatchInput,
        output_model=models.CoderPatchOutput,
        max_output_tokens=1024,
    ),
    "intent_compiler_v1": AgentSpec(
        agent_id="intent_compiler_v1",
        description="Architect (legacy id): compiles a macro intent into a sequential DAG (nodes+edges) in JSON.",
        prompt_filename="intent_compiler.md",
        input_model=models.IntentCompilerInput,
        output_model=models.IntentCompilerOutput,
        max_output_tokens=4096,
    ),
    "context_librarian_v1": AgentSpec(
        agent_id="context_librarian_v1",
        description="Scout: identify exact file paths and signatures needed for a DAG task.",
        prompt_filename="context_librarian_scout.md",
        input_model=BaseModel,
        output_model=models.LibrarianOutput,
        max_output_tokens=2048,
    ),
    "code_searcher_v1": AgentSpec(
        agent_id="code_searcher_v1",
        description="Precision code search helper that returns ranked file and symbol matches.",
        prompt_filename="code_searcher.md",
        input_model=BaseModel,
        output_model=models.CodeSearcherOutput,
        max_output_tokens=2048,
    ),
    "doc_fetcher_v1": AgentSpec(
        agent_id="doc_fetcher_v1",
        description="Documentation context agent that distills external references into actionable constraints.",
        prompt_filename="doc_fetcher.md",
        input_model=BaseModel,
        output_model=models.DocFetcherOutput,
    ),
    "sdet_v1": AgentSpec(
        agent_id="sdet_v1",
        description="SDET: write tests first for a DAG task using provided context and framework standards.",
        prompt_filename="sdet_test_writer.md",
        input_model=BaseModel,
        output_model=models.SdetOutput,
        max_output_tokens=4096,
    ),
    "coder_builder_v1": AgentSpec(
        agent_id="coder_builder_v1",
        description="Builder: implement code diffs for a DAG task using provided file contexts.",
        prompt_filename="coder_builder.md",
        input_model=BaseModel,
        output_model=models.CoderOutput,
        max_output_tokens=8192,  # Full file rewrites can be large.
    ),
    "security_governor_v1": AgentSpec(
        agent_id="security_governor_v1",
        description="Governor: fast lint/security audit of generated diffs vs repo rules.",
        prompt_filename="security_governor.md",
        input_model=BaseModel,
        output_model=models.GovernorOutput,
        max_output_tokens=1536,
    ),
    "diff_validator_v1": AgentSpec(
        agent_id="diff_validator_v1",
        description="Diff scope validator that catches out-of-scope changes and structural regressions.",
        prompt_filename="diff_validator.md",
        input_model=BaseModel,
        output_model=models.DiffValidatorOutput,
    ),
    "linter_v1": AgentSpec(
        agent_id="linter_v1",
        description="Static analysis parser that turns raw linter output into prioritized findings.",
        prompt_filename="linter.md",
        input_model=BaseModel,
        output_model=models.LinterOutput,
    ),
    "type_checker_v1": AgentSpec(
        agent_id="type_checker_v1",
        description="Type system validation parser for mypy and tsc outputs.",
        prompt_filename="type_checker.md",
        input_model=BaseModel,
        output_model=models.TypeCheckerOutput,
    ),
    "judge_evaluator_v1": AgentSpec(
        agent_id="judge_evaluator_v1",
        description="Evaluator: final PASS/FAIL based on DAG task, diffs, tests, and terminal logs.",
        prompt_filename="judge_evaluator.md",
        input_model=BaseModel,
        output_model=models.JudgeOutput,
        max_output_tokens=2048,
    ),
    "judge_v1": AgentSpec(
        agent_id="judge_v1",
        description='Strict judge that returns PASS/FAIL with justification and optional problematic lines.',
        prompt_filename="judge.md",
        input_model=models.SnippetJudgeInput,
        output_model=models.SnippetJudgeOutput,
        backend_type="mlx_local",  # Local quantized model: fast, cheap, good for classification
        # PASS/FAIL + one-sentence justification + line list — fits in well
        # under 256 tokens.  Tight cap so a runaway model is killed in seconds
        # rather than minutes.
        max_output_tokens=768,
    ),
    "doc_fetcher_v1": AgentSpec(
        agent_id="doc_fetcher_v1",
        description="Documentation agent: distils external doc text into actionable constraints for coders.",
        prompt_filename="doc_fetcher.md",
        input_model=BaseModel,
        output_model=models.DocFetcherOutput,
        max_output_tokens=2048,
    ),
    "diff_validator_v1": AgentSpec(
        agent_id="diff_validator_v1",
        description="Diff gate: scope/safety validation on unified git diffs before the semantic judge.",
        prompt_filename="diff_validator.md",
        input_model=BaseModel,
        output_model=models.DiffValidatorOutput,
        max_output_tokens=1536,
    ),
    "linter_v1": AgentSpec(
        agent_id="linter_v1",
        description="Linter interpreter: turns raw eslint/ruff output into structured findings.",
        prompt_filename="linter.md",
        input_model=BaseModel,
        output_model=models.LinterAgentOutput,
        max_output_tokens=2048,
    ),
    "type_checker_v1": AgentSpec(
        agent_id="type_checker_v1",
        description="Type-check interpreter: structures mypy/tsc output for downstream judges.",
        prompt_filename="type_checker.md",
        input_model=BaseModel,
        output_model=models.TypeCheckerAgentOutput,
        max_output_tokens=2048,
    ),
    "memory_summarizer_v1": AgentSpec(
        agent_id="memory_summarizer_v1",
        description="Retry coach: fuses attempt history + similar past episodes into a focused hint for the next coder attempt.",
        prompt_filename="memory_summarizer.md",
        input_model=models.MemorySummarizerInput,
        output_model=models.MemorySummarizerOutput,
        # Output = hypothesis + hint + 3-5 anti-patterns. Tight cap prevents
        # the hint from growing into a second prompt that dwarfs the coder's.
        max_output_tokens=1024,
    ),
    "memory_writer_v1": AgentSpec(
        agent_id="memory_writer_v1",
        description="Memory writer that converts resolved tasks into reusable episodic memory entries.",
        prompt_filename="memory_writer.md",
        input_model=BaseModel,
        output_model=models.MemoryWriterOutput,
        max_output_tokens=1024,
    ),
    "supreme_court_v1": AgentSpec(
        agent_id="supreme_court_v1",
        description="Final arbiter on exhausted retries: emits PASS_OVERRIDE / CONFIRM_BLOCKED / ESCALATE_HUMAN.",
        prompt_filename="supreme_court.md",
        input_model=models.SupremeCourtInput,
        output_model=models.SupremeCourtOutput,
        # Classification-shaped: 3-way ruling + one paragraph + evidence list.
        max_output_tokens=1536,
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


def get_agent_detail(agent_id: str) -> Dict[str, Any]:
    """Return a JSON-serializable view of the agent, including prompt and schemas."""
    spec = get_agent(agent_id)
    prompt_text = load_prompt_template(spec.prompt_filename)

    input_schema: Dict[str, Any] | None
    if spec.input_model is BaseModel:
        input_schema = None
    else:
        input_schema = spec.input_model.model_json_schema()

    output_schema = spec.output_model.model_json_schema()

    return {
        "id": spec.agent_id,
        "description": spec.description,
        "prompt": prompt_text,
        "prompt_filename": spec.prompt_filename,
        "input_model": spec.input_model.__name__,
        "output_model": spec.output_model.__name__,
        "input_schema": input_schema,
        "output_schema": output_schema,
    }


def update_agent_prompt(agent_id: str, new_prompt: str) -> None:
    """Persist updated prompt text to the underlying prompt file."""
    spec = get_agent(agent_id)
    prompts_dir = Path(__file__).resolve().parent / "prompts"
    path = (prompts_dir / spec.prompt_filename).resolve()
    path.write_text(new_prompt, encoding="utf-8")

