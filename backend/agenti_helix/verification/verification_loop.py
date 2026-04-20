"""
Verification loop — plain-Python implementation.

This module replaces the previous LangGraph state-machine. The pipeline is
straightforward enough to express as a single ``while`` loop, which makes
control flow obvious and removes a heavy dependency (``langgraph`` + its
async runtime) plus two roles that were never used in production
(``memory_summarizer_v1`` and ``supreme_court_v1``).

Public API kept stable:
    - ``VerificationState`` (dataclass holding loop state, used by tests)
    - ``run_verification_loop(task, cancel_token=None, ...) -> VerificationState``
    - ``_run_static_checks`` and the individual ``_check_*`` helpers
      (referenced from L3 / L4 unit tests)
    - ``_paths_for_git_diff`` / ``_git_unified_diff_for_paths`` /
      ``_tool_logs_with_git_unified_diff`` (used by sign-off + git unit tests)
"""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agenti_helix.observability.debug_log import log_event
from agenti_helix.runtime.chain_runtime import run_chain
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
    """Mutable state carried through the verification loop."""

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    raw = text.encode("utf-8")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _patch_pipeline(task: EditTaskSpec) -> bool:
    """Line-patch verification stages the post-state for manual sign-off."""
    mode = getattr(task, "pipeline_mode", None) or "patch"
    return mode in ("patch", "diff_guard_patch")


# Cap embedded git diff in checkpoint JSON (UI / control plane).
_MAX_GIT_UNIFIED_DIFF_CHARS = 512_000


def _paths_for_git_diff(task: EditTaskSpec, diff_json: Optional[Dict[str, Any]]) -> List[str]:
    """Repo-relative paths to include in a unified diff."""
    paths: List[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        raw = raw.strip().replace("\\", "/")
        if not raw or raw in seen or ".." in raw.split("/"):
            return
        seen.add(raw)
        paths.append(raw)

    if task.target_file:
        add(str(task.target_file))
    if not isinstance(diff_json, dict):
        return paths
    fp = diff_json.get("filePath")
    if isinstance(fp, str):
        add(fp)
    for key in ("files_written", "test_file_paths"):
        lst = diff_json.get(key)
        if isinstance(lst, list):
            for item in lst:
                if isinstance(item, str):
                    add(item)
    return paths


def _git_unified_diff_for_paths(repo_root: Path, paths: List[str]) -> str:
    """Best-effort unified diff (working tree vs HEAD for tracked, vs /dev/null for untracked)."""
    if not paths:
        return ""
    repo_root = repo_root.resolve()
    try:
        probe = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode != 0 or (probe.stdout or "").strip().lower() != "true":
            return ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""

    chunks: List[str] = []
    null_device = os.devnull
    for rel in paths:
        rel = rel.strip().replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            continue
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            ls = subprocess.run(
                ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", rel],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        tracked = ls.returncode == 0
        try:
            if tracked:
                diff = subprocess.run(
                    ["git", "-C", str(repo_root), "diff", "--no-color", "HEAD", "--", rel],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            else:
                diff = subprocess.run(
                    ["git", "diff", "--no-color", "--no-index", null_device, rel],
                    capture_output=True,
                    text=True,
                    cwd=str(repo_root),
                    timeout=120,
                )
            if diff.stdout:
                chunks.append(diff.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    out = "".join(chunks)
    if len(out) > _MAX_GIT_UNIFIED_DIFF_CHARS:
        return out[:_MAX_GIT_UNIFIED_DIFF_CHARS] + "\n... [truncated by agenti_helix: git unified diff cap]\n"
    return out


def _tool_logs_with_git_unified_diff(
    *,
    repo_root: Path,
    task: EditTaskSpec,
    diff_json: Optional[Dict[str, Any]],
    base: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(base)
    diff_txt = _git_unified_diff_for_paths(repo_root, _paths_for_git_diff(task, diff_json))
    if diff_txt.strip():
        merged["git_unified_diff"] = diff_txt
    return merged


# ---------------------------------------------------------------------------
# Static checks (kept as standalone helpers — used directly by L3/L4 tests)
# ---------------------------------------------------------------------------


def _check_python_syntax(target_path: Path) -> List[str]:
    errors: List[str] = []
    try:
        py_compile.compile(str(target_path), doraise=True)
    except py_compile.PyCompileError as exc:
        errors.append(str(exc))
    return errors


def _check_python_ruff(target_path: Path) -> List[str]:
    errors: List[str] = []
    try:
        result = subprocess.run(
            ["ruff", "check", "--select", "E,F", "--output-format", "text", str(target_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 and result.stdout.strip():
            errors.extend(result.stdout.strip().splitlines())
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        errors.append("ruff check timed out")
    return errors


def _check_js_ts_syntax(target_path: Path) -> List[str]:
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
        pass
    except subprocess.TimeoutExpired:
        errors.append("node --check timed out")
    return errors


def _check_bandit_security(target_path: Path) -> List[str]:
    """Bandit scan; returns only high-severity / high-confidence findings.

    Bandit is optional — absence of the binary is treated as a no-op so the
    pipeline can still complete in environments without security tooling.
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
        if result.returncode not in (0, 1):
            return []
        output = (result.stdout or "").strip()
        if output and "No issues identified" not in output:
            for line in output.splitlines():
                if line.startswith(">> Issue:") or line.startswith("Issue:"):
                    errors.append(f"[SECURITY] {line}")
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        pass
    return errors


def _run_static_checks(repo_root: Path, target_file: str) -> Dict[str, Any]:
    """Run syntax / lint / security checks on the patched target file."""
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
        checks_run.append("bandit")
        sec_errors = _check_bandit_security(target_path)
        if sec_errors:
            errors.extend(sec_errors)
            security_blocked = True
    elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        checks_run.append("node_check")
        errors.extend(_check_js_ts_syntax(target_path))

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "checks_run": checks_run,
        "security_blocked": security_blocked,
    }


# ---------------------------------------------------------------------------
# Loop steps (plain functions, no graph framework)
# ---------------------------------------------------------------------------


def _build_coder_intent(state: VerificationState) -> str:
    """Combine task intent with prior judge feedback, capped to keep prompts bounded."""
    parts: List[str] = [state.task.intent]
    if state.feedback:
        raw = state.feedback
        cap = 4_000  # Match historical max_error_history_chars cap.
        if len(raw) > cap:
            raw = raw[-cap:]
        parts.append(f"\n\nPrevious attempt feedback from Judge and tools:\n{raw}")
    return "".join(parts)


def _take_pre_checkpoint(state: VerificationState) -> None:
    target_path = _resolve_target_path(state.task)
    original = snapshot_file(target_path)
    checkpoint = create_pre_checkpoint(state.task, original)
    checkpoint.status = VerificationStatus.RUNNING
    state.checkpoint = checkpoint
    state.original_content = original
    log_event(
        run_id=state.task.task_id,
        hypothesis_id="pre_checkpoint",
        location="agenti_helix/verification/verification_loop.py:_take_pre_checkpoint",
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


def _run_coder(state: VerificationState) -> bool:
    """Run the resolved coder chain. Returns False on coder error or escalation."""
    record_verification_cycle_snapshot(
        dag_id=state.dag_id,
        task_id=state.task.task_id,
        verification_cycle=state.retry_count + 1,
        verification_status=state.checkpoint.status.value if state.checkpoint else None,
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
            "task_id": state.task.task_id,
            "doc_url": getattr(state.task, "doc_url", "") or "",
            "trace_id": state.trace_id,
            "dag_id": state.dag_id,
        }
        ctx = run_chain(
            chain_spec=coder_chain,
            initial_context=ctx,
            cancel_token=state.cancel_token,
            run_id=state.task.task_id,
            hypothesis_id=f"coder_attempt_{state.retry_count + 1}",
            location_prefix="agenti_helix/verification/verification_loop.py:_run_coder",
        )
        coder_patch = ctx.get("coder_patch") or {}
        if isinstance(coder_patch, dict) and coder_patch.get("escalate_to_human"):
            reason = str(coder_patch.get("escalation_reason") or "Coder requested escalation")
            state.judge_response = {
                "verdict": "FAIL",
                "justification": f"Human escalation: {reason}",
                "problematic_lines": [],
            }
            target_path = repo_root / state.task.target_file
            if state.checkpoint is not None:
                record_post_state(
                    state.checkpoint,
                    post_state_ref=target_path.read_text() if target_path.exists() else "",
                    diff=json.dumps(state.diff_json or {}, indent=2),
                    tool_logs=_tool_logs_with_git_unified_diff(
                        repo_root=repo_root,
                        task=state.task,
                        diff_json=state.diff_json,
                        base={
                            "judge": state.judge_response,
                            "human_escalation": reason,
                        },
                    ),
                    status=VerificationStatus.BLOCKED,
                )
            log_event(
                run_id=state.task.task_id,
                hypothesis_id=f"coder_attempt_{state.retry_count + 1}",
                location="agenti_helix/verification/verification_loop.py:_run_coder",
                message="Coder raised escalation signal — BLOCKED",
                data={"task_id": state.task.task_id, "reason": reason},
                trace_id=state.trace_id,
                dag_id=state.dag_id,
            )
            return False

        state.diff_json = ctx.get("diff_json")
        state.coder_error = None
        target_path = _resolve_target_path(state.task)
        post_coder_text = target_path.read_text() if target_path.exists() else ""
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=f"coder_attempt_{state.retry_count + 1}",
            location="agenti_helix/verification/verification_loop.py:_run_coder",
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
            verification_status=state.checkpoint.status.value if state.checkpoint else None,
            code_evidence={"post_coder": _text_fingerprint(post_coder_text)},
        )
        return True
    except Exception as exc:
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
            location="agenti_helix/verification/verification_loop.py:_run_coder",
            message="Coder failed (will be treated as verification FAIL)",
            data={"task_id": state.task.task_id, "error": state.coder_error},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
        return False


def _run_static_checks_step(state: VerificationState) -> None:
    repo_root = Path(state.task.repo_path).resolve()
    logs = _run_static_checks(repo_root, state.task.target_file)
    state.static_check_logs = logs
    target_path = repo_root / state.task.target_file
    post_static_text = target_path.read_text() if target_path.exists() else ""
    log_event(
        run_id=state.task.task_id,
        hypothesis_id=f"static_checks_attempt_{state.retry_count + 1}",
        location="agenti_helix/verification/verification_loop.py:_run_static_checks_step",
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
    record_verification_cycle_snapshot(
        dag_id=state.dag_id,
        task_id=state.task.task_id,
        verification_cycle=state.retry_count + 1,
        verification_status=state.checkpoint.status.value if state.checkpoint else None,
        code_evidence={"post_static_checks": _text_fingerprint(post_static_text)},
    )


def _call_judge(state: VerificationState) -> None:
    assert state.checkpoint is not None
    repo_root = Path(state.task.repo_path).resolve()
    from agenti_helix.orchestration.master_orchestrator import resolve_judge_chain  # noqa: PLC0415
    judge_chain = resolve_judge_chain(state.task)
    ctx = {
        "repo_root": repo_root,
        "repo_path": state.task.repo_path,
        "target_file": state.task.target_file,
        "acceptance_criteria": state.task.acceptance_criteria,
        "original_snippet": state.original_content or "",
        "static_check_logs": state.static_check_logs or {},
        "intent": state.task.intent,
        "diff_json": state.diff_json or {},
        "task_id": state.task.task_id,
        "trace_id": state.trace_id,
        "dag_id": state.dag_id,
    }
    attempt_label = f"judge_attempt_{state.retry_count + 1}"
    try:
        ctx = run_chain(
            chain_spec=judge_chain,
            initial_context=ctx,
            cancel_token=state.cancel_token,
            run_id=state.task.task_id,
            hypothesis_id=attempt_label,
            location_prefix="agenti_helix/verification/verification_loop.py:_call_judge",
        )
        state.judge_response = ctx.get("judge_response")
    except Exception as exc:
        state.judge_response = {
            "verdict": "FAIL",
            "justification": f"Judge failed before verdict: {type(exc).__name__}: {exc}",
            "problematic_lines": [],
        }
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=attempt_label,
            location="agenti_helix/verification/verification_loop.py:_call_judge",
            message="Judge chain failed (treated as FAIL)",
            data={"task_id": state.task.task_id, "error": state.judge_response["justification"]},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
        return

    log_event(
        run_id=state.task.task_id,
        hypothesis_id=attempt_label,
        location="agenti_helix/verification/verification_loop.py:_call_judge",
        message="Judge evaluated edit",
        data={
            "task_id": state.task.task_id,
            "verdict": (state.judge_response or {}).get("verdict"),
            "problematic_lines": (state.judge_response or {}).get("problematic_lines"),
        },
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )


def _record_security_blocked(state: VerificationState) -> None:
    """Static-check security finding: skip judge and mark BLOCKED immediately."""
    assert state.checkpoint is not None
    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file
    record_post_state(
        state.checkpoint,
        post_state_ref=target_path.read_text() if target_path.exists() else "",
        diff=json.dumps(state.diff_json or {}, indent=2),
        tool_logs=_tool_logs_with_git_unified_diff(
            repo_root=repo_root,
            task=state.task,
            diff_json=state.diff_json,
            base={
                "security_findings": (state.static_check_logs or {}).get("errors", []),
                "security_blocked": True,
            },
        ),
        status=VerificationStatus.BLOCKED,
    )


def _record_pass(state: VerificationState) -> None:
    """Judge PASS: stage post-state, optionally roll back workspace for sign-off."""
    assert state.checkpoint is not None
    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file
    post_judge_body = target_path.read_text()
    tool_logs = _tool_logs_with_git_unified_diff(
        repo_root=repo_root,
        task=state.task,
        diff_json=state.diff_json,
        base={"judge": state.judge_response, "static_checks": state.static_check_logs or {}},
    )
    if _patch_pipeline(state.task):
        record_post_state(
            state.checkpoint,
            post_state_ref=post_judge_body,
            diff=json.dumps(state.diff_json or {}, indent=2),
            tool_logs=tool_logs,
            status=VerificationStatus.PASSED_PENDING_SIGNOFF,
        )
        # Roll back workspace only — checkpoint metadata stays at PASSED_PENDING_SIGNOFF.
        pre_body = state.original_content if state.original_content is not None else state.checkpoint.pre_state_ref
        restore_file_from_snapshot(target_path, pre_body)
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=f"verdict_attempt_{state.retry_count + 1}",
            location="agenti_helix/verification/verification_loop.py:_record_pass",
            message="Judge PASS — staged post-state; workspace rolled back pending manual sign-off",
            data={
                "task_id": state.task.task_id,
                "checkpoint_id": state.checkpoint.checkpoint_id,
                "post_judge_fingerprint": _text_fingerprint(post_judge_body),
            },
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
    else:
        record_post_state(
            state.checkpoint,
            post_state_ref=post_judge_body,
            diff=json.dumps(state.diff_json or {}, indent=2),
            tool_logs=tool_logs,
            status=VerificationStatus.PASSED,
        )
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=f"verdict_attempt_{state.retry_count + 1}",
            location="agenti_helix/verification/verification_loop.py:_record_pass",
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


def _record_blocked_after_retries(state: VerificationState) -> None:
    """Retries exhausted with no PASS: BLOCKED."""
    assert state.checkpoint is not None
    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file
    record_post_state(
        state.checkpoint,
        post_state_ref=target_path.read_text() if target_path.exists() else "",
        diff=json.dumps(state.diff_json or {}, indent=2),
        tool_logs=_tool_logs_with_git_unified_diff(
            repo_root=repo_root,
            task=state.task,
            diff_json=state.diff_json,
            base={
                "judge": state.judge_response,
                "static_checks": state.static_check_logs or {},
            },
        ),
        status=VerificationStatus.BLOCKED,
    )
    log_event(
        run_id=state.task.task_id,
        hypothesis_id=f"verdict_attempt_{state.retry_count + 1}",
        location="agenti_helix/verification/verification_loop.py:_record_blocked_after_retries",
        message="Marked checkpoint BLOCKED (retries exhausted)",
        data={
            "task_id": state.task.task_id,
            "checkpoint_id": state.checkpoint.checkpoint_id,
            "retry_count": state.retry_count,
        },
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )


def _prepare_retry(state: VerificationState) -> None:
    """Roll the workspace back to the pre-checkpoint and stash judge feedback for the next coder pass."""
    assert state.checkpoint is not None
    rollback_to_checkpoint(
        state.task,
        state.checkpoint,
        original_content=state.original_content,
    )
    justification = (state.judge_response or {}).get("justification", "")
    state.feedback = "\n".join(
        ["Judge reported a failure.", f"Justification: {justification}"]
    )
    log_event(
        run_id=state.task.task_id,
        hypothesis_id=f"verdict_attempt_{state.retry_count + 1}",
        location="agenti_helix/verification/verification_loop.py:_prepare_retry",
        message="Rolled back and scheduled retry",
        data={
            "task_id": state.task.task_id,
            "checkpoint_id": state.checkpoint.checkpoint_id,
            "retry_count": state.retry_count + 1,
        },
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )
    record_verification_cycle_snapshot(
        dag_id=state.dag_id,
        task_id=state.task.task_id,
        verification_cycle=state.retry_count + 2,
        verification_status=VerificationStatus.RUNNING.value,
    )


def _mark_blocked_for_cancel(state: VerificationState) -> None:
    if state.checkpoint is None:
        return
    state.checkpoint.status = VerificationStatus.BLOCKED
    save_checkpoint(state.checkpoint)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_verification_loop(
    task: EditTaskSpec,
    cancel_token: Any | None = None,
    trace_id: Optional[str] = None,
    dag_id: Optional[str] = None,
) -> VerificationState:
    """Drive code → static-checks → judge → (retry|done) until a terminal status.

    Terminal statuses: PASSED, PASSED_PENDING_SIGNOFF, BLOCKED. Cancellation
    at any step short-circuits to BLOCKED.
    """
    cfg = DEFAULT_CONFIG
    state = VerificationState(
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

    try:
        if _is_cancelled(cancel_token):
            _mark_blocked_for_cancel(state)
            return state

        _take_pre_checkpoint(state)

        while True:
            if _is_cancelled(cancel_token):
                _mark_blocked_for_cancel(state)
                break

            coder_ok = _run_coder(state)
            if _is_cancelled(cancel_token):
                _mark_blocked_for_cancel(state)
                break

            # Coder requested escalation → already marked BLOCKED in _run_coder.
            if not coder_ok and state.checkpoint and state.checkpoint.status == VerificationStatus.BLOCKED:
                break

            verdict_from_static = False
            if coder_ok:
                _run_static_checks_step(state)
                logs = state.static_check_logs or {}
                if not logs.get("passed", True) and logs.get("errors"):
                    if logs.get("security_blocked"):
                        _record_security_blocked(state)
                        break
                    state.judge_response = {
                        "verdict": "FAIL",
                        "justification": "Static checks failed: " + "; ".join(str(e) for e in logs["errors"][:5]),
                        "problematic_lines": [],
                    }
                    verdict_from_static = True
                else:
                    _call_judge(state)

            verdict = str((state.judge_response or {}).get("verdict", "FAIL")).upper()
            if verdict == "PASS":
                _record_pass(state)
                break

            state.retry_count += 1
            if state.retry_count >= cfg.max_retries:
                _record_blocked_after_retries(state)
                break

            _prepare_retry(state)

    except Exception:
        # Loop crashes shouldn't leak to the orchestrator without a status.
        if state.checkpoint is not None and state.checkpoint.status == VerificationStatus.RUNNING:
            _mark_blocked_for_cancel(state)
        raise
    finally:
        status = state.checkpoint.status.value if state.checkpoint else None
        log_event(
            run_id=task.task_id,
            hypothesis_id="loop_end",
            location="agenti_helix/verification/verification_loop.py:run_verification_loop",
            message="Finished verification loop",
            data={"task_id": task.task_id, "status": status, "retry_count": state.retry_count},
            trace_id=trace_id,
            dag_id=dag_id,
        )

    try:
        index_from_verification_state(state)
    except Exception:
        pass  # Memory indexing is best-effort.

    return state
