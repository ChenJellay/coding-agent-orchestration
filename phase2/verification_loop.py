from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.graph import END, StateGraph

from phase2.checkpointing import (
    Checkpoint,
    EditTaskSpec,
    VerificationStatus,
    create_pre_checkpoint,
    record_post_state,
    rollback_to_checkpoint,
    snapshot_file,
)
from phase2.config import DEFAULT_CONFIG
from phase2.debug_log import log_event
from phase2.judge_client import JudgeClient, JudgeRequest
from single_agent_harness import run_single_agent_edit


@dataclass
class VerificationState:
    """Mutable state that flows through the LangGraph verification loop."""

    task: EditTaskSpec
    checkpoint: Optional[Checkpoint] = None
    diff_json: Optional[Dict[str, Any]] = None
    original_content: Optional[str] = None
    static_check_logs: Dict[str, Any] = None  # type: ignore[assignment]
    judge_response: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    feedback: str = ""
    coder_error: Optional[str] = None


def _resolve_target_path(task: EditTaskSpec) -> Path:
    return Path(task.repo_path).resolve() / task.target_file


def node_take_pre_checkpoint(state: VerificationState) -> VerificationState:
    target_path = _resolve_target_path(state.task)
    original = snapshot_file(target_path)
    pre_state_ref = original
    checkpoint = create_pre_checkpoint(state.task, pre_state_ref)
    checkpoint.status = VerificationStatus.RUNNING
    state.checkpoint = checkpoint
    state.original_content = original
    # region agent log
    log_event(
        run_id="pre",
        hypothesis_id="H1",
        location="phase2/verification_loop.py:node_take_pre_checkpoint",
        message="Created pre-checkpoint and captured original file snapshot",
        data={"task_id": state.task.task_id, "target_file": state.task.target_file, "checkpoint_id": checkpoint.checkpoint_id},
    )
    # endregion
    return state


def node_run_coder(state: VerificationState) -> VerificationState:
    """
    Run the Phase 1 single-agent edit using run_single_agent_edit.

    The existing primitive returns a LinePatch; we convert it to a simple
    dict for downstream use and also capture the patch JSON as a diff.
    """
    repo_root = Path(state.task.repo_path).resolve()
    # Incorporate feedback into the intent if present.
    intent = state.task.intent
    if state.feedback:
        intent = f"{intent}\n\nPrevious attempt feedback from Judge and tools:\n{state.feedback}"

    try:
        patch = run_single_agent_edit(repo_root, intent)
        state.diff_json = patch.__dict__
        state.coder_error = None
        # region agent log
        log_event(
            run_id="pre",
            hypothesis_id="H2",
            location="phase2/verification_loop.py:node_run_coder",
            message="Coder applied patch via Phase 1 primitive",
            data={"task_id": state.task.task_id, "patch": state.diff_json},
        )
        # endregion
        return state
    except Exception as exc:
        # Convert coder failures (e.g. model didn't emit JSON) into a structured
        # failure so Phase 2 can retry/rollback instead of crashing.
        state.diff_json = None
        state.coder_error = f"{type(exc).__name__}: {exc}"
        state.judge_response = {
            "verdict": "FAIL",
            "justification": f"Coder failed before verification: {state.coder_error}",
            "problematic_lines": [],
        }
        # region agent log
        log_event(
            run_id="pre",
            hypothesis_id="H2",
            location="phase2/verification_loop.py:node_run_coder",
            message="Coder failed (will be treated as verification FAIL)",
            data={"task_id": state.task.task_id, "error": state.coder_error},
        )
        # endregion
        return state


def _run_static_checks_for_demo_repo(repo_root: Path) -> Dict[str, Any]:
    """
    Run lightweight static checks for the demo repo.

    For now, this is a placeholder that can be extended to run npm-based
    commands. To keep Phase 2 self-contained and fast, we simply return
    an empty success log.
    """
    return {
        "status": "SKIPPED",
        "reason": "No static checks configured for Phase 2 demo.",
    }


def node_run_static_checks(state: VerificationState) -> VerificationState:
    repo_root = Path(state.task.repo_path).resolve()
    logs = _run_static_checks_for_demo_repo(repo_root)
    state.static_check_logs = logs
    # region agent log
    log_event(
        run_id="pre",
        hypothesis_id="H3",
        location="phase2/verification_loop.py:node_run_static_checks",
        message="Ran static checks step (demo repo)",
        data={"task_id": state.task.task_id, "static_check_logs": logs},
    )
    # endregion
    return state


def node_call_judge(state: VerificationState) -> VerificationState:
    assert state.checkpoint is not None, "Checkpoint must be created before judge call"
    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file

    original_snippet = state.original_content or ""
    edited_snippet = target_path.read_text()

    # Best-effort language guess from extension.
    language = target_path.suffix.lstrip(".") or "text"

    tool_logs = {
        "static_checks": state.static_check_logs or {},
    }

    judge_request = JudgeRequest(
        repo_path=str(repo_root),
        target_file=state.task.target_file,
        acceptance_criteria=state.task.acceptance_criteria,
        original_snippet=original_snippet,
        edited_snippet=edited_snippet,
        language=language,
        tool_logs=tool_logs,
    )

    client = JudgeClient()
    response = client.evaluate(judge_request)
    state.judge_response = {
        "verdict": response.verdict,
        "justification": response.justification,
        "problematic_lines": response.problematic_lines,
    }
    # region agent log
    log_event(
        run_id="pre",
        hypothesis_id="H4",
        location="phase2/verification_loop.py:node_call_judge",
        message="Judge evaluated edit",
        data={"task_id": state.task.task_id, "verdict": response.verdict, "problematic_lines": response.problematic_lines},
    )
    # endregion
    return state


def node_handle_verdict(state: VerificationState) -> VerificationState:
    assert state.checkpoint is not None, "Checkpoint must be set before verdict handling"
    cfg = DEFAULT_CONFIG
    verdict = (state.judge_response or {}).get("verdict", "FAIL")
    justification = (state.judge_response or {}).get("justification", "")

    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file

    if verdict.upper() == "PASS":
        record_post_state(
            state.checkpoint,
            post_state_ref=target_path.read_text(),
            diff=json.dumps(state.diff_json or {}, indent=2),
            tool_logs={"judge": state.judge_response, "static_checks": state.static_check_logs or {}},
            status=VerificationStatus.PASSED,
        )
        # region agent log
        log_event(
            run_id="pre",
            hypothesis_id="H5",
            location="phase2/verification_loop.py:node_handle_verdict",
            message="Marked checkpoint PASSED",
            data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id},
        )
        # endregion
        return state

    # Verdict failed. Treat retry_count as "number of failed verification cycles so far".
    state.retry_count += 1
    if state.retry_count >= cfg.max_retries:
        record_post_state(
            state.checkpoint,
            post_state_ref=target_path.read_text(),
            diff=json.dumps(state.diff_json or {}, indent=2),
            tool_logs={"judge": state.judge_response, "static_checks": state.static_check_logs or {}},
            status=VerificationStatus.BLOCKED,
        )
        # region agent log
        log_event(
            run_id="pre",
            hypothesis_id="H5",
            location="phase2/verification_loop.py:node_handle_verdict",
            message="Marked checkpoint BLOCKED (retries exhausted)",
            data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id, "retry_count": state.retry_count},
        )
        # endregion
        return state

    # Need to rollback and prepare feedback for another attempt.
    rollback_to_checkpoint(
        state.task,
        state.checkpoint,
        original_content=state.original_content,
    )

    feedback_lines = [
        "Judge reported a failure.",
        f"Justification: {justification}",
    ]
    state.feedback = "\n".join(feedback_lines)
    # region agent log
    log_event(
        run_id="pre",
        hypothesis_id="H5",
        location="phase2/verification_loop.py:node_handle_verdict",
        message="Rolled back and scheduled retry",
        data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id, "retry_count": state.retry_count},
    )
    # endregion
    return state


def build_verification_graph() -> StateGraph:
    graph = StateGraph(VerificationState)

    graph.add_node("take_pre_checkpoint", node_take_pre_checkpoint)
    graph.add_node("run_coder", node_run_coder)
    graph.add_node("run_static_checks", node_run_static_checks)
    graph.add_node("call_judge", node_call_judge)
    graph.add_node("handle_verdict", node_handle_verdict)

    graph.set_entry_point("take_pre_checkpoint")

    # Linear path for one attempt.
    graph.add_edge("take_pre_checkpoint", "run_coder")

    # If coder failed, skip static checks + judge and go straight to verdict handling.
    def _coder_ok(state: VerificationState) -> str:
        return "ok" if not state.coder_error else "error"

    graph.add_conditional_edges(
        "run_coder",
        _coder_ok,
        {
            "ok": "run_static_checks",
            "error": "handle_verdict",
        },
    )

    graph.add_edge("run_static_checks", "call_judge")
    graph.add_edge("call_judge", "handle_verdict")

    # After handling verdict:
    # - If passed or blocked, we terminate.
    # - If failing with retries left, we loop back to run_coder.
    def _should_retry(state: VerificationState) -> str:
        # Terminate if we've reached a terminal checkpoint state.
        if state.checkpoint is not None and state.checkpoint.status in (
            VerificationStatus.PASSED,
            VerificationStatus.BLOCKED,
        ):
            return END

        if state.judge_response is None:
            return END

        verdict = state.judge_response.get("verdict", "FAIL").upper()
        if verdict == "PASS":
            return END

        # Loop back for another attempt.
        return "run_coder"

    graph.add_conditional_edges(
        "handle_verdict",
        _should_retry,
        {
            "run_coder": "run_coder",
            END: END,
        },
    )

    return graph


def run_verification_loop(task: EditTaskSpec) -> VerificationState:
    """
    Execute the Phase 2 verification loop for a single task.

    Returns the final VerificationState after the graph finishes.
    """
    graph = build_verification_graph()
    app = graph.compile()
    initial_state = VerificationState(task=task)
    # region agent log
    log_event(
        run_id="pre",
        hypothesis_id="H1",
        location="phase2/verification_loop.py:run_verification_loop",
        message="Starting verification loop",
        data={"task_id": task.task_id, "repo_path": task.repo_path, "target_file": task.target_file},
    )
    # endregion
    final_state = app.invoke(initial_state)
    # region agent log
    if isinstance(final_state, dict):
        final_state_obj = VerificationState(**final_state)
    else:
        final_state_obj = final_state

    status = final_state_obj.checkpoint.status.value if final_state_obj.checkpoint else None
    log_event(
        run_id="pre",
        hypothesis_id="H1",
        location="phase2/verification_loop.py:run_verification_loop",
        message="Finished verification loop",
        data={"task_id": task.task_id, "status": status, "retry_count": final_state_obj.retry_count},
    )
    # endregion
    return final_state_obj

