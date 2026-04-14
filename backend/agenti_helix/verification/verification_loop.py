from __future__ import annotations

import hashlib
import json
import py_compile
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.graph import END, StateGraph

from agenti_helix.observability.debug_log import log_event
from agenti_helix.runtime.chain_runtime import run_chain
# master_orchestrator is imported lazily inside node_run_coder / node_call_judge
# to break the verification_loop ↔ orchestrator circular dependency.

from agenti_helix.api.task_lookup import record_verification_cycle_snapshot
from agenti_helix.memory.indexer import index_from_verification_state

from .checkpointing import (
    Checkpoint,
    EditTaskSpec,
    VerificationStatus,
    create_pre_checkpoint,
    record_post_state,
    restore_file_from_snapshot,
    rollback_to_checkpoint,
    save_checkpoint,
    snapshot_file,
)
from .config import DEFAULT_CONFIG


@dataclass
class VerificationState:
    """Mutable state that flows through the LangGraph verification loop."""

    task: EditTaskSpec
    checkpoint: Optional[Checkpoint] = None
    diff_json: Optional[Dict[str, Any]] = None
    original_content: Optional[str] = None
    static_check_logs: Dict[str, Any] = field(default_factory=dict)
    judge_response: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    feedback: str = ""
    coder_error: Optional[str] = None
    cancel_token: Any | None = None
    trace_id: Optional[str] = None
    dag_id: Optional[str] = None

    # §4.3 — Context pruning
    error_history: List[str] = field(default_factory=list)  # raw per-attempt errors (capped)
    compressed_context: Optional[str] = None                # LLM-compressed scratchpad

    # §4.4 — Supreme Court
    supreme_court_invoked: bool = False

    # §4.5 — Hybrid escalation
    human_escalation_requested: bool = False
    human_escalation_reason: str = ""


def _is_cancelled(cancel_token: Any | None) -> bool:
    if cancel_token is None:
        return False
    is_cancelled_fn = getattr(cancel_token, "is_cancelled", None)
    if callable(is_cancelled_fn):
        try:
            return bool(is_cancelled_fn())
        except Exception:
            return False
    is_set_fn = getattr(cancel_token, "is_set", None)
    if callable(is_set_fn):
        try:
            return bool(is_set_fn())
        except Exception:
            return False
    return False


def _resolve_target_path(task: EditTaskSpec) -> Path:
    return Path(task.repo_path).resolve() / task.target_file


def _text_fingerprint(text: str) -> Dict[str, Any]:
    """Stable, compact evidence for pre/post code comparisons (logged + merged into dag state)."""
    raw = text.encode("utf-8")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _patch_pipeline(task: EditTaskSpec) -> bool:
    """Line-patch verification requires staging + manual sign-off before workspace is final."""
    return (getattr(task, "pipeline_mode", None) or "patch") == "patch"


def node_take_pre_checkpoint(state: VerificationState) -> VerificationState:
    if _is_cancelled(state.cancel_token):
        return state
    target_path = _resolve_target_path(state.task)
    original = snapshot_file(target_path)
    pre_state_ref = original
    checkpoint = create_pre_checkpoint(state.task, pre_state_ref)
    checkpoint.status = VerificationStatus.RUNNING
    state.checkpoint = checkpoint
    state.original_content = original
    log_event(
        run_id=state.task.task_id,
        hypothesis_id="pre_checkpoint",
        location="agenti_helix/verification/verification_loop.py:node_take_pre_checkpoint",
        message="Created pre-checkpoint and captured original file snapshot",
        data={
            "task_id": state.task.task_id,
            "target_file": state.task.target_file,
            "checkpoint_id": checkpoint.checkpoint_id,
            "pre_execution_fingerprint": _text_fingerprint(original),
        },
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )
    record_verification_cycle_snapshot(
        dag_id=state.dag_id,
        task_id=state.task.task_id,
        verification_cycle=state.retry_count + 1,
        verification_status=VerificationStatus.RUNNING.value,
        code_evidence={"pre_execution": _text_fingerprint(original)},
    )
    return state


def _build_coder_intent(state: VerificationState) -> str:
    """Build the full intent string for the coder, incorporating compressed context."""
    intent = state.task.intent
    parts: List[str] = [intent]

    # §4.3 — Prefer compressed context when available; fall back to raw feedback.
    if state.compressed_context:
        parts.append(f"\n\nCompressed context from previous attempts:\n{state.compressed_context}")
    elif state.feedback:
        # Cap raw feedback to avoid unbounded context growth.
        raw = state.feedback
        if len(raw) > DEFAULT_CONFIG.max_error_history_chars:
            raw = raw[-DEFAULT_CONFIG.max_error_history_chars :]
        parts.append(f"\n\nPrevious attempt feedback from Judge and tools:\n{raw}")

    return "".join(parts)


def node_run_coder(state: VerificationState) -> VerificationState:
    if _is_cancelled(state.cancel_token):
        if state.checkpoint is not None:
            state.checkpoint.status = VerificationStatus.BLOCKED
            save_checkpoint(state.checkpoint)
        return state

    cp_st = state.checkpoint.status.value if state.checkpoint else None
    record_verification_cycle_snapshot(
        dag_id=state.dag_id,
        task_id=state.task.task_id,
        verification_cycle=state.retry_count + 1,
        verification_status=cp_st,
    )

    repo_root = Path(state.task.repo_path).resolve()
    intent = _build_coder_intent(state)

    try:
        from agenti_helix.orchestration.master_orchestrator import resolve_coder_chain  # noqa: PLC0415
        coder_chain = resolve_coder_chain(state.task)
        ctx = {
            "repo_root": repo_root,
            "intent": intent,
            "target_file": state.task.target_file,
            "acceptance_criteria": state.task.acceptance_criteria,
            "repo_path": state.task.repo_path,
        }
        ctx = run_chain(
            chain_spec=coder_chain,
            initial_context=ctx,
            cancel_token=state.cancel_token,
            run_id=state.task.task_id,
            hypothesis_id=f"coder_attempt_{state.retry_count + 1}",
            location_prefix="agenti_helix/verification/verification_loop.py:node_run_coder",
        )
        # §4.5 — Check if the coder requested human escalation via patch output field.
        coder_patch = ctx.get("coder_patch") or {}
        if isinstance(coder_patch, dict) and coder_patch.get("escalate_to_human"):
            state.human_escalation_requested = True
            state.human_escalation_reason = str(coder_patch.get("escalation_reason") or "Coder requested escalation")
            log_event(
                run_id=state.task.task_id,
                hypothesis_id=f"coder_attempt_{state.retry_count + 1}",
                location="agenti_helix/verification/verification_loop.py:node_run_coder",
                message="Coder raised escalation signal",
                data={"task_id": state.task.task_id, "reason": state.human_escalation_reason},
                trace_id=state.trace_id,
                dag_id=state.dag_id,
            )
            return state

        state.diff_json = ctx.get("diff_json")
        state.coder_error = None
        _tp = _resolve_target_path(state.task)
        post_coder_text = _tp.read_text() if _tp.exists() else ""
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=f"coder_attempt_{state.retry_count + 1}",
            location="agenti_helix/verification/verification_loop.py:node_run_coder",
            message="Coder chain produced diff_json",
            data={
                "task_id": state.task.task_id,
                "diff_json": state.diff_json,
                "post_coder_fingerprint": _text_fingerprint(post_coder_text),
            },
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
        record_verification_cycle_snapshot(
            dag_id=state.dag_id,
            task_id=state.task.task_id,
            verification_cycle=state.retry_count + 1,
            verification_status=cp_st,
            code_evidence={"post_coder": _text_fingerprint(post_coder_text)},
        )
        return state
    except Exception as exc:
        if _is_cancelled(state.cancel_token):
            if state.checkpoint is not None:
                state.checkpoint.status = VerificationStatus.BLOCKED
                save_checkpoint(state.checkpoint)
            return state

        state.diff_json = None
        state.coder_error = f"{type(exc).__name__}: {exc}"
        state.judge_response = {
            "verdict": "FAIL",
            "justification": f"Coder failed before verification: {state.coder_error}",
            "problematic_lines": [],
        }
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=f"coder_attempt_{state.retry_count + 1}",
            location="agenti_helix/verification/verification_loop.py:node_run_coder",
            message="Coder failed (will be treated as verification FAIL)",
            data={"task_id": state.task.task_id, "error": state.coder_error},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
        return state


def _check_python_syntax(target_path: Path) -> List[str]:
    """Return a list of syntax error strings, empty on success."""
    errors: List[str] = []
    try:
        py_compile.compile(str(target_path), doraise=True)
    except py_compile.PyCompileError as exc:
        errors.append(str(exc))
    return errors


def _check_python_ruff(target_path: Path) -> List[str]:
    """Run ruff (if available) and return violation strings."""
    errors: List[str] = []
    try:
        result = subprocess.run(
            ["ruff", "check", "--select", "E,F", "--output-format", "text", str(target_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                errors.append(line)
    except FileNotFoundError:
        # ruff not installed — skip this check
        pass
    except subprocess.TimeoutExpired:
        errors.append("ruff check timed out")
    return errors


def _check_js_ts_syntax(target_path: Path) -> List[str]:
    """Use `node --check` for syntax-only validation of JS/TS files."""
    errors: List[str] = []
    try:
        result = subprocess.run(
            ["node", "--check", str(target_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            combined = (result.stderr or result.stdout or "").strip()
            if combined:
                errors.append(combined)
    except FileNotFoundError:
        pass  # node not on PATH — skip
    except subprocess.TimeoutExpired:
        errors.append("node --check timed out")
    return errors


def _check_bandit_security(target_path: Path) -> List[str]:
    """§4.5 — Run bandit security scan on Python files; skip gracefully if not installed.

    Returns critical/high severity findings only to avoid false-positive noise.
    """
    errors: List[str] = []
    try:
        result = subprocess.run(
            [
                "bandit",
                "-r",
                str(target_path),
                "--severity-level", "high",
                "--confidence-level", "high",
                "-f", "txt",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode not in (0, 1):  # 0=no issues, 1=issues found (not tool error)
            return []  # Tool error (e.g., parse failure) — skip scan
        output = (result.stdout or "").strip()
        if output and "No issues identified" not in output:
            # Extract only Issue lines to keep the error list compact.
            for line in output.splitlines():
                if line.startswith(">> Issue:") or line.startswith("Issue:"):
                    errors.append(f"[SECURITY] {line}")
    except FileNotFoundError:
        pass  # bandit not installed — skip security scan
    except subprocess.TimeoutExpired:
        pass  # Timeout is non-fatal; don't block the pipeline
    return errors


def _run_static_checks(repo_root: Path, target_file: str) -> Dict[str, Any]:
    """
    Run syntax, lint, and security checks on the patched target file.

    Returns a dict with keys:
      - passed: bool
      - errors: list of error strings
      - checks_run: list of check names that executed
      - security_blocked: bool (True when critical security findings demand ESCALATE)
    """
    target_path = repo_root / target_file
    if not target_path.exists():
        return {"passed": False, "errors": [f"Target file not found: {target_file}"], "checks_run": [], "security_blocked": False}

    suffix = target_path.suffix.lower()
    errors: List[str] = []
    checks_run: List[str] = []
    security_blocked = False

    if suffix == ".py":
        checks_run.append("py_compile")
        errors.extend(_check_python_syntax(target_path))
        if not errors:
            checks_run.append("ruff")
            errors.extend(_check_python_ruff(target_path))
        # §4.5 — Security scan runs independently (even when ruff passes).
        checks_run.append("bandit")
        sec_errors = _check_bandit_security(target_path)
        if sec_errors:
            errors.extend(sec_errors)
            security_blocked = True  # Critical security findings → force ESCALATE.
    elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        checks_run.append("node_check")
        errors.extend(_check_js_ts_syntax(target_path))

    return {"passed": len(errors) == 0, "errors": errors, "checks_run": checks_run, "security_blocked": security_blocked}


def node_run_static_checks(state: VerificationState) -> VerificationState:
    if _is_cancelled(state.cancel_token):
        if state.checkpoint is not None:
            state.checkpoint.status = VerificationStatus.BLOCKED
            save_checkpoint(state.checkpoint)
        return state
    repo_root = Path(state.task.repo_path).resolve()
    logs = _run_static_checks(repo_root, state.task.target_file)
    state.static_check_logs = logs
    post_static_text = (repo_root / state.task.target_file).read_text() if (repo_root / state.task.target_file).exists() else ""
    log_event(
        run_id=state.task.task_id,
        hypothesis_id=f"static_checks_attempt_{state.retry_count + 1}",
        location="agenti_helix/verification/verification_loop.py:node_run_static_checks",
        message="Static checks completed",
        data={
            "task_id": state.task.task_id,
            "passed": logs.get("passed"),
            "errors": logs.get("errors", []),
            "post_static_checks_fingerprint": _text_fingerprint(post_static_text),
        },
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )
    _cp = state.checkpoint
    record_verification_cycle_snapshot(
        dag_id=state.dag_id,
        task_id=state.task.task_id,
        verification_cycle=state.retry_count + 1,
        verification_status=_cp.status.value if _cp is not None else None,
        code_evidence={"post_static_checks": _text_fingerprint(post_static_text)},
    )
    return state


def node_call_judge(state: VerificationState) -> VerificationState:
    assert state.checkpoint is not None, "Checkpoint must be created before judge call"
    if _is_cancelled(state.cancel_token):
        state.checkpoint.status = VerificationStatus.BLOCKED
        save_checkpoint(state.checkpoint)
        return state
    repo_root = Path(state.task.repo_path).resolve()

    original_snippet = state.original_content or ""
    from agenti_helix.orchestration.master_orchestrator import resolve_judge_chain  # noqa: PLC0415
    judge_chain = resolve_judge_chain(state.task)
    ctx = {
        "repo_root": repo_root,
        "repo_path": state.task.repo_path,
        "target_file": state.task.target_file,
        "acceptance_criteria": state.task.acceptance_criteria,
        "original_snippet": original_snippet,
        "static_check_logs": state.static_check_logs or {},
        # Passed so full-pipeline judge chain can access test file paths and diff metadata.
        "intent": state.task.intent,
        "diff_json": state.diff_json or {},
    }
    attempt_label = f"judge_attempt_{state.retry_count + 1}"
    try:
        ctx = run_chain(
            chain_spec=judge_chain,
            initial_context=ctx,
            cancel_token=state.cancel_token,
            run_id=state.task.task_id,
            hypothesis_id=attempt_label,
            location_prefix="agenti_helix/verification/verification_loop.py:node_call_judge",
        )
    except Exception as exc:
        if _is_cancelled(state.cancel_token):
            state.checkpoint.status = VerificationStatus.BLOCKED
            save_checkpoint(state.checkpoint)
            return state

        # If the judge chain fails, treat as verification FAIL.
        state.judge_response = {
            "verdict": "FAIL",
            "justification": f"Judge failed before verdict: {type(exc).__name__}: {exc}",
            "problematic_lines": [],
        }
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=attempt_label,
            location="agenti_helix/verification/verification_loop.py:node_call_judge",
            message="Judge chain failed (treated as FAIL)",
            data={"task_id": state.task.task_id, "error": state.judge_response["justification"]},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
        return state

    state.judge_response = ctx.get("judge_response")

    log_event(
        run_id=state.task.task_id,
        hypothesis_id=attempt_label,
        location="agenti_helix/verification/verification_loop.py:node_call_judge",
        message="Judge evaluated edit",
        data={
            "task_id": state.task.task_id,
            "verdict": (state.judge_response or {}).get("verdict"),
            "problematic_lines": (state.judge_response or {}).get("problematic_lines"),
        },
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )
    return state


def node_handle_verdict(state: VerificationState) -> VerificationState:
    assert state.checkpoint is not None, "Checkpoint must be set before verdict handling"
    cfg = DEFAULT_CONFIG
    verdict = (state.judge_response or {}).get("verdict", "FAIL")
    justification = (state.judge_response or {}).get("justification", "")

    if _is_cancelled(state.cancel_token):
        # Cancellation means we stop accepting judge verdicts and treat the checkpoint as blocked.
        state.checkpoint.status = VerificationStatus.BLOCKED
        save_checkpoint(state.checkpoint)
        return state

    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file

    verdict_label = f"verdict_attempt_{state.retry_count + 1}"

    if str(verdict).upper() == "PASS":
        post_judge_body = target_path.read_text()
        tool_logs_base = {"judge": state.judge_response, "static_checks": state.static_check_logs or {}}
        if _patch_pipeline(state.task):
            record_post_state(
                state.checkpoint,
                post_state_ref=post_judge_body,
                diff=json.dumps(state.diff_json or {}, indent=2),
                tool_logs=tool_logs_base,
                status=VerificationStatus.PASSED_PENDING_SIGNOFF,
            )
            # Roll back only the workspace file — do not call ``rollback_to_checkpoint`` here because
            # that resets checkpoint metadata to RUNNING for retry flows.
            pre_body = state.original_content if state.original_content is not None else state.checkpoint.pre_state_ref
            restore_file_from_snapshot(target_path, pre_body)
            log_event(
                run_id=state.task.task_id,
                hypothesis_id=verdict_label,
                location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
                message="Judge PASS — staged post-state; workspace rolled back pending manual sign-off",
                data={
                    "task_id": state.task.task_id,
                    "checkpoint_id": state.checkpoint.checkpoint_id,
                    "post_judge_fingerprint": _text_fingerprint(post_judge_body),
                    "workspace_after_rollback_fingerprint": _text_fingerprint(
                        target_path.read_text() if target_path.exists() else ""
                    ),
                },
                trace_id=state.trace_id,
                dag_id=state.dag_id,
            )
        else:
            record_post_state(
                state.checkpoint,
                post_state_ref=post_judge_body,
                diff=json.dumps(state.diff_json or {}, indent=2),
                tool_logs=tool_logs_base,
                status=VerificationStatus.PASSED,
            )
            log_event(
                run_id=state.task.task_id,
                hypothesis_id=verdict_label,
                location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
                message="Marked checkpoint PASSED",
                data={
                    "task_id": state.task.task_id,
                    "checkpoint_id": state.checkpoint.checkpoint_id,
                    "post_judge_fingerprint": _text_fingerprint(post_judge_body),
                },
                trace_id=state.trace_id,
                dag_id=state.dag_id,
            )
        record_verification_cycle_snapshot(
            dag_id=state.dag_id,
            task_id=state.task.task_id,
            verification_cycle=state.retry_count + 1,
            verification_status=state.checkpoint.status.value,
            code_evidence={
                "post_judge": _text_fingerprint(post_judge_body),
                "comparison_pre_vs_post_judge": {
                    "pre_sha256": _text_fingerprint(state.original_content or "")["sha256"],
                    "post_judge_sha256": _text_fingerprint(post_judge_body)["sha256"],
                    "identical": (state.original_content or "") == post_judge_body,
                },
            },
        )
        return state

    # §4.3 — Append to error_history (with cap to avoid unbounded growth).
    error_entry = f"Attempt {state.retry_count + 1}: {justification}"
    state.error_history.append(error_entry)
    if sum(len(e) for e in state.error_history) > cfg.max_error_history_chars * 3:
        # Keep only the most recent entries when history grows excessively.
        state.error_history = state.error_history[-5:]

    state.retry_count += 1
    if state.retry_count >= cfg.max_retries:
        if not cfg.supreme_court_enabled:
            record_post_state(
                state.checkpoint,
                post_state_ref=target_path.read_text(),
                diff=json.dumps(state.diff_json or {}, indent=2),
                tool_logs={"judge": state.judge_response, "static_checks": state.static_check_logs or {}, "supreme_court_invoked": state.supreme_court_invoked},
                status=VerificationStatus.BLOCKED,
            )
            log_event(
                run_id=state.task.task_id,
                hypothesis_id=verdict_label,
                location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
                message="Marked checkpoint BLOCKED (retries exhausted, SC disabled)",
                data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id, "retry_count": state.retry_count},
                trace_id=state.trace_id,
                dag_id=state.dag_id,
            )
        else:
            # §4.4 — Route to Supreme Court instead of immediately blocking.
            log_event(
                run_id=state.task.task_id,
                hypothesis_id=verdict_label,
                location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
                message="Retries exhausted; routing to Supreme Court",
                data={"task_id": state.task.task_id, "retry_count": state.retry_count},
                trace_id=state.trace_id,
                dag_id=state.dag_id,
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
        run_id=state.task.task_id,
        hypothesis_id=verdict_label,
        location="agenti_helix/verification/verification_loop.py:node_handle_verdict",
        message="Rolled back and scheduled retry",
        data={"task_id": state.task.task_id, "checkpoint_id": state.checkpoint.checkpoint_id, "retry_count": state.retry_count},
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )
    record_verification_cycle_snapshot(
        dag_id=state.dag_id,
        task_id=state.task.task_id,
        verification_cycle=state.retry_count + 1,
        verification_status=VerificationStatus.RUNNING.value,
    )
    return state


def node_summarize_context(state: VerificationState) -> VerificationState:
    """§4.3 — Compress error history via memory_summarizer_v1 before re-attempting the coder."""
    if _is_cancelled(state.cancel_token):
        return state

    # Lazy import to avoid circular dependency (verification_loop ↔ orchestrator ↔ agent_runtime).
    from agenti_helix.runtime.agent_runtime import run_agent  # noqa: PLC0415

    attempt_label = f"summarize_attempt_{state.retry_count}"
    raw_history = "\n".join(state.error_history)

    try:
        result = run_agent(
            agent_id="memory_summarizer_v1",
            raw_input={
                "errors": raw_history,
                "previous_patches": json.dumps(state.diff_json or {}),
                "attempt": state.retry_count,
            },
            runtime={"temperature": 0.0},
            cancel_token=state.cancel_token,
            observe={
                "run_id": state.task.task_id,
                "hypothesis_id": attempt_label,
                "location": "agenti_helix/verification/verification_loop.py:node_summarize_context",
                "trace_id": state.trace_id,
                "dag_id": state.dag_id,
            },
        )

        compressed = result.get("compressed_summary") or result.get("output") or raw_history
        state.compressed_context = str(compressed)
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=attempt_label,
            location="agenti_helix/verification/verification_loop.py:node_summarize_context",
            message="Memory summarizer compressed error history",
            data={"task_id": state.task.task_id, "compressed_len": len(state.compressed_context)},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
    except Exception as exc:
        # Summarization is best-effort; fall back to raw feedback.
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=attempt_label,
            location="agenti_helix/verification/verification_loop.py:node_summarize_context",
            message=f"Memory summarizer failed (using raw feedback): {exc}",
            data={"task_id": state.task.task_id},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )

    return state


def node_supreme_court(state: VerificationState) -> VerificationState:
    """§4.4 — Invoke frontier-model arbitration as a last resort before BLOCKED."""
    assert state.checkpoint is not None, "Checkpoint must exist for Supreme Court"

    # Lazy import to avoid circular dependency.
    from agenti_helix.runtime.agent_runtime import run_agent  # noqa: PLC0415

    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file

    log_event(
        run_id=state.task.task_id,
        hypothesis_id="supreme_court",
        location="agenti_helix/verification/verification_loop.py:node_supreme_court",
        message="Invoking Supreme Court arbitration",
        data={"task_id": state.task.task_id, "retry_count": state.retry_count},
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )

    rejection_summary = "\n".join(state.error_history[-3:]) or (
        (state.judge_response or {}).get("justification", "No rejection reasons recorded")
    )

    try:
        result = run_agent(
            agent_id="supreme_court_v1",
            raw_input={
                "original_intent": state.task.task_id,
                "intent": state.task.intent,
                "best_patch": json.dumps(state.diff_json or {}),
                "rejection_reasons": rejection_summary,
                "error_history": "\n".join(state.error_history),
            },
            runtime={"temperature": 0.1},
            cancel_token=state.cancel_token,
            observe={
                "run_id": state.task.task_id,
                "hypothesis_id": "supreme_court",
                "location": "agenti_helix/verification/verification_loop.py:node_supreme_court",
                "trace_id": state.trace_id,
                "dag_id": state.dag_id,
            },
        )
        state.supreme_court_invoked = True

        resolved = result.get("resolved", False)
        if not resolved:
            raise ValueError(result.get("reasoning", "Supreme Court could not resolve"))

        # Apply the SC-arbitrated patch.
        sc_patch = {
            "filePath": result["filePath"],
            "startLine": result["startLine"],
            "endLine": result["endLine"],
            "replacementLines": result["replacementLines"],
        }
        from agenti_helix.runtime.tools import tool_apply_line_patch_and_validate
        apply_result = tool_apply_line_patch_and_validate(
            repo_root=str(repo_root),
            patch=sc_patch,
            allowed_paths=[state.task.target_file],
        )
        if not apply_result.get("ok"):
            raise ValueError(f"SC patch failed to apply: {apply_result.get('error')}")

        state.diff_json = sc_patch
        state.coder_error = None
        state.judge_response = None  # Clear so verification runs fresh.
        log_event(
            run_id=state.task.task_id,
            hypothesis_id="supreme_court",
            location="agenti_helix/verification/verification_loop.py:node_supreme_court",
            message="Supreme Court resolved dispute; patch applied",
            data={"task_id": state.task.task_id, "sc_patch": sc_patch},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )

    except Exception as exc:
        # SC failed — transition to BLOCKED.
        state.supreme_court_invoked = True
        record_post_state(
            state.checkpoint,
            post_state_ref=target_path.read_text(),
            diff=json.dumps(state.diff_json or {}, indent=2),
            tool_logs={
                "judge": state.judge_response,
                "static_checks": state.static_check_logs or {},
                "supreme_court_invoked": True,
                "supreme_court_error": str(exc),
            },
            status=VerificationStatus.BLOCKED,
        )
        log_event(
            run_id=state.task.task_id,
            hypothesis_id="supreme_court",
            location="agenti_helix/verification/verification_loop.py:node_supreme_court",
            message="Supreme Court failed to resolve; BLOCKED",
            data={"task_id": state.task.task_id, "error": str(exc)},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )

    return state


def build_verification_graph() -> StateGraph:
    graph = StateGraph(VerificationState)

    graph.add_node("take_pre_checkpoint", node_take_pre_checkpoint)
    graph.add_node("run_coder", node_run_coder)
    graph.add_node("summarize_context", node_summarize_context)
    graph.add_node("run_static_checks", node_run_static_checks)
    graph.add_node("call_judge", node_call_judge)
    graph.add_node("handle_verdict", node_handle_verdict)
    graph.add_node("supreme_court", node_supreme_court)

    graph.set_entry_point("take_pre_checkpoint")

    graph.add_edge("take_pre_checkpoint", "run_coder")

    def _coder_ok(state: VerificationState) -> str:
        if _is_cancelled(state.cancel_token):
            return END
        # §4.5 — Human escalation short-circuits to handle_verdict (→ BLOCKED).
        if state.human_escalation_requested:
            state.judge_response = {
                "verdict": "FAIL",
                "justification": f"Human escalation: {state.human_escalation_reason}",
                "problematic_lines": [],
            }
            # Force BLOCKED immediately.
            if state.checkpoint is not None:
                from agenti_helix.runtime.tools import tool_apply_line_patch_and_validate  # noqa
                repo_root = Path(state.task.repo_path).resolve()
                target_path = repo_root / state.task.target_file
                record_post_state(
                    state.checkpoint,
                    post_state_ref=target_path.read_text() if target_path.exists() else "",
                    diff=json.dumps(state.diff_json or {}, indent=2),
                    tool_logs={
                        "judge": state.judge_response,
                        "human_escalation": state.human_escalation_reason,
                    },
                    status=VerificationStatus.BLOCKED,
                )
            return END
        return "ok" if not state.coder_error else "error"

    graph.add_conditional_edges(
        "run_coder",
        _coder_ok,
        {
            END: END,
            "ok": "run_static_checks",
            "error": "handle_verdict",
        },
    )

    # §4.3 — After summarization, always go to coder.
    graph.add_edge("summarize_context", "run_coder")

    def _static_checks_ok(state: VerificationState) -> str:
        if _is_cancelled(state.cancel_token):
            return END
        logs = state.static_check_logs or {}
        if not logs.get("passed", True) and logs.get("errors"):
            # §4.5 — Security findings bypass the retry loop entirely → force BLOCKED.
            if logs.get("security_blocked") and state.checkpoint is not None:
                repo_root = Path(state.task.repo_path).resolve()
                target_path = repo_root / state.task.target_file
                record_post_state(
                    state.checkpoint,
                    post_state_ref=target_path.read_text() if target_path.exists() else "",
                    diff=json.dumps(state.diff_json or {}, indent=2),
                    tool_logs={"security_findings": logs["errors"], "security_blocked": True},
                    status=VerificationStatus.BLOCKED,
                )
                return END
            state.judge_response = {
                "verdict": "FAIL",
                "justification": "Static checks failed: " + "; ".join(str(e) for e in logs["errors"][:5]),
                "problematic_lines": [],
            }
            return "handle_verdict"
        return "call_judge"

    graph.add_conditional_edges(
        "run_static_checks",
        _static_checks_ok,
        {END: END, "call_judge": "call_judge", "handle_verdict": "handle_verdict"},
    )
    graph.add_edge("call_judge", "handle_verdict")

    def _should_retry(state: VerificationState) -> str:
        if _is_cancelled(state.cancel_token):
            return END
        if state.checkpoint is not None and state.checkpoint.status in (
            VerificationStatus.PASSED,
            VerificationStatus.PASSED_PENDING_SIGNOFF,
            VerificationStatus.BLOCKED,
        ):
            return END

        if state.judge_response is None:
            return END

        verdict = str(state.judge_response.get("verdict", "FAIL")).upper()
        if verdict == "PASS":
            return END

        cfg = DEFAULT_CONFIG
        # §4.4 — Route to Supreme Court when retries are exhausted and SC is enabled.
        if state.retry_count >= cfg.max_retries:
            if cfg.supreme_court_enabled and not state.supreme_court_invoked:
                return "supreme_court"
            return END  # SC already tried or disabled.

        # §4.3 — Trigger context summarization from the second retry onward.
        if state.retry_count >= 1:
            return "summarize_context"

        return "run_coder"

    graph.add_conditional_edges(
        "handle_verdict",
        _should_retry,
        {
            "run_coder": "run_coder",
            "summarize_context": "summarize_context",
            "supreme_court": "supreme_court",
            END: END,
        },
    )

    # §4.4 — After Supreme Court, either go to static checks (resolved) or end (blocked).
    def _after_supreme_court(state: VerificationState) -> str:
        if state.checkpoint is not None and state.checkpoint.status == VerificationStatus.BLOCKED:
            return END
        return "run_static_checks"

    graph.add_conditional_edges(
        "supreme_court",
        _after_supreme_court,
        {END: END, "run_static_checks": "run_static_checks"},
    )

    return graph


def run_verification_loop(
    task: EditTaskSpec,
    cancel_token: Any | None = None,
    trace_id: Optional[str] = None,
    dag_id: Optional[str] = None,
) -> VerificationState:
    graph = build_verification_graph()
    app = graph.compile()
    initial_state = VerificationState(
        task=task,
        cancel_token=cancel_token,
        trace_id=trace_id,
        dag_id=dag_id,
    )
    log_event(
        run_id=task.task_id,
        hypothesis_id="loop_start",
        location="agenti_helix/verification/verification_loop.py:run_verification_loop",
        message="Starting verification loop",
        data={"task_id": task.task_id, "repo_path": task.repo_path, "target_file": task.target_file},
        trace_id=trace_id,
        dag_id=dag_id,
    )
    final_state = app.invoke(initial_state)

    if isinstance(final_state, dict):
        final_state_obj = VerificationState(**final_state)
    else:
        final_state_obj = final_state

    status = final_state_obj.checkpoint.status.value if final_state_obj.checkpoint else None
    log_event(
        run_id=task.task_id,
        hypothesis_id="loop_end",
        location="agenti_helix/verification/verification_loop.py:run_verification_loop",
        message="Finished verification loop",
        data={"task_id": task.task_id, "status": status, "retry_count": final_state_obj.retry_count},
        trace_id=trace_id,
        dag_id=dag_id,
    )

    # Index resolved episodes into episodic memory for future retrieval.
    try:
        index_from_verification_state(final_state_obj)
    except Exception:
        pass  # Memory indexing is best-effort and must never break the main loop.

    return final_state_obj

