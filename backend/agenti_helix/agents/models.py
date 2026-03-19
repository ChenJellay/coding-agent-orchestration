from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List, Optional

# NOTE
# - The classes below implement the “full agent roster” schemas requested.
# - Legacy models used by currently-wired endpoints remain at the bottom to
#   preserve backward compatibility for `coder_patch_v1`, `intent_compiler_v1`,
#   and `judge_v1`.


# ---------------------------------------------------------
# 1. The DAG Generator (The Architect)
# ---------------------------------------------------------
class TaskNode(BaseModel):
    task_id: str = Field(description="Unique identifier for the task, e.g., 'task_1'")
    description: str = Field(description="Actionable description of the work to be done")
    dependencies: List[str] = Field(description="List of task_ids that must be completed before this one")


class DagGeneratorOutput(BaseModel):
    chain_of_thought: str = Field(description="Internal reasoning for how to break down the user intent")
    tasks: List[TaskNode] = Field(description="The sequential array of micro-tasks")


# ---------------------------------------------------------
# 2. The Context Librarian (The Scout)
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
    implementation_logic: str = Field(description="Step-by-step logic used to solve the task")
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
    pass_tests: bool = Field(description="True if the code works perfectly, False if it needs revision")
    feedback_for_coder: str = Field(
        description="If pass_tests is false, highly specific instructions on how to fix the code"
    )


# ---------------------------------------------------------
# 7. The Scribe
# ---------------------------------------------------------
class ScribeOutput(BaseModel):
    summary_reasoning: str = Field(description="Analysis of the task execution to extract key architectural decisions")
    commit_message: str = Field(description="Conventional commit message for this task")
    semantic_trace_log: str = Field(description="A 2-3 sentence summary of how the agent solved the original intent")


# =========================================================
# Legacy schemas (used by currently-wired endpoints)
# =========================================================
class CoderPatchInput(BaseModel):
    repo_map_json: str = Field(description="JSON string of repo map file/symbol metadata")
    intent: str = Field(description="User intent describing desired change")


class CoderPatchOutput(BaseModel):
    filePath: str = Field(description="Target file path (must exist in repo map)")
    startLine: int = Field(description="1-based inclusive start line of edit range")
    endLine: int = Field(description="1-based inclusive end line of edit range")
    replacementLines: List[str] = Field(description="Replacement code lines to apply")


class IntentCompilerInput(BaseModel):
    macro_intent: str = Field(description="High-level feature intent")
    repo_path: str = Field(description="Absolute path to repository root")


class IntentNodeSpec(BaseModel):
    node_id: str = Field(description="Node id in DAG (e.g., N1)")
    description: str = Field(description="Short description of the micro-task")
    target_file: str = Field(description="Path relative to repo root")
    acceptance_criteria: str = Field(description="Testable criteria for success")


class IntentCompilerOutput(BaseModel):
    dag_id: Optional[str] = Field(default=None, description="Optional DAG id")
    nodes: List[IntentNodeSpec] = Field(description="DAG nodes")
    edges: List[List[str]] = Field(description="Edges as [from, to] pairs")


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
    problematic_lines: List[int] = Field(description="1-based line numbers in edited snippet that are problematic")

