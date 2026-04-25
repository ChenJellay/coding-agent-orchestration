"""
Atomic chain step builders + thin chain factories.

Every chain executed by ``chain_runtime.run_chain`` is a list of step objects.
Rather than maintain six near-identical hand-written chains for each
combination of TDD / doc / diff_gate / lint_type, we expose small step builder
functions and let ``runtime.run_plan`` compose them from a ``RunPlan``.

Public surface (kept stable for callers):
- ``default_coder_chain`` / ``default_judge_chain``           — patch-style baseline
- ``default_full_pipeline_coder_chain`` / ``..._judge_chain``  — TDD baseline
- ``precompile_doc_enrichment_chain``                          — pre-compile doc merge
- ``default_intent_compiler_chain``                            — intent → DAG
- ``doc_prefix_steps`` / ``diff_validator_gate_steps`` /
  ``lint_type_gate_steps`` / ``judge_evaluator_steps``         — composable blocks
"""
from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Atomic step builders
# ---------------------------------------------------------------------------


def _tool(*, id: str, output_key: str, tool_name: str, input_bindings: Dict[str, Any], skip_if_nonempty_key: str | None = None) -> Dict[str, Any]:
    step: Dict[str, Any] = {
        "type": "tool",
        "id": id,
        "output_key": output_key,
        "tool_name": tool_name,
        "input_bindings": input_bindings,
    }
    if skip_if_nonempty_key:
        step["skip_if_nonempty_key"] = skip_if_nonempty_key
    return step


def _agent(*, id: str, output_key: str, agent_id: str, input_bindings: Dict[str, Any], max_tokens: int, skip_if_nonempty_key: str | None = None) -> Dict[str, Any]:
    step: Dict[str, Any] = {
        "type": "agent",
        "id": id,
        "output_key": output_key,
        "agent_id": agent_id,
        "input_bindings": input_bindings,
        "runtime": {"temperature": 0.0, "max_tokens": max_tokens},
    }
    if skip_if_nonempty_key:
        step["skip_if_nonempty_key"] = skip_if_nonempty_key
    return step


# ─── Coder steps ──────────────────────────────────────────────────────────


def _step_focused_repo_map() -> Dict[str, Any]:
    return _tool(
        id="build_repo_map_ctx",
        output_key="repo_map_ctx",
        tool_name="get_focused_context",
        input_bindings={
            "repo_root": {"$ref": "repo_root"},
            "target_files": [{"$ref": "target_file"}],
            "depth": 1,
        },
    )


def _step_snapshot_target() -> Dict[str, Any]:
    return _tool(
        id="snapshot_target",
        output_key="target_file_content",
        tool_name="snapshot_target_file",
        input_bindings={"repo_root": {"$ref": "repo_root"}, "target_file": {"$ref": "target_file"}},
    )


def _step_coder_patch_agent() -> Dict[str, Any]:
    return _agent(
        id="coder_patch",
        output_key="coder_patch",
        agent_id="coder_patch_v1",
        input_bindings={
            "repo_map_json": {"$ref": "repo_map_ctx.repo_map_json"},
            "intent": {"$ref": "intent"},
            "target_file": {"$ref": "target_file"},
            "target_file_content": {"$ref": "target_file_content"},
        },
        max_tokens=1024,
    )


def _step_apply_patch() -> Dict[str, Any]:
    return _tool(
        id="apply_patch",
        output_key="diff_json",
        tool_name="apply_line_patch_and_validate",
        input_bindings={
            "repo_root": {"$ref": "repo_root"},
            "patch": {"$ref": "coder_patch"},
            "allowed_paths": {"$ref": "repo_map_ctx.allowed_paths"},
        },
    )


# ─── Full-TDD coder steps ─────────────────────────────────────────────────


def _step_ast_repo_map() -> Dict[str, Any]:
    return _tool(
        id="build_ast_ctx",
        output_key="ast_repo_map_ctx",
        tool_name="build_ast_context",
        input_bindings={"repo_root": {"$ref": "repo_root"}, "target_files": [{"$ref": "target_file"}]},
    )


def _step_context_librarian() -> Dict[str, Any]:
    return _agent(
        id="context_librarian",
        output_key="librarian_output",
        agent_id="context_librarian_v1",
        input_bindings={
            "dag_task": {"$ref": "intent"},
            "ast_repo_map_json": {"$ref": "ast_repo_map_ctx.ast_repo_map_json"},
        },
        max_tokens=2048,
    )


def _step_load_files() -> Dict[str, Any]:
    return _tool(
        id="load_files",
        output_key="file_contexts",
        tool_name="load_file_contents",
        input_bindings={
            "repo_root": {"$ref": "repo_root"},
            "required_files": {"$ref": "librarian_output.required_files"},
        },
    )


def _step_sdet() -> Dict[str, Any]:
    return _agent(
        id="sdet",
        output_key="sdet_output",
        agent_id="sdet_v1",
        input_bindings={
            "dag_task": {"$ref": "intent"},
            "acceptance_criteria": {"$ref": "acceptance_criteria"},
            "context_chunks_json": {"$ref": "file_contexts.file_contexts_json"},
            "testing_standards": (
                "Follow the repository's existing testing patterns. "
                "Mock external dependencies. Write focused, edge-case-aware tests. "
                "Ensure tests fail before the implementation exists."
            ),
        },
        max_tokens=3072,
    )


def _step_coder_builder() -> Dict[str, Any]:
    return _agent(
        id="coder_builder",
        output_key="coder_output",
        agent_id="coder_builder_v1",
        input_bindings={
            "dag_task": {"$ref": "intent"},
            "acceptance_criteria": {"$ref": "acceptance_criteria"},
            "file_contexts_json": {"$ref": "file_contexts.file_contexts_json"},
        },
        max_tokens=6144,
    )


def _step_write_files() -> Dict[str, Any]:
    return _tool(
        id="write_files",
        output_key="diff_json",
        tool_name="write_all_files",
        input_bindings={
            "repo_root": {"$ref": "repo_root"},
            "modified_files": {"$ref": "coder_output.modified_files"},
            "test_files": {"$ref": "sdet_output.test_files"},
        },
    )


# ─── Doc-fetcher prefix (composable for both coder chains and pre-compile) ─


def doc_prefix_steps(*, intent_key: str = "intent") -> List[Dict[str, Any]]:
    """Doc fetcher → distill → merge into the named intent key.

    Used both as a coder-chain prefix (intent_key="intent") and standalone
    pre-compile (intent_key="macro_intent").
    """
    return [
        _tool(
            id="fetch_doc" if intent_key == "intent" else "fetch_doc_precompile",
            output_key="doc_fetch",
            tool_name="fetch_doc_content",
            input_bindings={
                "repo_root": {"$ref": "repo_root"},
                "task_id": {"$ref": "task_id"},
                "doc_url": {"$ref": "doc_url"},
            },
        ),
        _agent(
            id="doc_fetcher" if intent_key == "intent" else "doc_fetcher_precompile",
            output_key="doc_fetcher_out",
            agent_id="doc_fetcher_v1",
            input_bindings={
                "doc_url": {"$ref": "doc_fetch.doc_url"},
                "doc_content": {"$ref": "doc_fetch.doc_content"},
                "intent": {"$ref": intent_key},
                "target_file": {"$ref": "target_file"},
                "acceptance_criteria": {"$ref": "acceptance_criteria"},
            },
            max_tokens=3072,
        ),
        _tool(
            id="merge_doc_intent" if intent_key == "intent" else "merge_doc_intent_precompile",
            output_key=intent_key,
            tool_name="merge_doc_into_intent",
            input_bindings={
                "intent": {"$ref": intent_key},
                "doc_fetcher_output": {"$ref": "doc_fetcher_out"},
            },
        ),
    ]


# ─── Judge steps (snippet judge) ──────────────────────────────────────────


def _step_snapshot_edited(*, skip_key: str | None = None) -> Dict[str, Any]:
    return _tool(
        id="snapshot_edited",
        output_key="edited_snippet",
        tool_name="snapshot_target_file",
        input_bindings={"repo_root": {"$ref": "repo_root"}, "target_file": {"$ref": "target_file"}},
        skip_if_nonempty_key=skip_key,
    )


def _step_infer_language(*, skip_key: str | None = None) -> Dict[str, Any]:
    return _tool(
        id="infer_language",
        output_key="language",
        tool_name="infer_language_from_target_file",
        input_bindings={"target_file": {"$ref": "target_file"}},
        skip_if_nonempty_key=skip_key,
    )


def _step_build_tool_logs(*, skip_key: str | None = None) -> Dict[str, Any]:
    return _tool(
        id="build_tool_logs_json",
        output_key="tool_logs_json",
        tool_name="build_tool_logs_json",
        input_bindings={"static_check_logs": {"$ref": "static_check_logs"}},
        skip_if_nonempty_key=skip_key,
    )


def _step_judge_v1(*, skip_key: str | None = None) -> Dict[str, Any]:
    return _agent(
        id="judge",
        output_key="judge_response",
        agent_id="judge_v1",
        input_bindings={
            "repo_path": {"$ref": "repo_path"},
            "target_file": {"$ref": "target_file"},
            "acceptance_criteria": {"$ref": "acceptance_criteria"},
            "original_snippet": {"$ref": "original_snippet"},
            "edited_snippet": {"$ref": "edited_snippet"},
            "language": {"$ref": "language"},
            "tool_logs_json": {"$ref": "tool_logs_json"},
        },
        max_tokens=6144,
        skip_if_nonempty_key=skip_key,
    )


# ─── Judge steps (full TDD pipeline) ──────────────────────────────────────


def _step_run_tests() -> Dict[str, Any]:
    return _tool(
        id="run_tests",
        output_key="test_results",
        tool_name="run_tests",
        input_bindings={
            "repo_root": {"$ref": "repo_root"},
            "test_file_paths": {"$ref": "diff_json.test_file_paths"},
        },
    )


def _step_load_rules() -> Dict[str, Any]:
    return _tool(
        id="load_rules",
        output_key="rules",
        tool_name="load_rules",
        input_bindings={"repo_root": {"$ref": "repo_root"}},
    )


def _step_security_governor() -> Dict[str, Any]:
    return _agent(
        id="security_governor",
        output_key="governor_output",
        agent_id="security_governor_v1",
        input_bindings={
            "diff_json": {"$ref": "diff_json.diff_json_str"},
            "repo_rules_text": {"$ref": "rules.repo_rules_text"},
        },
        max_tokens=2048,
    )


def _step_judge_evaluator(*, skip_key: str | None = None) -> Dict[str, Any]:
    return _agent(
        id="judge_evaluator",
        output_key="judge_eval",
        agent_id="judge_evaluator_v1",
        input_bindings={
            "dag_task": {"$ref": "intent"},
            "acceptance_criteria": {"$ref": "acceptance_criteria"},
            "coder_diff_json": {"$ref": "diff_json.diff_json_str"},
            "sdet_tests_json": {"$ref": "diff_json.diff_json_str"},
            "terminal_logs": {"$ref": "test_results.terminal_logs"},
        },
        max_tokens=4096,
        skip_if_nonempty_key=skip_key,
    )


def _step_map_evaluator_verdict(*, skip_key: str | None = None) -> Dict[str, Any]:
    return _tool(
        id="map_verdict",
        output_key="judge_response",
        tool_name="map_evaluator_verdict",
        input_bindings={
            "pass_tests": {"$ref": "judge_eval.pass_tests"},
            "evaluation_reasoning": {"$ref": "judge_eval.evaluation_reasoning"},
            "feedback_for_coder": {"$ref": "judge_eval.feedback_for_coder"},
            "audit_reasoning": {"$ref": "governor_output.audit_reasoning"},
            "is_safe": {"$ref": "governor_output.is_safe"},
            "violations": {"$ref": "governor_output.violations"},
        },
        skip_if_nonempty_key=skip_key,
    )


# ─── Diff validator gate ──────────────────────────────────────────────────


def _step_git_diff_hdr() -> Dict[str, Any]:
    return _tool(
        id="git_diff_hdr",
        output_key="git_diff_hdr",
        tool_name="get_git_unified_diff",
        input_bindings={
            "repo_root": {"$ref": "repo_root"},
            "target_file": {"$ref": "target_file"},
            "diff_json": {"$ref": "diff_json"},
        },
    )


def _step_diff_validator(*, allowed_paths_ref: str) -> Dict[str, Any]:
    return _agent(
        id="diff_validator",
        output_key="diff_validator_out",
        agent_id="diff_validator_v1",
        input_bindings={
            "intent": {"$ref": "intent"},
            "target_file": {"$ref": "target_file"},
            "acceptance_criteria": {"$ref": "acceptance_criteria"},
            "git_diff": {"$ref": "git_diff_hdr.git_diff"},
            "allowed_paths": {"$ref": allowed_paths_ref},
            "repo_rules_text": {"$ref": "rules.repo_rules_text"},
        },
        max_tokens=3072,
    )


def _step_diff_gate() -> Dict[str, Any]:
    return _tool(
        id="diff_gate",
        output_key="judge_response",
        tool_name="apply_diff_validator_gate",
        input_bindings={"diff_validator_output": {"$ref": "diff_validator_out"}},
    )


def diff_validator_gate_steps(*, allowed_paths_ref: str) -> List[Dict[str, Any]]:
    """Drop-in block: gate the judge on a ``diff_validator_v1`` BLOCK verdict.

    On BLOCK, ``judge_response`` is set short-circuit so all downstream
    judge steps can opt out via ``skip_if_nonempty_key="judge_response"``.
    """
    return [_step_git_diff_hdr(), _step_diff_validator(allowed_paths_ref=allowed_paths_ref), _step_diff_gate()]


# ─── Linter / type-checker gate (lint_type_gate pipeline) ─────────────────


def lint_type_gate_steps() -> List[Dict[str, Any]]:
    return [
        _tool(
            id="run_linter_tool",
            output_key="linter_raw",
            tool_name="run_linter",
            input_bindings={"repo_root": {"$ref": "repo_root"}, "target_file": {"$ref": "target_file"}},
        ),
        _agent(
            id="linter_agent",
            output_key="linter_out",
            agent_id="linter_v1",
            input_bindings={
                "target_file": {"$ref": "target_file"},
                "language": {"$ref": "language"},
                "file_content": {"$ref": "edited_snippet"},
                "linter_raw_output": {"$ref": "linter_raw.raw_output"},
                "acceptance_criteria": {"$ref": "acceptance_criteria"},
            },
            max_tokens=3072,
        ),
        _tool(
            id="run_typecheck_tool",
            output_key="type_raw",
            tool_name="run_typecheck",
            input_bindings={"repo_root": {"$ref": "repo_root"}, "target_file": {"$ref": "target_file"}},
        ),
        _agent(
            id="type_checker_agent",
            output_key="type_out",
            agent_id="type_checker_v1",
            input_bindings={
                "target_file": {"$ref": "target_file"},
                "language": {"$ref": "language"},
                "file_content": {"$ref": "edited_snippet"},
                "type_checker_output": {"$ref": "type_raw.raw_output"},
                "intent": {"$ref": "intent"},
                "acceptance_criteria": {"$ref": "acceptance_criteria"},
            },
            max_tokens=3072,
        ),
        _tool(
            id="overlay_logs",
            output_key="test_results",
            tool_name="overlay_terminal_logs",
            input_bindings={
                "test_results": {"$ref": "test_results"},
                "linter_out": {"$ref": "linter_out"},
                "type_out": {"$ref": "type_out"},
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Public chain factories (used by run_plan + tests)
# ---------------------------------------------------------------------------


def default_coder_chain(_task: Any | None = None) -> Dict[str, Any]:
    """Patch-style coder chain (focused repo map → coder_patch_v1 → apply)."""
    return {"steps": [_step_focused_repo_map(), _step_snapshot_target(), _step_coder_patch_agent(), _step_apply_patch()]}


def default_judge_chain(_task: Any | None = None) -> Dict[str, Any]:
    """Patch-style judge chain (snippet diff judge_v1)."""
    return {
        "steps": [
            _step_snapshot_edited(),
            _step_infer_language(),
            _step_build_tool_logs(),
            _step_judge_v1(),
        ]
    }


def default_full_pipeline_coder_chain(_task: Any | None = None) -> Dict[str, Any]:
    """TDD coder chain: librarian → load → sdet → coder_builder → write_all."""
    return {
        "steps": [
            _step_ast_repo_map(),
            _step_context_librarian(),
            _step_load_files(),
            _step_sdet(),
            _step_coder_builder(),
            _step_write_files(),
        ]
    }


def default_full_pipeline_judge_chain(_task: Any | None = None) -> Dict[str, Any]:
    """TDD judge chain: run_tests → governor → judge_evaluator → map verdict."""
    return {
        "steps": [
            _step_run_tests(),
            _step_load_rules(),
            _step_security_governor(),
            _step_judge_evaluator(),
            _step_map_evaluator_verdict(),
        ]
    }


def precompile_doc_enrichment_chain() -> Dict[str, Any]:
    """Pre-compile doc enrichment: fetch + distill + merge into ``macro_intent``."""
    return {"steps": doc_prefix_steps(intent_key="macro_intent")}


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
            max_tokens=3072,
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
            map_bindings["audit_reasoning"] = {"$ref": "governor_output.audit_reasoning"}
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
    """build_repo_map_context → intent_compiler_v1 → typed nodes/edges JSON."""
    return {
        "steps": [
            _tool(
                id="build_repo_map_ctx",
                output_key="repo_map_ctx",
                tool_name="build_repo_map_context",
                input_bindings={"repo_root": {"$ref": "repo_path"}},
            ),
            _agent(
                id="intent_compiler",
                output_key="intent_compiler_output",
                agent_id="intent_compiler_v1",
                input_bindings={
                    "macro_intent": {"$ref": "macro_intent"},
                    "repo_path": {"$ref": "repo_path"},
                    "repo_map_json": {"$ref": "repo_map_ctx.repo_map_json"},
                },
                max_tokens=6144,
            ),
        ]
    }
