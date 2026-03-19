from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.graph import END, StateGraph

from agenti_helix.observability.debug_log import log_event
from agenti_helix.single_agent.harness import run_single_agent_edit

from .checkpointing import (
    Checkpoint,
    EditTaskSpec,
    VerificationStatus,
    create_pre_checkpoint,
    record_post_state,
    rollback_to_checkpoint,
    snapshot_file,
)
from .config import DEFAULT_CONFIG
from .judge_client import JudgeClient, JudgeRequest


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
    log_event(
        run_id="pre",
        hypothesis_id="H1",
        location="agenti_helix/verification/verification_loop.py:node_take_pre_checkpoint",
        message="Created pre-checkpoint and captured original file snapshot",
        data={"task_id": state.task.task_id, "target_file": state.task.target_file, "checkpoint_id": checkpoint.checkpoint_id},
    )
    return state


def node_run_coder(state: VerificationState) -> VerificationState:
    """
    Run the single-agent edit using run_single_agent_edit.

    The primitive returns a LinePatch; we convert it to a dict for downstream use.
    """
    repo_root = Path(state.task.repo_path).resolve()
    intent = state.task.intent
    if state.feedback:
        intent = f"{intent}\n\nPrevious attempt feedback from Judge and tools:\n{state.feedback}"

    try:
        patch = run_single_agent_edit(repo_root, intent)
        state.diff_json = patch.__dict__
        state.coder_error = None
        log_event(
            run_id="pre",
            hypothesis_id="H2",
            location="agenti_helix/verification/verification_loop.py:node_run_coder",
            message="Coder applied patch via single-agent primitive",
            data={"task_id": state.task.task_id, "patch": state.diff_json},
        )
        return state
    except Exception as exc:
        state.diff_json = None
        state.coder_error = f"{type(exc).__name__}: {exc}"
        state.judge_response = {
            "verdict": "FAIL",
            "justification": f"Coder failed before verification: {state.coder_error}",
            "problematic_lines": [],
        }
        log_event(
            run_id="pre",
            hypothesis_id="H2",
            location="agenti_helix/verification/verification_loop.py:node_run_coder",
            message="Coder failed (will be treated as verification FAIL)",
            data={"task_id": state.task.task_id, "error": state.coder_error},
        )
        return state


def _run_static_checks_for_demo_repo(repo_root: Path) -> Dict[str, Any]:
    return {
        "status": "SKIPPED",
        "reason": "No static checks configured for verification demo.",
    }


def node_run_static_checks(state: VerificationState) -> VerificationState:
    repo_root = Path(state.task.repo_path).resolve()
    logs = _run_static_checks_for_demo_repo(repo_root)
    state.static_check_logs = logs
    log_event(
        run_id="pre",
        hypothesis_id="H3",
        location="agenti_helix/verification/verification_loop.py:node_run_static_checks",
        message="Ran static checks step (demo repo)",
        data={"task_id": state.task.task_id, "static_check_logs": logs},
    )
    return state


def node_call_judge(state: VerificationState) -> VerificationState:
    assert state.checkpoint is not None, "Checkpoint must be created before judge call"
    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file

    original_snippet = state.original_content or ""
    edited_snippet = target_path.read_text()

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
    log_event(
        run_id="pre",
        hypothesis_id="H4",
        location="agenti_helix/verification/verification_loop.py:node_call_judge",
        message="Judge evaluated edit",
        data={"task_id": state.task.task_id, "verdict": response.verdict, "problematic_lines": response.problematic_lines},
    )
    return state


def node_handle_verdict(state: VerificationState) -> VerificationState:
    assert state.checkpoint is not None, "Checkpoint must be set before verdict handling"
    cfg = DEFAULT_CONFIG
    verdict = (state.judge_response or {}).get("verdict", "FAIL")
    justification = (state.judge_response or {}).get("justification", "")

    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file

    if str(verdict).upper() == "PASS":
        record_post_state(
            state.checkpoint,
            post_state_ref=target_path.read_text(),
            diff=json.dumps(state.diff_json or {}, indent=2),
            tool_logs={"judge": state.judge_response, "static_checks": state.static_check_logs or {}},
            status=VerificationStatus.PASSED,
        )
        log_event(
            run_id="pre",
            hypothesis_id="H5",
            location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
            message="Marked checkpoint PASSED",
            data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id},
        )
        return state

    state.retry_count += 1
    if state.retry_count >= cfg.max_retries:
        record_post_state(
            state.checkpoint,
            post_state_ref=target_path.read_text(),
            diff=json.dumps(state.diff_json or {}, indent=2),
            tool_logs={"judge": state.judge_response, "static_checks": state.static_check_logs or {}},
            status=VerificationStatus.BLOCKED,
        )
        log_event(
            run_id="pre",
            hypothesis_id="H5",
            location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
            message="Marked checkpoint BLOCKED (retries exhausted)",
            data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id, "retry_count": state.retry_count},
        )
        return state

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
    log_event(
        run_id="pre",
        hypothesis_id="H5",
        location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
        message="Rolled back and scheduled retry",
        data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id, "retry_count": state.retry_count},
    )
    return state


def build_verification_graph() -> StateGraph:
    graph = StateGraph(VerificationState)

    graph.add_node("take_pre_checkpoint", node_take_pre_checkpoint)
    graph.add_node("run_coder", node_run_coder)
    graph.add_node("run_static_checks", node_run_static_checks)
    graph.add_node("call_judge", node_call_judge)
    graph.add_node("handle_verdict", node_handle_verdict)

    graph.set_entry_point("take_pre_checkpoint")

    graph.add_edge("take_pre_checkpoint", "run_coder")

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

    def _should_retry(state: VerificationState) -> str:
        if state.checkpoint is not None and state.checkpoint.status in (
            VerificationStatus.PASSED,
            VerificationStatus.BLOCKED,
        ):
            return END

        if state.judge_response is None:
            return END

        verdict = str(state.judge_response.get("verdict", "FAIL")).upper()
        if verdict == "PASS":
            return END

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
    graph = build_verification_graph()
    app = graph.compile()
    initial_state = VerificationState(task=task)
    log_event(
        run_id="pre",
        hypothesis_id="H1",
        location="agenti_helix/verification/verification_loop.py:run_verification_loop",
        message="Starting verification loop",
        data={"task_id": task.task_id, "repo_path": task.repo_path, "target_file": task.target_file},
    )
    final_state = app.invoke(initial_state)

    if isinstance(final_state, dict):
        final_state_obj = VerificationState(**final_state)
    else:
        final_state_obj = final_state

    status = final_state_obj.checkpoint.status.value if final_state_obj.checkpoint else None
    log_event(
        run_id="pre",
        hypothesis_id="H1",
        location="agenti_helix/verification/verification_loop.py:run_verification_loop",
        message="Finished verification loop",
        data={"task_id": task.task_id, "status": status, "retry_count": final_state_obj.retry_count},
    )
    return final_state_obj

