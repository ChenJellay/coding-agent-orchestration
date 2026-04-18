from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def default_coder_chain(task: Any | None = None) -> Dict[str, Any]:
    """
    Default chain: build focused Repo Map context -> coder_patch_v1 -> apply patch.

    Uses `get_focused_context` (target file + 1-hop import deps) so the agent
    receives a minimal, dependency-aware slice rather than the full repo map.
    The `target_file` key must be present in the chain execution context.
    """
    return {
        "steps": [
            {
                "type": "tool",
                "id": "build_repo_map_ctx",
                "output_key": "repo_map_ctx",
                "tool_name": "get_focused_context",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "target_files": [{"$ref": "target_file"}],
                    "depth": 1,
                },
            },
            {
                "type": "tool",
                "id": "snapshot_target",
                "output_key": "target_file_content",
                "tool_name": "snapshot_target_file",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "target_file": {"$ref": "target_file"},
                },
            },
            {
                "type": "agent",
                "id": "coder_patch",
                "output_key": "coder_patch",
                "agent_id": "coder_patch_v1",
                "input_bindings": {
                    "repo_map_json": {"$ref": "repo_map_ctx.repo_map_json"},
                    "intent": {"$ref": "intent"},
                    "target_file": {"$ref": "target_file"},
                    "target_file_content": {"$ref": "target_file_content"},
                },
                # Patch output is a small JSON object (startLine/endLine/replacementLines).
                # Cap at 1024 tokens to prevent runaway generation.
                "runtime": {"temperature": 0.0, "max_tokens": 1024},
            },
            {
                "type": "tool",
                "id": "apply_patch",
                "output_key": "diff_json",
                "tool_name": "apply_line_patch_and_validate",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "patch": {"$ref": "coder_patch"},
                    "allowed_paths": {"$ref": "repo_map_ctx.allowed_paths"},
                },
            },
        ]
    }


def default_judge_chain(task: Any | None = None) -> Dict[str, Any]:
    """
    Default chain: snapshot target file -> infer language -> build tool logs json -> judge_v1.
    """
    return {
        "steps": [
            {
                "type": "tool",
                "id": "snapshot_edited_snippet",
                "output_key": "edited_snippet",
                "tool_name": "snapshot_target_file",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "target_file": {"$ref": "target_file"},
                },
            },
            {
                "type": "tool",
                "id": "infer_language",
                "output_key": "language",
                "tool_name": "infer_language_from_target_file",
                "input_bindings": {"target_file": {"$ref": "target_file"}},
            },
            {
                "type": "tool",
                "id": "build_tool_logs_json",
                "output_key": "tool_logs_json",
                "tool_name": "build_tool_logs_json",
                "input_bindings": {"static_check_logs": {"$ref": "static_check_logs"}},
            },
            {
                "type": "agent",
                "id": "judge",
                "output_key": "judge_response",
                "agent_id": "judge_v1",
                "input_bindings": {
                    "repo_path": {"$ref": "repo_path"},
                    "target_file": {"$ref": "target_file"},
                    "acceptance_criteria": {"$ref": "acceptance_criteria"},
                    "original_snippet": {"$ref": "original_snippet"},
                    "edited_snippet": {"$ref": "edited_snippet"},
                    "language": {"$ref": "language"},
                    "tool_logs_json": {"$ref": "tool_logs_json"},
                },
                # Judge output is a JSON verdict + reasoning. Historical max ~17K chars
                # (~4K tokens). Cap at 6144 to handle verbose outputs without runaway.
                "runtime": {"temperature": 0.0, "max_tokens": 6144},
            },
        ]
    }


def default_full_pipeline_coder_chain(task: Any | None = None) -> Dict[str, Any]:
    """
    Full TDD pipeline coder chain:
      build_ast_context → context_librarian_v1 → load_file_contents
      → sdet_v1 → coder_builder_v1 → write_all_files (→ diff_json)

    The `target_file` and `intent` keys must be present in the chain execution context.
    """
    return {
        "steps": [
            # 1. Build AST-level repo map for the librarian.
            # skip_if_present: reuse on coder retries so the librarian isn't called again.
            {
                "type": "tool",
                "id": "build_ast_ctx",
                "output_key": "ast_repo_map_ctx",
                "tool_name": "build_ast_context",
                "skip_if_present": True,
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    # Build-mode often needs broad repo visibility (new files, wiring, tests).
                    # Provide no target_files so the tool returns the full repo map.
                    "target_files": [],
                },
            },
            # 2. Context Librarian: identify required files and symbols.
            # skip_if_present: reuse on coder retries.
            {
                "type": "agent",
                "id": "context_librarian",
                "output_key": "librarian_output",
                "agent_id": "context_librarian_v1",
                "skip_if_present": True,
                "input_bindings": {
                    "dag_task": {"$ref": "intent"},
                    "ast_repo_map_json": {"$ref": "ast_repo_map_ctx.ast_repo_map_json"},
                },
                "runtime": {"temperature": 0.0, "max_tokens": 2048},
            },
            # 3. Load file contents identified by the librarian.
            # skip_if_present: reuse on coder retries.
            {
                "type": "tool",
                "id": "load_files",
                "output_key": "file_contexts",
                "tool_name": "load_file_contents",
                "skip_if_present": True,
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "required_files": {"$ref": "librarian_output.required_files"},
                },
            },
            # 4. SDET: write tests first (TDD).
            # skip_if_present: reuse on coder retries (tests don't change between attempts).
            {
                "type": "agent",
                "id": "sdet",
                "output_key": "sdet_output",
                "agent_id": "sdet_v1",
                "skip_if_present": True,
                "input_bindings": {
                    "dag_task": {"$ref": "intent"},
                    "acceptance_criteria": {"$ref": "acceptance_criteria"},
                    "context_chunks_json": {"$ref": "file_contexts.file_contexts_json"},
                    "testing_standards": (
                        "Follow the repository's existing testing patterns. "
                        "Mock external dependencies. Write focused, edge-case-aware tests. "
                        "Ensure tests fail before the implementation exists."
                    ),
                },
                "runtime": {"temperature": 0.1, "max_tokens": 4096},
            },
            # 5. Coder Builder: implement the feature.
            {
                "type": "agent",
                "id": "coder_builder",
                "output_key": "coder_output",
                "agent_id": "coder_builder_v1",
                "input_bindings": {
                    "dag_task": {"$ref": "intent"},
                    "acceptance_criteria": {"$ref": "acceptance_criteria"},
                    "file_contexts_json": {"$ref": "file_contexts.file_contexts_json"},
                },
                "runtime": {"temperature": 0.0, "max_tokens": 8192},
            },
            # 6. Write all files (code + tests) to disk; output becomes diff_json.
            {
                "type": "tool",
                "id": "write_files",
                "output_key": "diff_json",
                "tool_name": "write_all_files",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "modified_files": {"$ref": "coder_output.modified_files"},
                    "test_files": {"$ref": "sdet_output.test_files"},
                    "checkpoint_id": {"$ref": "checkpoint_id"},
                },
            },
        ]
    }


def default_full_pipeline_judge_chain(task: Any | None = None) -> Dict[str, Any]:
    """
    Full TDD pipeline judge chain:
      run_tests → load_rules → security_governor_v1 → judge_evaluator_v1
      → map_evaluator_verdict (→ judge_response)

    Requires `intent`, `diff_json`, `acceptance_criteria` in the execution context
    (all provided by node_call_judge when running the full pipeline).
    """
    return {
        "steps": [
            # 1. Run the test suite written by the SDET.
            {
                "type": "tool",
                "id": "run_tests",
                "output_key": "test_results",
                "tool_name": "run_tests",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "test_file_paths": {"$ref": "diff_json.test_file_paths"},
                },
            },
            # 2. Load repo compliance rules.
            {
                "type": "tool",
                "id": "load_rules",
                "output_key": "rules",
                "tool_name": "load_rules",
                "input_bindings": {"repo_root": {"$ref": "repo_root"}},
            },
            # 3. Security Governor: audit the generated code.
            {
                "type": "agent",
                "id": "security_governor",
                "output_key": "governor_output",
                "agent_id": "security_governor_v1",
                "input_bindings": {
                    "diff_json": {"$ref": "diff_json.diff_json_str"},
                    "repo_rules_text": {"$ref": "rules.repo_rules_text"},
                },
                "runtime": {"temperature": 0.0, "max_tokens": 2048},
            },
            # 4. Judge Evaluator: assess test results against acceptance criteria.
            {
                "type": "agent",
                "id": "judge_evaluator",
                "output_key": "judge_eval",
                "agent_id": "judge_evaluator_v1",
                "input_bindings": {
                    "dag_task": {"$ref": "intent"},
                    "acceptance_criteria": {"$ref": "acceptance_criteria"},
                    "coder_diff_json": {"$ref": "diff_json.diff_json_str"},
                    "sdet_tests_json": {"$ref": "diff_json.diff_json_str"},
                    "terminal_logs": {"$ref": "test_results.terminal_logs"},
                },
                "runtime": {"temperature": 0.0, "max_tokens": 4096},
            },
            # 5. Map to the verdict shape expected by the verification loop.
            {
                "type": "tool",
                "id": "map_verdict",
                "output_key": "judge_response",
                "tool_name": "map_evaluator_verdict",
                "input_bindings": {
                    "pass_tests": {"$ref": "judge_eval.pass_tests"},
                    "evaluation_reasoning": {"$ref": "judge_eval.evaluation_reasoning"},
                    "feedback_for_coder": {"$ref": "judge_eval.feedback_for_coder"},
                    "is_safe": {"$ref": "governor_output.is_safe"},
                    "violations": {"$ref": "governor_output.violations"},
                },
            },
        ]
    }


# ---------------------------------------------------------
# Dynamic workflow composition
# ---------------------------------------------------------
# Agents are classified by role so an ordered list from the intent compiler
# can be split cleanly into a coder chain (produces a diff) and a judge chain
# (evaluates the diff).
_CODER_SIDE_AGENTS = {
    "doc_fetcher_v1",
    "code_searcher_v1",
    "context_librarian_v1",
    "sdet_v1",
    "coder_builder_v1",
    "coder_patch_v1",
}
_JUDGE_SIDE_AGENTS = {
    "security_governor_v1",
    "diff_validator_v1",
    "linter_v1",
    "type_checker_v1",
    "judge_evaluator_v1",
    "judge_v1",
    "scribe_v1",
    "memory_writer_v1",
}


def _tool_step(step_id: str, tool_name: str, output_key: str, input_bindings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "tool",
        "id": step_id,
        "tool_name": tool_name,
        "output_key": output_key,
        "input_bindings": input_bindings,
    }


def _agent_step(
    step_id: str,
    agent_id: str,
    output_key: str,
    input_bindings: Dict[str, Any],
    *,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> Dict[str, Any]:
    return {
        "type": "agent",
        "id": step_id,
        "agent_id": agent_id,
        "output_key": output_key,
        "input_bindings": input_bindings,
        "runtime": {"temperature": temperature, "max_tokens": max_tokens},
    }


# Per-agent "blocks" — each returns the ordered (tool + agent + trailing-tool) steps
# needed to run that agent with sensible default bindings sourced from the verification
# loop's initial context (repo_root, target_file, intent, acceptance_criteria, …) and
# any outputs produced by earlier blocks in the workflow.

def _block_context_librarian() -> List[Dict[str, Any]]:
    return [
        {**_tool_step("build_ast_ctx", "build_ast_context", "ast_repo_map_ctx", {
            "repo_root": {"$ref": "repo_root"},
            "target_files": [{"$ref": "target_file"}],
        }), "skip_if_present": True},
        {**_agent_step(
            "context_librarian", "context_librarian_v1", "librarian_output",
            {
                "dag_task": {"$ref": "task_input.intent"},
                "ast_repo_map_json": {"$ref": "ast_repo_map_ctx.ast_repo_map_json"},
            },
            max_tokens=2048,
        ), "skip_if_present": True},
        {**_tool_step("load_files", "load_file_contents", "file_contexts", {
            "repo_root": {"$ref": "repo_root"},
            "required_files": {"$ref": "librarian_output.required_files"},
        }), "skip_if_present": True},
    ]


def _block_sdet() -> List[Dict[str, Any]]:
    return [
        {**_agent_step(
            "sdet", "sdet_v1", "sdet_output",
            {
                "dag_task": {"$ref": "task_input.intent"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
                "context_chunks_json": {"$ref": "file_contexts.file_contexts_json"},
                "testing_standards": (
                    "Follow the repository's existing testing patterns. "
                    "Mock external dependencies. Write focused, edge-case-aware tests. "
                    "Ensure tests fail before the implementation exists."
                ),
            },
            max_tokens=4096,
            temperature=0.1,
        ), "skip_if_present": True},
    ]


def _block_coder_builder(has_sdet: bool) -> List[Dict[str, Any]]:
    write_bindings: Dict[str, Any] = {
        "repo_root": {"$ref": "repo_root"},
        "modified_files": {"$ref": "coder_output.modified_files"},
    }
    if has_sdet:
        write_bindings["test_files"] = {"$ref": "sdet_output.test_files"}
    return [
        _agent_step(
            "coder_builder", "coder_builder_v1", "coder_output",
            {
                "dag_task": {"$ref": "task_input.intent"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
                "file_contexts_json": {"$ref": "file_contexts.file_contexts_json"},
            },
            max_tokens=8192,
        ),
        _tool_step("write_files", "write_all_files", "diff_json", write_bindings),
    ]


def _block_doc_fetcher() -> List[Dict[str, Any]]:
    return [
        {**_tool_step("fetch_doc_context", "fetch_doc_content", "doc_context", {
            "task_id": {"$ref": "task_id"},
        }), "skip_if_present": True},
        {**_agent_step(
            "doc_fetcher", "doc_fetcher_v1", "doc_fetcher_output",
            {
                "doc_url": {"$ref": "doc_context.doc_url"},
                "doc_content": {"$ref": "doc_context.doc_content"},
                "intent": {"$ref": "intent"},
                "target_file": {"$ref": "target_file"},
                "acceptance_criteria": {"$ref": "acceptance_criteria"},
            },
            max_tokens=3072,
        ), "skip_if_present": True},
        _tool_step("augment_task_inputs", "build_augmented_task_inputs", "task_input", {
            "intent": {"$ref": "intent"},
            "acceptance_criteria": {"$ref": "acceptance_criteria"},
            "doc_fetcher_output": {"$ref": "doc_fetcher_output"},
            "task_notes": {"$ref": "doc_context.notes"},
        }),
    ]


def _block_coder_patch() -> List[Dict[str, Any]]:
    return [
        _tool_step("build_repo_map_ctx", "get_focused_context", "repo_map_ctx", {
            "repo_root": {"$ref": "repo_root"},
            "target_files": [{"$ref": "target_file"}],
            "depth": 1,
        }),
        _tool_step("snapshot_target", "snapshot_target_file", "target_file_content", {
            "repo_root": {"$ref": "repo_root"},
            "target_file": {"$ref": "target_file"},
        }),
        _agent_step(
            "coder_patch", "coder_patch_v1", "coder_patch",
            {
                "repo_map_json": {"$ref": "repo_map_ctx.repo_map_json"},
                "intent": {"$ref": "task_input.intent"},
                "target_file": {"$ref": "target_file"},
                "target_file_content": {"$ref": "target_file_content"},
            },
            max_tokens=1024,
        ),
        _tool_step("apply_patch", "apply_line_patch_and_validate", "diff_json", {
            "repo_root": {"$ref": "repo_root"},
            "patch": {"$ref": "coder_patch"},
            "allowed_paths": {"$ref": "repo_map_ctx.allowed_paths"},
        }),
    ]


def _block_security_governor() -> List[Dict[str, Any]]:
    return [
        _tool_step("load_rules", "load_rules", "rules", {"repo_root": {"$ref": "repo_root"}}),
        _agent_step(
            "security_governor", "security_governor_v1", "governor_output",
            {
                "diff_json": {"$ref": "diff_json.diff_json_str"},
                "repo_rules_text": {"$ref": "rules.repo_rules_text"},
            },
            max_tokens=2048,
        ),
    ]


def _block_judge_evaluator() -> List[Dict[str, Any]]:
    return [
        _tool_step("run_tests", "run_tests", "test_results", {
            "repo_root": {"$ref": "repo_root"},
            "test_file_paths": {"$ref": "diff_json.test_file_paths"},
        }),
        _agent_step(
            "judge_evaluator", "judge_evaluator_v1", "judge_eval",
            {
                "dag_task": {"$ref": "task_input.intent"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
                "coder_diff_json": {"$ref": "diff_json.diff_json_str"},
                "sdet_tests_json": {"$ref": "diff_json.diff_json_str"},
                "terminal_logs": {"$ref": "test_results.terminal_logs"},
            },
            max_tokens=4096,
        ),
    ]


def _block_diff_validator() -> List[Dict[str, Any]]:
    return [
        _tool_step("collect_git_diff", "get_git_diff", "git_diff_ctx", {
            "repo_root": {"$ref": "repo_root"},
        }),
        _tool_step("load_rules_for_diff", "load_rules", "diff_rules", {"repo_root": {"$ref": "repo_root"}}),
        _agent_step(
            "diff_validator", "diff_validator_v1", "diff_validator_output",
            {
                "intent": {"$ref": "task_input.intent"},
                "target_file": {"$ref": "target_file"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
                "git_diff": {"$ref": "git_diff_ctx.git_diff"},
                "allowed_paths": {"$ref": "allowed_paths"},
                "repo_rules_text": {"$ref": "diff_rules.repo_rules_text"},
            },
            max_tokens=3072,
        ),
    ]


def _block_linter() -> List[Dict[str, Any]]:
    return [
        _tool_step("snapshot_for_lint", "snapshot_target_file", "lint_file_content", {
            "repo_root": {"$ref": "repo_root"},
            "target_file": {"$ref": "target_file"},
        }),
        _tool_step("run_linter", "run_linter", "lint_run", {
            "repo_root": {"$ref": "repo_root"},
            "target_file": {"$ref": "target_file"},
        }),
        _agent_step(
            "linter", "linter_v1", "linter_output",
            {
                "target_file": {"$ref": "lint_run.target_file"},
                "language": {"$ref": "lint_run.language"},
                "file_content": {"$ref": "lint_file_content"},
                "linter_raw_output": {"$ref": "lint_run.linter_raw_output"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
            },
            max_tokens=3072,
        ),
    ]


def _block_type_checker() -> List[Dict[str, Any]]:
    return [
        _tool_step("snapshot_for_typecheck", "snapshot_target_file", "type_file_content", {
            "repo_root": {"$ref": "repo_root"},
            "target_file": {"$ref": "target_file"},
        }),
        _tool_step("run_typecheck", "run_typecheck", "typecheck_run", {
            "repo_root": {"$ref": "repo_root"},
            "target_file": {"$ref": "target_file"},
        }),
        _agent_step(
            "type_checker", "type_checker_v1", "type_checker_output",
            {
                "target_file": {"$ref": "typecheck_run.target_file"},
                "language": {"$ref": "typecheck_run.language"},
                "file_content": {"$ref": "type_file_content"},
                "type_checker_output": {"$ref": "typecheck_run.type_checker_output"},
                "intent": {"$ref": "task_input.intent"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
            },
            max_tokens=3072,
        ),
    ]


def _block_judge_v1() -> List[Dict[str, Any]]:
    return [
        _tool_step("snapshot_edited_snippet", "snapshot_target_file", "edited_snippet", {
            "repo_root": {"$ref": "repo_root"},
            "target_file": {"$ref": "target_file"},
        }),
        _tool_step("infer_language", "infer_language_from_target_file", "language", {
            "target_file": {"$ref": "target_file"},
        }),
        _tool_step("build_tool_logs_json", "build_tool_logs_json", "tool_logs_json", {
            "static_check_logs": {"$ref": "static_check_logs"},
        }),
        _agent_step(
            "judge", "judge_v1", "snippet_judge_response",
            {
                "repo_path": {"$ref": "repo_path"},
                "target_file": {"$ref": "target_file"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
                "original_snippet": {"$ref": "original_snippet"},
                "edited_snippet": {"$ref": "edited_snippet"},
                "language": {"$ref": "language"},
                "tool_logs_json": {"$ref": "tool_logs_json"},
            },
            max_tokens=6144,
        ),
    ]


def _block_scribe() -> List[Dict[str, Any]]:
    return [
        _agent_step(
            "scribe", "scribe_v1", "scribe_output",
            {
                "task_id": {"$ref": "task_id"},
                "dag_id": {"$ref": "dag_id"},
                "intent": {"$ref": "task_input.intent"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
                "final_verdict": {"$ref": "judge_response.verdict"},
                "resolution_summary": {"$ref": "judge_response.justification"},
            },
            max_tokens=2048,
        ),
    ]


def _block_memory_writer() -> List[Dict[str, Any]]:
    return [
        _agent_step(
            "memory_writer", "memory_writer_v1", "memory_writer_output",
            {
                "task_id": {"$ref": "task_id"},
                "dag_id": {"$ref": "dag_id"},
                "target_file": {"$ref": "target_file"},
                "intent": {"$ref": "task_input.intent"},
                "acceptance_criteria": {"$ref": "task_input.acceptance_criteria"},
                "final_verdict": {"$ref": "judge_response.verdict"},
                "attempt_count": {"$ref": "attempt_count"},
                "error_history": {"$ref": "error_history"},
                "patch_summaries": {"$ref": "patch_summaries"},
                "resolution_summary": {"$ref": "judge_response.justification"},
            },
            max_tokens=2048,
        ),
    ]


def _split_workflow(workflow: List[str]) -> Tuple[List[str], List[str]]:
    """Partition the ordered agent list into coder-side and judge-side agents (order preserved)."""
    coder: List[str] = []
    judge: List[str] = []
    for aid in workflow:
        if aid in _JUDGE_SIDE_AGENTS:
            judge.append(aid)
        elif aid in _CODER_SIDE_AGENTS:
            coder.append(aid)
        # Unknown agent ids are silently skipped; the intent compiler prompt enumerates the valid roster.
    return coder, judge


def build_workflow_coder_chain(workflow: List[str], task: Any | None = None) -> Dict[str, Any]:
    """
    Compose a coder chain from an ordered agent_id list.

    Falls back to `default_coder_chain` if the coder side of the workflow is empty or
    contains only unrecognised agents — this keeps bespoke workflows from producing
    a no-op coder chain that would leave the diff empty.
    """
    coder_side, _ = _split_workflow(workflow)
    if not coder_side:
        return default_coder_chain(task)

    has_doc_fetcher = "doc_fetcher_v1" in coder_side
    has_librarian = "context_librarian_v1" in coder_side
    has_sdet = "sdet_v1" in coder_side
    has_builder = "coder_builder_v1" in coder_side
    has_patch = "coder_patch_v1" in coder_side

    # `coder_builder_v1` / `sdet_v1` require file_contexts from the librarian.
    # Auto-prepend the librarian block when they are used without it.
    needs_librarian = (has_builder or has_sdet) and not has_librarian

    steps: List[Dict[str, Any]] = []
    if has_doc_fetcher:
        steps.extend(_block_doc_fetcher())
    if needs_librarian:
        steps.extend(_block_context_librarian())
    if has_librarian:
        steps.extend(_block_context_librarian())
    if has_sdet:
        steps.extend(_block_sdet())
    if has_builder:
        steps.extend(_block_coder_builder(has_sdet=has_sdet))
    if has_patch:
        steps.extend(_block_coder_patch())

    if not steps:
        return default_coder_chain(task)
    return {"steps": steps}


def build_workflow_judge_chain(workflow: List[str], task: Any | None = None) -> Dict[str, Any]:
    """
    Compose a judge chain from an ordered agent_id list.

    Falls back to `default_judge_chain` when the judge side is empty — every DAG node
    needs at least one verdict emitter, so silently defaulting is safer than producing
    an empty chain.
    """
    _, judge_side = _split_workflow(workflow)
    if not judge_side:
        return default_judge_chain(task)

    has_governor = "security_governor_v1" in judge_side
    has_diff_validator = "diff_validator_v1" in judge_side
    has_linter = "linter_v1" in judge_side
    has_type_checker = "type_checker_v1" in judge_side
    has_evaluator = "judge_evaluator_v1" in judge_side
    has_judge_v1 = "judge_v1" in judge_side
    has_scribe = "scribe_v1" in judge_side
    has_memory_writer = "memory_writer_v1" in judge_side

    steps: List[Dict[str, Any]] = []
    if has_governor:
        steps.extend(_block_security_governor())
    if has_diff_validator:
        steps.extend(_block_diff_validator())
    if has_linter:
        steps.extend(_block_linter())
    if has_type_checker:
        steps.extend(_block_type_checker())
    if has_evaluator:
        steps.extend(_block_judge_evaluator())

    # Map evaluator (+ optional governor) outputs to the judge_response shape.
    if has_evaluator:
        map_bindings: Dict[str, Any] = {
            "pass_tests": {"$ref": "judge_eval.pass_tests"},
            "evaluation_reasoning": {"$ref": "judge_eval.evaluation_reasoning"},
            "feedback_for_coder": {"$ref": "judge_eval.feedback_for_coder"},
        }
        if has_governor:
            map_bindings["is_safe"] = {"$ref": "governor_output.is_safe"}
            map_bindings["violations"] = {"$ref": "governor_output.violations"}
        if has_diff_validator:
            map_bindings["diff_validator_output"] = {"$ref": "diff_validator_output"}
        if has_linter:
            map_bindings["linter_output"] = {"$ref": "linter_output"}
        if has_type_checker:
            map_bindings["type_checker_output"] = {"$ref": "type_checker_output"}
        steps.append(_tool_step("map_verdict", "map_evaluator_verdict", "provisional_judge_response", map_bindings))

    if has_judge_v1:
        steps.extend(_block_judge_v1())

    finalize_needed = has_evaluator or has_judge_v1 or has_diff_validator or has_governor or has_linter or has_type_checker
    if finalize_needed:
        finalize_bindings: Dict[str, Any] = {}
        if has_evaluator:
            finalize_bindings["base_judge_response"] = {"$ref": "provisional_judge_response"}
        if has_judge_v1:
            finalize_bindings["snippet_judge_response"] = {"$ref": "snippet_judge_response"}
        if has_diff_validator:
            finalize_bindings["diff_validator_output"] = {"$ref": "diff_validator_output"}
        if has_governor:
            finalize_bindings["governor_output"] = {"$ref": "governor_output"}
        if has_linter:
            finalize_bindings["linter_output"] = {"$ref": "linter_output"}
        if has_type_checker:
            finalize_bindings["type_checker_output"] = {"$ref": "type_checker_output"}
        steps.append(_tool_step("finalize_verdict", "finalize_judge_verdict", "judge_response", finalize_bindings))

    if has_scribe:
        steps.extend(_block_scribe())
    if has_memory_writer:
        steps.extend(_block_memory_writer())

    if not steps:
        return default_judge_chain(task)
    return {"steps": steps}


def default_intent_compiler_chain() -> Dict[str, Any]:
    """
    Default intent compilation chain:
      build_repo_map_context tool -> intent_compiler_v1 agent -> typed nodes+edges JSON
    """
    return {
        "steps": [
            {
                "type": "tool",
                "id": "build_repo_map_ctx",
                "output_key": "repo_map_ctx",
                "tool_name": "build_repo_map_context",
                "input_bindings": {"repo_root": {"$ref": "repo_path"}},
            },
            {
                "type": "agent",
                "id": "intent_compiler",
                "output_key": "intent_compiler_output",
                "agent_id": "intent_compiler_v1",
                "input_bindings": {
                    "macro_intent": {"$ref": "macro_intent"},
                    "repo_path": {"$ref": "repo_path"},
                    "repo_map_json": {"$ref": "repo_map_ctx.repo_map_json"},
                },
                # Intent compiler produces a DAG spec JSON (nodes + edges).
                # Historical max ~16K chars (~4K tokens). Cap at 6144 with headroom.
                "runtime": {"temperature": 0.0, "max_tokens": 6144},
            }
        ]
    }

