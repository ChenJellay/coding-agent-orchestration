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
import py_compile
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agenti_helix.core.git_unified_diff import build_git_unified_diff, collect_diff_paths
from agenti_helix.observability.debug_log import log_event
from agenti_helix.sandbox.manager import log_sandbox_status_for_task
from agenti_helix.runtime.chain_runtime import run_chain
from agenti_helix.api.task_lookup import record_verification_cycle_snapshot
from agenti_helix.memory.indexer import index_from_verification_state
from agenti_helix.memory.store import MemoryStore, get_default_store

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


def _normalize_repo_relative_path(raw: str) -> str:
    """Normalize a repo-relative path for comparisons (slashes, ./, leading /)."""
    s = str(raw).strip().replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def _supreme_court_allowed_paths(*, repo_root: Path, task_target_file: str, patch_file_path: str) -> List[str]:
    """
    Paths the Supreme Court may patch. Includes the task target plus the SC-chosen file;
    the latter may be a new file not yet in the scanned repo map.
    """
    out: List[str] = []
    root = repo_root.resolve()
    for p in (_normalize_repo_relative_path(task_target_file), _normalize_repo_relative_path(patch_file_path)):
        if p and p not in out:
            out.append(p)
    for p in out:
        resolved = (repo_root / p).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Supreme Court patch path outside repo: {p!r}") from exc
    return out


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
    # Ordered per-attempt snapshots used by ``memory_summarizer_v1`` and
    # ``supreme_court_v1``. Each entry mirrors ``AttemptRecord`` (see
    # agenti_helix.agents.models). Populated by the main loop right after
    # each judge verdict resolves.
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    # Terminal arbitration output, if the supreme_court agent ran. Surfaced
    # in the final checkpoint tool_logs so reviewers can see the ruling.
    supreme_court_ruling: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

    # §4.6 — Upstream context cache (librarian + SDET outputs).
    # Populated after the first successful chain run so that retries skip those
    # expensive steps and only re-run the coder + write_files portion.
    cached_chain_context: Optional[Dict[str, Any]] = None
    chain_artifacts: Dict[str, Any] = field(default_factory=dict)


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


def _diff_json_for_judge_gate(dj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Ensure ``diff_validator_allowed_paths`` exists (older ``diff_json`` blobs may omit it)."""
    if not isinstance(dj, dict):
        return {}
    if dj.get("diff_validator_allowed_paths"):
        return dj
    merged: List[str] = []
    for key in ("files_written", "test_file_paths"):
        lst = dj.get(key)
        if isinstance(lst, list):
            merged.extend(str(p) for p in lst if isinstance(p, str) and p.strip())
    if not merged:
        return dj
    out = dict(dj)
    out["diff_validator_allowed_paths"] = sorted(set(merged))
    return out


def _paths_for_git_diff(task: EditTaskSpec, diff_json: Optional[Dict[str, Any]]) -> List[str]:
    """Repo-relative paths to include in a unified diff."""
    return collect_diff_paths(task.target_file or "", diff_json)


def _git_unified_diff_for_paths(repo_root: Path, paths: List[str]) -> str:
    """Best-effort unified diff (working tree vs HEAD for tracked, vs /dev/null for untracked)."""
    return build_git_unified_diff(repo_root, paths)


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
        parts.append(
            "\n\nCumulative feedback from prior judge / security / tool rounds (oldest to newest):\n" + raw
        )
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
    allowed_paths = {state.task.target_file}
    if isinstance(state.diff_json, dict):
        allowed_paths.update(str(p) for p in (state.diff_json.get("files_written") or []) if p)
        allowed_paths.update(str(p) for p in (state.diff_json.get("test_file_paths") or []) if p)
    ctx = {
        "repo_root": repo_root,
        "repo_path": state.task.repo_path,
        "target_file": state.task.target_file,
        "acceptance_criteria": state.task.acceptance_criteria,
        "original_snippet": state.original_content or "",
        "static_check_logs": state.static_check_logs or {},
        "intent": state.task.intent,
        "diff_json": _diff_json_for_judge_gate(state.diff_json),
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
    base: Dict[str, Any] = {"judge": state.judge_response, "static_checks": state.static_check_logs or {}}
    if state.supreme_court_ruling is not None:
        # PASS_OVERRIDE path: per-attempt judge said FAIL, supreme_court said
        # the transcript actually satisfies acceptance_criteria. Keep both
        # signals in tool_logs so humans can audit the override.
        base["supreme_court"] = state.supreme_court_ruling
    tool_logs = _tool_logs_with_git_unified_diff(
        repo_root=repo_root,
        task=state.task,
        diff_json=state.diff_json,
        base=base,
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


def _record_blocked_after_retries(
    state: VerificationState,
    *,
    human_review: bool = False,
) -> None:
    """Retries exhausted with no PASS: BLOCKED.

    When ``human_review`` is True, the supreme_court ruled ``ESCALATE_HUMAN``
    — we still mark BLOCKED but stash the flag in tool_logs so dashboards /
    downstream sign-off flows can route the task to a reviewer.
    """
    assert state.checkpoint is not None
    repo_root = Path(state.task.repo_path).resolve()
    target_path = repo_root / state.task.target_file
    base: Dict[str, Any] = {
        "judge": state.judge_response,
        "static_checks": state.static_check_logs or {},
    }
    if state.supreme_court_ruling is not None:
        base["supreme_court"] = state.supreme_court_ruling
    if human_review:
        base["human_review_required"] = True
    record_post_state(
        state.checkpoint,
        post_state_ref=target_path.read_text() if target_path.exists() else "",
        diff=json.dumps(state.diff_json or {}, indent=2),
        tool_logs=_tool_logs_with_git_unified_diff(
            repo_root=repo_root,
            task=state.task,
            diff_json=state.diff_json,
            base=base,
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


def _record_attempt(state: VerificationState, *, verdict_from_static: bool) -> None:
    """Append a compact snapshot of this coder→judge attempt to ``state.attempts``.

    Called exactly once per loop iteration, right after a verdict has resolved
    (whether from static-check short-circuit or a real judge call). The shape
    matches ``agenti_helix.agents.models.AttemptRecord`` so the retry-loop
    agents can consume it directly.
    """
    jr = state.judge_response or {}
    static_logs = state.static_check_logs or {}
    static_errors = [str(e) for e in (static_logs.get("errors") or [])][:8]
    diff_summary = _summarise_diff_for_attempt(state)
    state.attempts.append(
        {
            "attempt_n": state.retry_count + 1,
            "judge_verdict": str(jr.get("verdict") or "FAIL").upper(),
            "justification": str(jr.get("justification") or "")[:1500],
            "diff_summary": diff_summary,
            "static_errors": static_errors,
        }
    )


def _summarise_diff_for_attempt(state: VerificationState) -> str:
    """One-line description of what the attempt changed — NOT the full diff.

    The retry agents don't need the full unified diff (that would blow the
    context budget); they need to see whether each attempt touched the same
    file, how many hunks, and roughly which region.
    """
    dj = state.diff_json or {}
    files_written = dj.get("files_written") or []
    if isinstance(files_written, list) and files_written:
        return f"wrote {len(files_written)} file(s): " + ", ".join(str(p) for p in files_written[:3])
    # Patch-style diff_json has filePath / startLine / endLine.
    path = dj.get("filePath")
    start = dj.get("startLine")
    end = dj.get("endLine")
    if path is not None:
        return f"edited {path} lines {start}..{end}"
    return "(no diff summary available)"


def _run_memory_summarizer_into_feedback(
    state: VerificationState,
    *,
    memory_store: Optional[MemoryStore] = None,
) -> None:
    """Append a focused summarizer hint after cumulative judge/security feedback.

    Invoked from ``_prepare_retry`` when ``task.enable_memory_summarizer`` is
    set. On any failure (agent raises, schema repair exhausted, memory store
    missing, etc.) the loop silently keeps the cumulative feedback — a retry
    hint is best-effort and must never block the loop.
    """
    # Lazy import: avoids a structured_output → agent_runtime import cycle at
    # module load time and keeps the dependency optional for tests that patch
    # the verification loop without registering structured agents.
    from agenti_helix.runtime.structured_output import run_agent_structured

    preserved_cumulative = (state.feedback or "").strip()

    store = memory_store or get_default_store()
    jr = state.judge_response or {}
    query_text = str(jr.get("justification") or state.feedback or "")
    try:
        episodes = store.query(query_text, top_k=3)
    except Exception:
        episodes = []
    similar = [
        {"error_text": ep.error_text[:500], "resolution": ep.resolution[:500]}
        for ep in episodes
    ]

    raw_input = {
        "task_intent": state.task.intent,
        "target_file": state.task.target_file,
        "acceptance_criteria": state.task.acceptance_criteria,
        "attempts": list(state.attempts),
        "similar_past_episodes": similar,
    }

    try:
        hint = run_agent_structured(
            agent_id="memory_summarizer_v1",
            raw_input=raw_input,
            cancel_token=state.cancel_token,
            observe={
                "run_id": state.task.task_id,
                "hypothesis_id": f"memory_summarizer_attempt_{state.retry_count + 1}",
                "location": "agenti_helix/verification/verification_loop.py:_run_memory_summarizer_into_feedback",
                "trace_id": state.trace_id,
                "dag_id": state.dag_id,
            },
        )
    except Exception as exc:
        log_event(
            run_id=state.task.task_id,
            hypothesis_id=f"memory_summarizer_attempt_{state.retry_count + 1}",
            location="agenti_helix/verification/verification_loop.py:_run_memory_summarizer_into_feedback",
            message="memory_summarizer_v1 failed — keeping legacy judge-justification feedback",
            data={"error": f"{type(exc).__name__}: {exc}", "task_id": state.task.task_id},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
        return

    root_cause = str(hint.get("root_cause_hypothesis") or "").strip()
    actionable = str(hint.get("actionable_hint") or "").strip()
    anti = hint.get("anti_patterns_to_avoid") or []
    if not actionable:
        return  # Empty hint is no hint; keep legacy feedback.

    anti_block = ""
    if isinstance(anti, list) and anti:
        bullets = "\n".join(f"- {str(a).strip()}" for a in anti[:5] if str(a).strip())
        if bullets:
            anti_block = "\nDo NOT repeat:\n" + bullets

    hint_body = "\n".join(
        line
        for line in [
            "Retry hint from memory_summarizer_v1:",
            f"Root cause: {root_cause}" if root_cause else None,
            f"Next-step hint: {actionable}",
            anti_block.strip() or None,
        ]
        if line
    )
    state.feedback = (
        f"{preserved_cumulative}\n\n{hint_body}".strip() if preserved_cumulative else hint_body
    )
    log_event(
        run_id=state.task.task_id,
        hypothesis_id=f"memory_summarizer_attempt_{state.retry_count + 1}",
        location="agenti_helix/verification/verification_loop.py:_run_memory_summarizer_into_feedback",
        message="Injected memory_summarizer_v1 hint into retry feedback",
        data={
            "task_id": state.task.task_id,
            "similar_episodes": len(similar),
            "hint_chars": len(state.feedback),
        },
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )


def _run_supreme_court(state: VerificationState) -> Optional[Dict[str, Any]]:
    """Invoke ``supreme_court_v1`` to arbitrate an exhausted retry budget.

    Returns the ruling dict on success, or ``None`` if the agent failed (in
    which case the caller must fall back to the legacy ``CONFIRM_BLOCKED``
    behaviour — we never let an arbitration failure swallow a genuine block).
    """
    from agenti_helix.runtime.structured_output import run_agent_structured

    repo_root = Path(state.task.repo_path).resolve()
    diff_text = _git_unified_diff_for_paths(
        repo_root=repo_root,
        paths=_paths_for_git_diff(state.task, state.diff_json),
    )
    static_logs = state.static_check_logs or {}
    static_summary_bits: List[str] = []
    if static_logs:
        if static_logs.get("security_blocked"):
            static_summary_bits.append("security-blocked")
        if static_logs.get("errors"):
            static_summary_bits.append(f"{len(static_logs['errors'])} static error(s)")
        if static_logs.get("passed") is True:
            static_summary_bits.append("static checks passed")
    static_summary = "; ".join(static_summary_bits) or "no static-check signal"

    raw_input = {
        "task_intent": state.task.intent,
        "target_file": state.task.target_file,
        "acceptance_criteria": state.task.acceptance_criteria,
        "final_git_diff": diff_text[:_MAX_GIT_UNIFIED_DIFF_CHARS] if diff_text else "",
        "attempts": list(state.attempts),
        "final_judge_verdict": state.judge_response or {},
        "static_check_summary": static_summary,
    }

    try:
        ruling = run_agent_structured(
            agent_id="supreme_court_v1",
            raw_input=raw_input,
            cancel_token=state.cancel_token,
            observe={
                "run_id": state.task.task_id,
                "hypothesis_id": "supreme_court_final",
                "location": "agenti_helix/verification/verification_loop.py:_run_supreme_court",
                "trace_id": state.trace_id,
                "dag_id": state.dag_id,
            },
        )
    except Exception as exc:
        log_event(
            run_id=state.task.task_id,
            hypothesis_id="supreme_court_final",
            location="agenti_helix/verification/verification_loop.py:_run_supreme_court",
            message="supreme_court_v1 failed — falling back to CONFIRM_BLOCKED",
            data={"error": f"{type(exc).__name__}: {exc}", "task_id": state.task.task_id},
            trace_id=state.trace_id,
            dag_id=state.dag_id,
        )
        return None

    log_event(
        run_id=state.task.task_id,
        hypothesis_id="supreme_court_final",
        location="agenti_helix/verification/verification_loop.py:_run_supreme_court",
        message=f"supreme_court_v1 ruling: {ruling.get('ruling')}",
        data={"task_id": state.task.task_id, "ruling": ruling.get("ruling")},
        trace_id=state.trace_id,
        dag_id=state.dag_id,
    )
    state.supreme_court_ruling = ruling
    return ruling


def _prepare_retry(state: VerificationState) -> None:
    """Roll the workspace back to the pre-checkpoint and stash judge feedback for the next coder pass."""
    assert state.checkpoint is not None
    rollback_to_checkpoint(
        state.task,
        state.checkpoint,
        original_content=state.original_content,
    )
    jr = state.judge_response or {}
    justification = str(jr.get("justification", "") or "").strip()
    # ``retry_count`` was incremented before this call — it equals the number of
    # completed coder→judge rounds that have failed so far.
    round_n = max(1, int(state.retry_count))
    new_block = "\n".join(
        line
        for line in [
            f"--- After judge round {round_n} ---",
            justification,
        ]
        if line
    )
    prior = (state.feedback or "").strip()
    if prior:
        state.feedback = f"{prior}\n\n{new_block}"
    else:
        state.feedback = new_block
    if getattr(state.task, "enable_memory_summarizer", False):
        _run_memory_summarizer_into_feedback(state)
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


def _finalise_after_retries(state: VerificationState) -> None:
    """Retry budget exhausted. If ``supreme_court`` is enabled, let it arbitrate.

    Dispatch table:
      - PASS_OVERRIDE  → promote to ``_record_pass`` (supreme_court sides with
        the workspace state despite per-attempt FAILs).
      - ESCALATE_HUMAN → BLOCKED with ``human_review_required`` flag set.
      - CONFIRM_BLOCKED / agent failure / disabled → legacy BLOCKED path.
    """
    if not getattr(state.task, "enable_supreme_court", False):
        _record_blocked_after_retries(state)
        return

    ruling = _run_supreme_court(state)
    if ruling is None:
        # Agent failed; never let arbitration errors unblock a BLOCKED task.
        _record_blocked_after_retries(state)
        return

    verdict = str(ruling.get("ruling") or "").upper()
    if verdict == "PASS_OVERRIDE":
        _record_pass(state)
        return
    if verdict == "ESCALATE_HUMAN":
        _record_blocked_after_retries(state, human_review=True)
        return
    # CONFIRM_BLOCKED or anything we don't recognise → default BLOCKED.
    _record_blocked_after_retries(state)


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
    log_sandbox_status_for_task(task.task_id, trace_id=trace_id, dag_id=dag_id)

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
                        # Security-blocked short-circuits both the judge and
                        # any supreme_court arbitration — we never override
                        # a security violation.
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

            _record_attempt(state, verdict_from_static=verdict_from_static)

            verdict = str((state.judge_response or {}).get("verdict", "FAIL")).upper()
            if verdict == "PASS":
                _record_pass(state)
                break

            state.retry_count += 1
            if state.retry_count >= cfg.max_retries:
                _finalise_after_retries(state)
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
