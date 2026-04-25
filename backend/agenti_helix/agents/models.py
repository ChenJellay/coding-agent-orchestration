from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Any, Dict, List, Optional

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
# 2. Code Searcher (precision retrieval)
# ---------------------------------------------------------
class CodeSearcherOutput(BaseModel):
    """Structured output for ``code_searcher_v1``."""

    summary: str = Field(default="", description="Brief rationale for the listed matches")
    results: List[Dict[str, Any]] = Field(default_factory=list, description="Ranked file/symbol hits")


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
            '"patch", "build", or named presets: "product_eng", "diff_guard_patch", '
            '"secure_build_plus", "lint_type_gate".'
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


class DocFetcherOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    doc_url: str = ""
    doc_title: str = ""
    key_constraints: List[str] = Field(default_factory=list)
    task_relevance_summary: str = ""
    irrelevant: bool = False


class DiffValidatorOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    verdict: str = "PASS"
    summary: str = ""


class LinterAgentOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: str = ""
    has_errors: bool = False


class TypeCheckerAgentOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: str = ""
    type_health: str = "clean"


# ---------------------------------------------------------
# Retry-loop agents (memory_summarizer_v1, supreme_court_v1)
# ---------------------------------------------------------
class AttemptRecord(BaseModel):
    """Single coder→judge attempt snapshot fed into retry-aware agents."""

    attempt_n: int = Field(description="1-based attempt index")
    judge_verdict: str = Field(description='"PASS" or "FAIL"')
    justification: str = Field(description="Judge's one-line justification for the verdict")
    diff_summary: str = Field(
        default="",
        description="Short, human-readable description of what this attempt changed (NOT the full diff)",
    )
    static_errors: List[str] = Field(
        default_factory=list,
        description="Non-LLM static-check findings observed on this attempt (lint, syntax, security)",
    )


class MemorySummarizerInput(BaseModel):
    """Context for ``memory_summarizer_v1``: fuse this task's attempts with similar past episodes."""

    task_intent: str
    target_file: str
    acceptance_criteria: str
    attempts: List[AttemptRecord] = Field(description="All coder→judge attempts observed so far for this task")
    similar_past_episodes: List[Dict[str, str]] = Field(
        default_factory=list,
        description='Up to top_k similar resolved error/fix pairs retrieved from MemoryStore. Each dict has keys "error_text" and "resolution".',
    )


class MemorySummarizerOutput(BaseModel):
    """Structured hint that replaces raw judge justification in ``state.feedback`` for the next retry."""

    model_config = ConfigDict(extra="allow")

    root_cause_hypothesis: str = Field(
        description="One-sentence hypothesis of why the previous attempts failed"
    )
    actionable_hint: str = Field(
        description="Concrete, specific direction for the next coder attempt (e.g. 'use X, not Y, because Z')"
    )
    anti_patterns_to_avoid: List[str] = Field(
        default_factory=list,
        description="Specific approaches that previous attempts tried and that the next attempt must NOT repeat",
    )


class SupremeCourtInput(BaseModel):
    """Context for ``supreme_court_v1``: the full retry transcript after ``max_retries`` is exhausted."""

    task_intent: str
    target_file: str
    acceptance_criteria: str
    final_git_diff: str = Field(
        default="",
        description="Unified git diff representing the current workspace state after the last attempt",
    )
    attempts: List[AttemptRecord]
    final_judge_verdict: Dict[str, Any] = Field(
        description="The final judge_response dict (verdict + justification + problematic_lines)"
    )
    static_check_summary: str = Field(
        default="",
        description="Short human-readable summary of the last attempt's static-check status",
    )


class SupremeCourtOutput(BaseModel):
    """Arbitration ruling. The verification loop maps this to PASSED / BLOCKED / human escalation."""

    model_config = ConfigDict(extra="allow")

    ruling: str = Field(
        description='One of "PASS_OVERRIDE" (judge was wrong, accept the change), "CONFIRM_BLOCKED" (retries truly failed), "ESCALATE_HUMAN" (ambiguous; needs review)'
    )
    justification: str = Field(description="One-paragraph reasoning for the ruling")
    evidence: List[str] = Field(
        default_factory=list,
        description="Specific, concrete points from the transcript that support the ruling",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalise_ruling(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        r = str(out.get("ruling") or "").strip().upper().replace("-", "_").replace(" ", "_")
        allowed = {"PASS_OVERRIDE", "CONFIRM_BLOCKED", "ESCALATE_HUMAN"}
        out["ruling"] = r if r in allowed else "CONFIRM_BLOCKED"
        return out


# Registry uses historical names for linter / type-checker agents.
LinterOutput = LinterAgentOutput
TypeCheckerOutput = TypeCheckerAgentOutput


class MemoryWriterOutput(BaseModel):
    """Structured output for ``memory_writer_v1``."""

    model_config = ConfigDict(extra="allow")

    written: int = Field(default=0, description="Number of memory entries written")
    message: str = Field(default="", description="Optional human-readable status")

