from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Any, List, Optional

# NOTE
# - The classes below implement the “full agent roster” schemas requested.
# - Legacy models used by currently-wired endpoints remain at the bottom to
#   preserve backward compatibility for `coder_patch_v1`, `intent_compiler_v1`,
#   and `judge_v1`.


# ---------------------------------------------------------
# 1. The Context Librarian (The Scout)
# ---------------------------------------------------------
class FileRequirement(BaseModel):
    file_path: str = Field(description="Exact path to the file in the repository")
    required_symbols: List[str] = Field(description="Specific classes or functions needed from this file")


class LibrarianOutput(BaseModel):
    search_strategy: str = Field(description="Explanation of why these specific files were selected")
    required_files: List[FileRequirement] = Field(description="Array of files the Coder needs to read or modify")


# ---------------------------------------------------------
# 3. The SDET & 4. The Coder (Shared Output Format)
# ---------------------------------------------------------
class CodeFile(BaseModel):
    file_path: str = Field(description="Path where the file should be written or updated")
    content: str = Field(description="The complete, exact code content to be written to the file")


class SdetOutput(BaseModel):
    testing_strategy: str = Field(description="Reasoning for the test cases and edge cases covered")
    test_files: List[CodeFile] = Field(description="The generated test files")


class CoderOutput(BaseModel):
    implementation_logic: str = Field(description="Brief 1-3 sentence summary of what was implemented and why. All extended reasoning must appear in the preceding <think> block, not here.")
    modified_files: List[CodeFile] = Field(description="The actual feature code files to be updated")
    missing_context: Optional[str] = Field(
        description="If the Librarian missed a file, flag it here. Otherwise, leave null."
    )


# ---------------------------------------------------------
# 5. The Security & Linting Governor
# ---------------------------------------------------------
class GovernorOutput(BaseModel):
    audit_reasoning: str = Field(description="Internal thought process while checking rules")
    is_safe: bool = Field(description="True if the code passes all static checks, False otherwise")
    violations: List[str] = Field(description="If is_safe is false, list the specific lines and rule violations")


# ---------------------------------------------------------
# 6. The Judge
# ---------------------------------------------------------
class JudgeOutput(BaseModel):
    evaluation_reasoning: str = Field(description="Analysis of the terminal test logs against the acceptance criteria")
    pass_tests: Optional[bool] = Field(
        description=(
            "True if tests ran and passed (or code-only review approved). "
            "False if tests ran and failed. "
            "null if tests could not be executed at all (e.g. no test runner) — signals human review required."
        )
    )
    feedback_for_coder: str = Field(
        description="If pass_tests is false or null, highly specific instructions on how to fix the code"
    )


# ---------------------------------------------------------
# 7. The Scribe
# ---------------------------------------------------------
class ScribeOutput(BaseModel):
    summary_reasoning: str = Field(description="Analysis of the task execution to extract key architectural decisions")
    commit_message: str = Field(description="Conventional commit message for this task")
    semantic_trace_log: str = Field(description="A 2-3 sentence summary of how the agent solved the original intent")


# ---------------------------------------------------------
# 8. Supplemental workflow agents
# ---------------------------------------------------------
class DocExample(BaseModel):
    label: str = Field(description="Short label for the referenced example")
    snippet: str = Field(description="Verbatim code or text snippet extracted from the document")


class DocFetcherOutput(BaseModel):
    doc_url: str = Field(description="Echoed source URL")
    doc_title: str = Field(description="Best-effort document title")
    key_constraints: List[str] = Field(description="Task-relevant implementation constraints extracted from the document")
    code_examples: List[DocExample] = Field(description="Relevant verbatim examples from the document")
    task_relevance_summary: str = Field(description="Short explanation of how the document applies to the task")
    irrelevant: bool = Field(description="True when the document did not materially help the task")


class DiffValidationFinding(BaseModel):
    type: str = Field(description="Finding category such as deletion, scope, or regression")
    severity: str = Field(description="Severity level, typically info, warn, or error")
    file: Optional[str] = Field(default=None, description="File associated with the finding, if any")
    line_range: Optional[List[int]] = Field(default=None, description="Approximate affected line range")
    description: str = Field(description="Human-readable summary of the issue")
    recommendation: str = Field(description="Minimal corrective action")


class DiffValidatorOutput(BaseModel):
    verdict: str = Field(description='Expected values: "PASS", "WARN", or "BLOCK"')
    files_changed: List[str] = Field(description="Files observed in the diff")
    out_of_scope_files: List[str] = Field(description="Files that fall outside the allowed path set")
    findings: List[DiffValidationFinding] = Field(description="Structured diff review findings")
    rule_violations: List[str] = Field(description="Repository rule violations detected in the diff")
    structural_regressions: List[str] = Field(description="Potential public API or contract regressions")
    summary: str = Field(description="Overall diff validation summary")


class StaticAnalysisFinding(BaseModel):
    line_number: Optional[int] = Field(default=None, description="1-based line number of the finding, when available")
    column: Optional[int] = Field(default=None, description="1-based column of the finding, when available")
    rule_id: str = Field(description="Linter or compiler rule identifier")
    severity: str = Field(description="Severity level: error, warning, or info")
    message: str = Field(description="Raw finding message")
    fix_hint: str = Field(description="Minimal actionable fix guidance")
    blocks_acceptance: bool = Field(description="Whether this finding likely blocks the stated acceptance criteria")


class LinterOutput(BaseModel):
    target_file: str = Field(description="Echoed target file path")
    language: str = Field(description="Echoed language identifier")
    finding_count: int = Field(description="Total findings parsed from the linter output")
    has_errors: bool = Field(description="True when at least one error-level finding is present")
    findings: List[StaticAnalysisFinding] = Field(description="Structured linter findings")
    summary: str = Field(description="Condensed linter summary")


class TypeCheckFinding(BaseModel):
    line_number: Optional[int] = Field(default=None, description="1-based line number of the finding, when available")
    column: Optional[int] = Field(default=None, description="1-based column of the finding, when available")
    error_code: str = Field(description="Type checker error code")
    classification: str = Field(description="Normalized classification of the type issue")
    message: str = Field(description="Raw type checker message")
    in_dependency: bool = Field(description="True when the error originates in a non-target dependency file")
    fix_instruction: str = Field(description="Concrete next action for resolving the type issue")
    blocks_acceptance: bool = Field(description="Whether this finding likely blocks the stated acceptance criteria")


class TypeCheckerOutput(BaseModel):
    target_file: str = Field(description="Echoed target file path")
    language: str = Field(description="Echoed language identifier")
    type_health: str = Field(description='Overall status: "clean", "fixable", or "structural"')
    error_count: int = Field(description="Total errors parsed from the type checker output")
    findings: List[TypeCheckFinding] = Field(description="Structured type checker findings")
    dependency_errors: List[str] = Field(description="Errors rooted in dependency files")
    summary: str = Field(description="Condensed type-check summary")


class SearchResult(BaseModel):
    file_path: str = Field(description="Matching file path")
    symbol: Optional[str] = Field(default=None, description="Most relevant symbol in the match, if any")
    snippet: str = Field(description="Context snippet explaining the match")
    relevance: str = Field(description="Short explanation of why this result is relevant")


class CodeSearcherOutput(BaseModel):
    search_strategy: str = Field(description="How the search narrowed the codebase")
    results: List[SearchResult] = Field(description="Ranked search results with supporting snippets")
    summary: str = Field(description="Condensed answer to the original search task")


# =========================================================
# Legacy schemas (used by currently-wired endpoints)
# =========================================================
class CoderPatchInput(BaseModel):
    repo_map_json: str = Field(description="JSON string of repo map file/symbol metadata")
    intent: str = Field(description="User intent describing desired change")
    target_file: Optional[str] = Field(default=None, description="Target file path the system intends to edit (relative)")
    target_file_content: Optional[str] = Field(default=None, description="Current contents of target_file (verbatim)")


class CoderPatchOutput(BaseModel):
    filePath: str = Field(description="Target file path (must exist in repo map)")
    startLine: int = Field(description="1-based inclusive start line of edit range")
    endLine: int = Field(description="1-based inclusive end line of edit range")
    replacementLines: List[str] = Field(description="Replacement code lines to apply")
    # §4.5 — Optional human escalation signal emitted instead of a patch.
    escalate_to_human: Optional[bool] = Field(default=None, description="Set true if the task is ambiguous and requires human clarification")
    escalation_reason: Optional[str] = Field(default=None, description="Short reason for escalation (required when escalate_to_human=true)")

    @model_validator(mode="before")
    @classmethod
    def _coerce_escalation_shape(cls, data: Any) -> Any:
        """
        Allow the coder to emit an escalation object that omits patch fields.
        Downstream will short-circuit on `escalate_to_human` and never apply a patch.
        """
        if not isinstance(data, dict):
            return data
        if data.get("escalate_to_human"):
            data.setdefault("filePath", "")
            data.setdefault("startLine", 0)
            data.setdefault("endLine", 0)
            data.setdefault("replacementLines", [])
        return data


class IntentCompilerInput(BaseModel):
    macro_intent: str = Field(description="High-level feature intent")
    repo_path: str = Field(description="Absolute path to repository root")


class IntentNodeSpec(BaseModel):
    node_id: str = Field(description="Node id in DAG (e.g., N1)")
    description: str = Field(description="Short description of the micro-task")
    target_file: str = Field(description="Path relative to repo root")
    acceptance_criteria: str = Field(description="Testable criteria for success")
    pipeline_mode: str = Field(
        default="patch",
        description=(
            '"patch" — fast single-file line-patch (coder_patch_v1 + judge_v1). '
            '"build" — full TDD pipeline (librarian → sdet → coder_builder → governor → judge_evaluator). '
            '"product_eng" / "diff_guard_patch" / "secure_build_plus" / "lint_type_gate" — named preset workflows. '
            '"custom" — bespoke workflow; populate `workflow` with the ordered agent_id list.'
        ),
    )
    workflow: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional ordered list of agent_ids composing a bespoke workflow for this node. "
            "When provided, the orchestrator builds coder+judge chains dynamically from this list "
            "(ignoring pipeline_mode presets). Agents are automatically split: coder-side agents "
            "(doc_fetcher_v1, context_librarian_v1, sdet_v1, coder_builder_v1, coder_patch_v1) produce the diff; "
            "judge-side agents (security_governor_v1, diff_validator_v1, linter_v1, type_checker_v1, "
            "judge_evaluator_v1, judge_v1, scribe_v1, memory_writer_v1) evaluate or summarize it."
        ),
    )


class IntentCompilerOutput(BaseModel):
    dag_id: Optional[str] = Field(default=None, description="Optional DAG id")
    nodes: List[IntentNodeSpec] = Field(description="DAG nodes")
    edges: List[List[str]] = Field(
        default_factory=list,
        description="Edges as [from, to] pairs",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_single_node_or_missing_edges(cls, data: Any) -> Any:
        """
        Local models sometimes emit a single node dict at the top level (no `nodes` array),
        or omit `edges`. Normalize so validation matches the prompt schema.
        """
        if not isinstance(data, dict):
            return data
        if "nodes" in data:
            out = dict(data)
            out.setdefault("edges", [])
            return out
        if "node_id" in data and "target_file" in data:
            dag_id = data.get("dag_id")
            node = {k: v for k, v in data.items() if k != "dag_id"}
            return {"dag_id": dag_id, "nodes": [node], "edges": []}
        return data


class SnippetJudgeInput(BaseModel):
    repo_path: Optional[str] = Field(default=None, description="Optional repo path for disk-backed judge")
    target_file: Optional[str] = Field(default=None, description="Optional relative target file path")
    acceptance_criteria: str = Field(description="Acceptance criteria string")
    original_snippet: str = Field(description="Original code snippet")
    edited_snippet: str = Field(description="Edited code snippet")
    language: str = Field(description="Language identifier")
    tool_logs_json: str = Field(description="Tool logs as JSON string")


class SnippetJudgeOutput(BaseModel):
    verdict: str = Field(description='Expected values: "PASS" or "FAIL"')
    justification: str = Field(description="Short justification string")
    problematic_lines: List[int] = Field(description="Up to 5 specific 1-based line numbers where the error originates. Never enumerate every line — only the lines the coder must fix.")


# ---------------------------------------------------------
# Module Rewriter
# ---------------------------------------------------------
class CoderModuleOutput(BaseModel):
    rewritten_module: str = Field(
        description=(
            "The complete rewritten module text. This is a drop-in replacement for the extracted "
            "lines — do not include file-level imports or code outside the module."
        )
    )


# ---------------------------------------------------------
# 8. The Memory Summarizer
# ---------------------------------------------------------
# ---------------------------------------------------------
# 9. The Supreme Court Arbitrator
# ---------------------------------------------------------
class SupremeCourtOutput(BaseModel):
    resolved: bool = Field(
        description="True if the Supreme Court was able to produce a definitive patch. False if escalation to human is unavoidable."
    )
    reasoning: str = Field(description="Arbitration reasoning explaining the decision or why resolution failed")
    # Populated only when resolved=True — same shape as CoderPatchOutput.
    filePath: Optional[str] = Field(default=None, description="Target file path for the resolved patch")
    startLine: Optional[int] = Field(default=None, description="1-based start line of the resolved patch")
    endLine: Optional[int] = Field(default=None, description="1-based end line of the resolved patch")
    replacementLines: Optional[List[str]] = Field(default=None, description="Replacement lines for the resolved patch")
    compromise_summary: Optional[str] = Field(default=None, description="One-sentence description of the compromise made")


class MemorySummaryOutput(BaseModel):
    compressed_summary: str = Field(
        description=(
            "2-4 sentence summary of what was attempted, what failed, and the current state. "
            "Should preserve enough context for the next coder attempt without raw error dumps."
        )
    )
    key_constraints: List[str] = Field(
        description="Bullet list of constraints or facts the next attempt MUST respect (max 5)."
    )


class MemoryWriterEpisode(BaseModel):
    error_pattern: str = Field(description="Generalized problem pattern")
    resolution_pattern: str = Field(description="Generalized fix pattern")
    anti_patterns: List[str] = Field(description="Previously attempted approaches that should be avoided")
    applicable_file_types: List[str] = Field(description="File types where this episode is relevant")
    retrieval_key: List[str] = Field(description="Keywords for future retrieval")


class MemoryWriterOutput(BaseModel):
    task_id: str = Field(description="Echoed task id")
    dag_id: str = Field(description="Echoed dag id")
    target_file: str = Field(description="Echoed target file")
    final_verdict: str = Field(description='Expected values: "PASS" or "ESCALATED"')
    attempt_count: int = Field(description="Number of attempts that occurred before resolution")
    episode: MemoryWriterEpisode = Field(description="Generalized episode to persist")
    should_persist: bool = Field(description="Whether the episode is worth writing to memory")

