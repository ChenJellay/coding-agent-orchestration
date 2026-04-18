from __future__ import annotations

from typing import Any, Dict, Optional


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
                "runtime": {"temperature": 0.0},
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
                "runtime": {"temperature": 0.0},
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
            {
                "type": "tool",
                "id": "build_ast_ctx",
                "output_key": "ast_repo_map_ctx",
                "tool_name": "build_ast_context",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "target_files": [{"$ref": "target_file"}],
                },
            },
            # 2. Context Librarian: identify required files and symbols.
            {
                "type": "agent",
                "id": "context_librarian",
                "output_key": "librarian_output",
                "agent_id": "context_librarian_v1",
                "input_bindings": {
                    "dag_task": {"$ref": "intent"},
                    "ast_repo_map_json": {"$ref": "ast_repo_map_ctx.ast_repo_map_json"},
                },
                "runtime": {"temperature": 0.0},
            },
            # 3. Load file contents identified by the librarian.
            {
                "type": "tool",
                "id": "load_files",
                "output_key": "file_contexts",
                "tool_name": "load_file_contents",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "required_files": {"$ref": "librarian_output.required_files"},
                },
            },
            # 4. SDET: write tests first (TDD).
            {
                "type": "agent",
                "id": "sdet",
                "output_key": "sdet_output",
                "agent_id": "sdet_v1",
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
                "runtime": {"temperature": 0.1},
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
                "runtime": {"temperature": 0.0},
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
                "runtime": {"temperature": 0.0},
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
                "runtime": {"temperature": 0.0},
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


def default_module_rewriter_chain(task: Any | None = None) -> Dict[str, Any]:
    """
    Module-rewrite coder chain:
      snapshot_target_file → extract_module → coder_module_v1 → splice_module (→ diff_json)

    Extracts the primary function/class from target_file, rewrites it with the LLM,
    then splices the result back in-place. Avoids line-number ambiguity.
    """
    return {
        "steps": [
            {
                "type": "tool",
                "id": "snapshot_file",
                "output_key": "target_file_content",
                "tool_name": "snapshot_target_file",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "target_file": {"$ref": "target_file"},
                },
            },
            {
                "type": "tool",
                "id": "extract_mod",
                "output_key": "module_ctx",
                "tool_name": "extract_module",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "target_file": {"$ref": "target_file"},
                },
            },
            {
                "type": "agent",
                "id": "coder_module",
                "output_key": "coder_module_output",
                "agent_id": "coder_module_v1",
                "input_bindings": {
                    "full_file_content": {"$ref": "target_file_content"},
                    "module_content": {"$ref": "module_ctx.module_content"},
                    "module_start_line": {"$ref": "module_ctx.module_start_line"},
                    "module_end_line": {"$ref": "module_ctx.module_end_line"},
                    "intent": {"$ref": "intent"},
                    "acceptance_criteria": {"$ref": "acceptance_criteria"},
                },
                "runtime": {"temperature": 0.0, "max_tokens": 4096},
            },
            {
                "type": "tool",
                "id": "splice_mod",
                "output_key": "diff_json",
                "tool_name": "splice_module",
                "input_bindings": {
                    "repo_root": {"$ref": "repo_root"},
                    "target_file": {"$ref": "target_file"},
                    "module_start_line": {"$ref": "module_ctx.module_start_line"},
                    "module_end_line": {"$ref": "module_ctx.module_end_line"},
                    "rewritten_module": {"$ref": "coder_module_output.rewritten_module"},
                },
            },
        ]
    }


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
                "runtime": {"temperature": 0.0},
            }
        ]
    }

