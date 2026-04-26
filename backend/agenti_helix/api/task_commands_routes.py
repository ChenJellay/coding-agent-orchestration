from __future__ import annotations

import copy
import dataclasses
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agenti_helix.api.auth import Role, require_auth, require_editor
from agenti_helix.api.repo_run_lock import RepoLockTimeoutError, hold_repo_execution_lock
from agenti_helix.api.errors import raise_http_error
from agenti_helix.runtime.pipeline_presets import PIPELINE_MODES
from agenti_helix.api.response_caches import invalidate_features_and_triage_caches
from agenti_helix.api.job_registry import CancelToken, cancel_running_job_for_task, start_background_job
from agenti_helix.api.paths import PATHS, iter_jsonl, read_json
from agenti_helix.api.task_context_store import load_task_context, render_task_context_feedback, save_task_context
from agenti_helix.api.task_lookup import find_task_ref, persist_dag_state, try_load_dag_state
from agenti_helix.observability.debug_log import log_event
from agenti_helix.orchestration.orchestrator import DagNodeStatus, execute_dag, load_dag_spec, persist_dag_spec
from agenti_helix.orchestration.intent_compiler import (
    compile_macro_intent_to_dag,
    enrich_macro_intent_with_doc_before_compile,
)
from agenti_helix.runtime.chain_defaults import default_coder_chain, default_judge_chain
from agenti_helix.verification.checkpointing import (
    EditTaskSpec,
    Checkpoint,
    VerificationStatus,
    apply_signed_off_checkpoint,
    load_checkpoint,
    materialize_passed_checkpoint_to_workspace,
    rollback_to_checkpoint,
)
from agenti_helix.verification.verification_loop import run_verification_loop
from agenti_helix.api.git_ops import real_git_commit


router = APIRouter()


def _validate_dashboard_repo_path(repo_path: str) -> str:
    """When ``AGENTI_HELIX_ALLOWED_REPO_ROOTS`` is set, reject paths outside those prefixes."""
    p = Path(repo_path).expanduser().resolve()
    raw = (os.environ.get("AGENTI_HELIX_ALLOWED_REPO_ROOTS") or "").strip()
    if not raw:
        return str(p)
    allowed = [Path(x.strip()).expanduser().resolve() for x in raw.split(",") if x.strip()]
    if not any(p == a or p.is_relative_to(a) for a in allowed):
        raise_http_error(
            code="repo_not_allowed",
            message=f"repo_path must be under one of: {[str(a) for a in allowed]}",
            status_code=403,
        )
    return str(p)


def _repo_rel_path(*, repo_root: Path, task_repo: Path, rel_or_abs: str) -> str:
    """Express ``rel_or_abs`` as a path relative to ``repo_root`` for git staging."""
    rel_or_abs = rel_or_abs.strip().replace("\\", "/").lstrip("/")
    if not rel_or_abs:
        return ""
    p = Path(rel_or_abs)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            return rel_or_abs
    try:
        return (task_repo / rel_or_abs).resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return rel_or_abs


def _merge_stage_paths(*, repo_root: Path, task: EditTaskSpec, checkpoint: Checkpoint) -> List[str]:
    """Paths relative to the git root to include in a merge commit (primary file + diff metadata)."""
    task_repo = Path(task.repo_path).resolve()
    ordered: List[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        rel = _repo_rel_path(repo_root=repo_root, task_repo=task_repo, rel_or_abs=raw)
        if rel and rel not in seen:
            seen.add(rel)
            ordered.append(rel)

    if task.target_file:
        add(str(task.target_file))

    if checkpoint.diff:
        try:
            d = json.loads(checkpoint.diff)
            if isinstance(d, dict):
                fp = d.get("filePath")
                if isinstance(fp, str):
                    add(fp)
                for key in ("files_written", "test_file_paths"):
                    lst = d.get(key)
                    if isinstance(lst, list):
                        for item in lst:
                            if isinstance(item, str):
                                add(item)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    return ordered


def _ensure_dag_state_initialized(*, dag_id: str) -> Dict[str, Any]:
    existing = try_load_dag_state(dag_id)
    if existing and isinstance(existing, dict) and isinstance(existing.get("nodes"), dict) and existing["nodes"]:
        return existing

    spec_path = PATHS.dags_dir / f"{dag_id}.json"
    if not spec_path.exists():
        raise_http_error(code="dag_not_found", message=f"DAG not found for dag_id={dag_id!r}", status_code=404)

    spec = read_json(spec_path)
    nodes_raw = spec.get("nodes") if isinstance(spec, dict) else None
    if not isinstance(nodes_raw, dict):
        raise_http_error(code="dag_state_invalid", message=f"DAG spec has no nodes for dag_id={dag_id!r}", status_code=500)

    node_states: Dict[str, Any] = {}
    for node_id in nodes_raw.keys():
        node_states[str(node_id)] = {
            "node_id": str(node_id),
            "status": DagNodeStatus.PENDING.value,
            "attempts": 0,
            "verification_status": None,
        }
    return {"dag_id": dag_id, "nodes": node_states}


def _set_node_state(
    *,
    dag_id: str,
    node_id: str,
    status: DagNodeStatus,
    verification_status: Optional[VerificationStatus],
    bump_attempts: bool,
) -> Dict[str, Any]:
    state = _ensure_dag_state_initialized(dag_id)
    nodes = state["nodes"]
    node_state = nodes.get(node_id)
    if not isinstance(node_state, dict):
        raise_http_error(code="node_not_found", message=f"Unknown node_id={node_id!r} for dag_id={dag_id!r}", status_code=404)

    if bump_attempts:
        node_state["attempts"] = int(node_state.get("attempts") or 0) + 1

    node_state["status"] = status.value
    node_state["verification_status"] = verification_status.value if verification_status is not None else None
    persist_dag_state(dag_id, state)
    return state


def _build_task_intent_with_injected_guidance(*, task: EditTaskSpec, injected: str) -> EditTaskSpec:
    injected = (injected or "").strip()
    if not injected:
        return task
    new_intent = f"{task.intent}\n\nInjected guidance for next retry:\n{injected}".strip()
    return dataclasses.replace(task, intent=new_intent)


def _feedback_from_context_and_guidance(*, task_id: str, guidance: Optional[str]) -> str:
    ctx = load_task_context(task_id)
    ctx_feedback = render_task_context_feedback(ctx)
    extra = (guidance or "").strip()
    parts = [p for p in [ctx_feedback, extra] if p]
    return "\n".join(parts).strip()


_MAX_CHECKPOINT_FEEDBACK_INJECT_CHARS = 6000


def _feedback_blob_from_checkpoint_tool_logs(checkpoint: Checkpoint) -> str:
    """Summarise judge / static / escalation signals from a prior attempt for the next coder run."""
    logs = checkpoint.tool_logs or {}
    if not isinstance(logs, dict):
        return ""
    chunks: List[str] = []

    judge = logs.get("judge")
    if isinstance(judge, dict):
        parts_j: List[str] = []
        v = judge.get("verdict")
        if v:
            parts_j.append(f"Judge verdict: {v}")
        j = judge.get("justification")
        if isinstance(j, str) and j.strip():
            parts_j.append(f"Justification: {j.strip()}")
        lines = judge.get("problematic_lines")
        if isinstance(lines, list) and lines:
            parts_j.append("Problematic lines: " + ", ".join(str(x) for x in lines[:24]))
        if parts_j:
            chunks.append("\n".join(parts_j))

    static = logs.get("static_checks")
    if isinstance(static, dict):
        errs = static.get("errors")
        if isinstance(errs, list) and errs:
            chunks.append("Static checks: " + "; ".join(str(e) for e in errs[:16]))

    esc = logs.get("human_escalation")
    if isinstance(esc, str) and esc.strip():
        chunks.append(f"Coder escalation: {esc.strip()}")

    out = "\n\n".join(chunks).strip()
    if not out:
        return ""
    header = "Signals from the checkpoint you are re-running from (previous attempt):\n"
    out = header + out
    if len(out) > _MAX_CHECKPOINT_FEEDBACK_INJECT_CHARS:
        out = out[: _MAX_CHECKPOINT_FEEDBACK_INJECT_CHARS - 24].rstrip() + "\n… (truncated)"
    return out


def _merge_injected_feedback(*, prior_checkpoint: str, context_and_human: str) -> str:
    prior_checkpoint = (prior_checkpoint or "").strip()
    context_and_human = (context_and_human or "").strip()
    if prior_checkpoint and context_and_human:
        return f"{prior_checkpoint}\n\n---\n\nReviewer / task context:\n{context_and_human}".strip()
    return prior_checkpoint or context_and_human


def _run_rerun_job(
    *,
    cancel_token: CancelToken,
    dag_id: str,
    node_id: str,
    task_id: str,
    checkpoint_id: str,
    guidance: Optional[str],
) -> None:
    """Background worker: validate checkpoint, restore workspace, run ``run_verification_loop``."""
    if cancel_token.is_cancelled():
        return

    trace_rerun = str(uuid.uuid4())
    log_event(
        run_id=dag_id,
        hypothesis_id=node_id,
        location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
        message="Re-run requested (background job)",
        data={"task_id": task_id, "checkpoint_id": checkpoint_id, "trace_id": trace_rerun},
        trace_id=trace_rerun,
        dag_id=dag_id,
    )

    try:
        ref = find_task_ref(task_id=task_id, feature_id=dag_id, node_id=node_id)
    except KeyError:
        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
            message="Re-run aborted: unknown task",
            data={"task_id": task_id},
            trace_id=trace_rerun,
            dag_id=dag_id,
        )
        return
    except RuntimeError as exc:
        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
            message="Re-run aborted: task lookup error",
            data={"task_id": task_id, "error": str(exc)},
            trace_id=trace_rerun,
            dag_id=dag_id,
        )
        return

    try:
        checkpoint: Checkpoint = load_checkpoint(checkpoint_id)
    except FileNotFoundError:
        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
            message="Re-run aborted: checkpoint not found",
            data={"checkpoint_id": checkpoint_id},
            trace_id=trace_rerun,
            dag_id=dag_id,
        )
        return
    except Exception as exc:
        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
            message="Re-run aborted: checkpoint load error",
            data={"checkpoint_id": checkpoint_id, "error": str(exc)},
            trace_id=trace_rerun,
            dag_id=dag_id,
        )
        return

    if checkpoint.task_id != ref.task.task_id:
        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
            message="Re-run aborted: checkpoint does not belong to task",
            data={"checkpoint_id": checkpoint_id, "expected_task": ref.task.task_id, "got": checkpoint.task_id},
            trace_id=trace_rerun,
            dag_id=dag_id,
        )
        return

    prior_fb = _feedback_blob_from_checkpoint_tool_logs(checkpoint)
    ctx_fb = _feedback_from_context_and_guidance(task_id=task_id, guidance=guidance)
    injected = _merge_injected_feedback(prior_checkpoint=prior_fb, context_and_human=ctx_fb)
    task_to_run = _build_task_intent_with_injected_guidance(task=ref.task, injected=injected)

    _set_node_state(
        dag_id=dag_id,
        node_id=node_id,
        status=DagNodeStatus.RUNNING,
        verification_status=None,
        bump_attempts=True,
    )
    try:
        invalidate_features_and_triage_caches()
    except Exception:
        pass

    if cancel_token.is_cancelled():
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.FAILED,
            verification_status=VerificationStatus.BLOCKED,
            bump_attempts=False,
        )
        try:
            invalidate_features_and_triage_caches()
        except Exception:
            pass
        return

    rollback_to_checkpoint(ref.task, checkpoint)

    if cancel_token.is_cancelled():
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.FAILED,
            verification_status=VerificationStatus.BLOCKED,
            bump_attempts=False,
        )
        try:
            invalidate_features_and_triage_caches()
        except Exception:
            pass
        return

    try:
        with hold_repo_execution_lock([str(task_to_run.repo_path)], acquire_timeout_s=300.0):
            final_state = run_verification_loop(
                task_to_run,
                cancel_token=cancel_token,
                trace_id=trace_rerun,
                dag_id=dag_id,
            )
    except RepoLockTimeoutError as exc:
        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
            message="Re-run failed: workspace lock timeout (another run likely holds the repo)",
            data={"task_id": task_id, "error": str(exc)},
            trace_id=trace_rerun,
            dag_id=dag_id,
        )
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.FAILED,
            verification_status=VerificationStatus.BLOCKED,
            bump_attempts=False,
        )
        try:
            invalidate_features_and_triage_caches()
        except Exception:
            pass
        return
    except Exception as exc:
        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
            message="Re-run failed: verification loop raised",
            data={"task_id": task_id, "error": str(exc)},
            trace_id=trace_rerun,
            dag_id=dag_id,
        )
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.FAILED,
            verification_status=VerificationStatus.BLOCKED,
            bump_attempts=False,
        )
        try:
            invalidate_features_and_triage_caches()
        except Exception:
            pass
        return

    cp = getattr(final_state, "checkpoint", None)
    cp_status = getattr(cp, "status", None)

    if cp_status == VerificationStatus.PASSED:
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.PASSED_VERIFICATION,
            verification_status=cp_status,
            bump_attempts=False,
        )
    elif cp_status == VerificationStatus.PASSED_PENDING_SIGNOFF:
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.AWAITING_SIGNOFF,
            verification_status=cp_status,
            bump_attempts=False,
        )
    else:
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.FAILED,
            verification_status=cp_status or VerificationStatus.BLOCKED,
            bump_attempts=False,
        )

    try:
        invalidate_features_and_triage_caches()
    except Exception:
        pass

    log_event(
        run_id=dag_id,
        hypothesis_id=node_id,
        location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
        message="Re-run finished (background job)",
        data={"task_id": task_id, "checkpoint_id": checkpoint_id, "cp_status": str(cp_status)},
        trace_id=trace_rerun,
        dag_id=dag_id,
    )


class RerunRequestBody(BaseModel):
    task_id: str
    checkpoint_id: str
    guidance: Optional[str] = Field(default=None, description="Optional context injector textarea content.")
    feature_id: Optional[str] = None
    node_id: Optional[str] = None


class AbortRequestBody(BaseModel):
    task_id: str
    feature_id: Optional[str] = None
    node_id: Optional[str] = None
    abort_reason: Optional[str] = None


class TaskContextRequestBody(BaseModel):
    task_id: str
    doc_url: str
    notes: Optional[str] = None


class ApplyAndRerunRequestBody(BaseModel):
    task_id: str
    checkpoint_id: str
    guidance: Optional[str] = None
    doc_url: Optional[str] = None
    feature_id: Optional[str] = None
    node_id: Optional[str] = None


class EditIntentRequestBody(BaseModel):
    macro_intent: str


class ExecutionExtras(BaseModel):
    """Optional behaviour toggles layered on top of the base mode (RunPlan flags)."""

    doc: bool = Field(
        default=False,
        description="Run the doc_fetcher prefix that distils PRD/API docs into the macro intent.",
    )
    diff_gate: bool = Field(
        default=False,
        description="Insert the diff_validator gate between the coder and the judge.",
    )
    lint_type: bool = Field(
        default=False,
        description="Run the linter + type_checker agents and fold their findings into the judge prompt.",
    )
    memory_summarizer: bool = Field(
        default=False,
        description=(
            "Before each retry, replace raw judge justification with a focused hint synthesised by "
            "memory_summarizer_v1 from attempt history + similar past episodes."
        ),
    )
    supreme_court: bool = Field(
        default=False,
        description=(
            "After retries are exhausted, invoke supreme_court_v1 to arbitrate: PASS_OVERRIDE promotes "
            "to PASSED; ESCALATE_HUMAN marks BLOCKED with human_review_required; CONFIRM_BLOCKED is the default."
        ),
    )


# Reverse map: a RunPlan tuple → the legacy pipeline_mode string EditTaskSpec uses.
# Kept in lockstep with PIPELINE_MODES; any combination not in this table is rejected
# upstream so the chain resolvers always see a valid mode.
def _runplan_to_legacy_mode(*, gather_doc: bool, write_tests: bool, diff_gate: bool, lint_type_gate: bool) -> Optional[str]:
    key = (gather_doc, write_tests, diff_gate, lint_type_gate)
    return {
        (False, False, False, False): "patch",
        (False, False, True, False): "diff_guard_patch",
        (False, True, False, False): "build",
        (False, True, True, False): "secure_build_plus",
        (True, True, True, False): "product_eng",
        (False, True, False, True): "lint_type_gate",
    }.get(key)


def _resolve_internal_pipeline_mode(
    mode: Optional[str],
    extras: ExecutionExtras,
) -> Optional[str]:
    """
    Translate the dashboard ``(mode, extras)`` payload into a legacy
    ``pipeline_mode`` string that ``EditTaskSpec`` carries through the loop.

    Today the chain resolver still keys off ``pipeline_mode``; this is the
    single conversion site so everything else can think in terms of RunPlan.
    """
    if mode is None:
        return None
    base = mode.strip().lower()
    if base not in {"patch", "build"}:
        raise ValueError(f"Unknown mode={mode!r}; expected 'patch', 'build', or null.")

    write_tests = base == "build"
    legacy = _runplan_to_legacy_mode(
        gather_doc=bool(extras.doc),
        write_tests=write_tests,
        diff_gate=bool(extras.diff_gate),
        lint_type_gate=bool(extras.lint_type),
    )
    if legacy is None:
        raise ValueError(
            f"Unsupported combination: mode={base!r}, extras={extras.model_dump()}. "
            "Run the dashboard with one of the supported presets, or omit conflicting toggles."
        )
    return legacy


class ExecuteDagFromDashboardRequestBody(BaseModel):
    repo_path: str = Field(description="Absolute or relative path to the target repository root.")
    macro_intent: str = Field(description="Helix command / macro intent that compiles into a DAG.")
    agent_ids: List[str] = Field(
        description="Selected agents. When mode is set, agent_ids are informational only.",
        default=["coder_patch_v1", "judge_v1"],
    )
    dag_id: Optional[str] = Field(default=None, description="Optional DAG id (feature id).")
    mode: Optional[str] = Field(
        default=None,
        description=(
            'Base execution mode: "patch" for fast single-file line-patches, "build" for full TDD. '
            "Set to null to let the LLM intent compiler pick per node."
        ),
    )
    extras: ExecutionExtras = Field(
        default_factory=ExecutionExtras,
        description="Optional behaviour toggles (doc, diff_gate, lint_type).",
    )
    doc_url: Optional[str] = Field(
        default=None,
        description="Optional documentation URL for doc_fetcher (extras.doc). Ignored when doc_text is set.",
    )
    doc_text: Optional[str] = Field(
        default=None,
        description="Optional uploaded documentation body; written under .agenti_helix/ and referenced via file URI.",
    )
    doc_filename: Optional[str] = Field(
        default=None,
        description="Original filename for uploaded doc (used to pick .md/.txt extension).",
    )


class UpdateNodeChainsRequestBody(BaseModel):
    # When a chain is omitted (or set to null), execution uses the system defaults.
    coder_chain: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional custom coder chain DSL object. Use null to reset to default.",
    )
    judge_chain: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional custom judge chain DSL object. Use null to reset to default.",
    )
    start_execution: bool = Field(
        default=True,
        description="If true, re-executes the DAG using the updated node chain configuration.",
    )


class MergeRequestBody(BaseModel):
    task_id: str
    checkpoint_id: str
    target_branch: Optional[str] = "main"
    commit_message: Optional[str] = None


class SignoffApplyRequestBody(BaseModel):
    task_id: str
    checkpoint_id: str
    signed_by: Optional[str] = Field(default=None, description="Optional reviewer id or display name for audit trail.")


def _schedule_verification_rerun(body: RerunRequestBody) -> Dict[str, Any]:
    """Validate task + checkpoint, then enqueue ``_run_rerun_job`` (HTTP handlers call this)."""
    try:
        ref = find_task_ref(task_id=body.task_id, feature_id=body.feature_id, node_id=body.node_id)
    except KeyError:
        raise_http_error(code="task_not_found", message="Unknown task_id", status_code=404)
    except RuntimeError as exc:
        raise_http_error(code="task_lookup_error", message=str(exc), status_code=500)

    try:
        checkpoint: Checkpoint = load_checkpoint(body.checkpoint_id)
    except FileNotFoundError:
        raise_http_error(code="checkpoint_not_found", message="Unknown checkpoint_id", status_code=404)
    except Exception:
        raise_http_error(code="checkpoint_not_found", message="Unknown checkpoint_id", status_code=404)

    if checkpoint.task_id != ref.task.task_id:
        raise_http_error(code="checkpoint_mismatch", message="checkpoint_id does not belong to task_id", status_code=409)

    task_key = f"{ref.dag_id}|{ref.node_id}|{ref.task.task_id}"
    start_background_job(
        meta={"task_id": ref.task.task_id, "checkpoint_id": body.checkpoint_id, "action": "rerun"},
        task_key=task_key,
        target=lambda cancel_token: _run_rerun_job(
            cancel_token=cancel_token,
            dag_id=ref.dag_id,
            node_id=ref.node_id,
            task_id=ref.task.task_id,
            checkpoint_id=body.checkpoint_id,
            guidance=body.guidance,
        ),
    )
    try:
        invalidate_features_and_triage_caches()
    except Exception:
        pass
    return {"ok": True, "reRunId": task_key}


@router.post("/api/tasks/rerun")
def rerun_task(body: RerunRequestBody, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    return _schedule_verification_rerun(body)


@router.post("/api/tasks/abort")
def abort_task(body: AbortRequestBody, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    try:
        ref = find_task_ref(task_id=body.task_id, feature_id=body.feature_id, node_id=body.node_id)
    except KeyError:
        raise_http_error(code="task_not_found", message="Unknown task_id", status_code=404)

    # Best-effort: update persisted node state immediately so UI tone changes.
    abort_state = _set_node_state(
        dag_id=ref.dag_id,
        node_id=ref.node_id,
        status=DagNodeStatus.FAILED,
        verification_status=VerificationStatus.BLOCKED,
        bump_attempts=False,
    )

    log_event(
        run_id=ref.dag_id,
        hypothesis_id=ref.node_id,
        location="agenti_helix/api/task_commands_routes.py:abort_task",
        message="Abort task requested",
        data={"task_id": ref.task.task_id, "abort_reason": body.abort_reason},
    )

    # In this pass we cancel best-effort via job_registry. Cooperative stopping is added later.
    from agenti_helix.api.job_registry import cancel_running_job_for_task

    task_key = f"{ref.dag_id}|{ref.node_id}|{ref.task.task_id}"
    cancelled = cancel_running_job_for_task(dag_id=ref.dag_id, node_id=ref.node_id, task_id=ref.task.task_id)

    return {"ok": True, "aborted": bool(cancelled) or abort_state is not None}


@router.post("/api/tasks/context")
def attach_task_context(body: TaskContextRequestBody, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    # Do not fetch remote URLs server-side; we only persist the reference + notes.
    save_task_context(task_id=body.task_id, doc_url=body.doc_url, notes=body.notes)
    log_event(
        run_id="context",
        hypothesis_id=body.task_id,
        location="agenti_helix/api/task_commands_routes.py:attach_task_context",
        message="Stored doc_url/notes for task context",
        data={"task_id": body.task_id},
    )
    return {"ok": True}


@router.post("/api/tasks/apply-and-rerun")
def apply_and_rerun(body: ApplyAndRerunRequestBody, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    if body.doc_url:
        save_task_context(task_id=body.task_id, doc_url=body.doc_url, notes=None)

    return _schedule_verification_rerun(
        RerunRequestBody(
            task_id=body.task_id,
            checkpoint_id=body.checkpoint_id,
            guidance=body.guidance,
            feature_id=body.feature_id,
            node_id=body.node_id,
        )
    )


@router.put("/api/dags/{dag_id}/intent")
def edit_dag_intent(dag_id: str, body: EditIntentRequestBody, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    # Recompile DAG spec and run it (background) so UI can poll.
    repo_root = str(PATHS.repo_root)
    try:
        spec = compile_macro_intent_to_dag(
            body.macro_intent,
            repo_path=repo_root,
            dag_id=dag_id,
            user_intent_label=body.macro_intent.strip(),
        )
    except Exception as exc:
        raise_http_error(
            code="intent_compile_failed",
            message=f"Failed to compile macro intent into a DAG: {exc}",
            status_code=422,
        )

    # Ensure the DAG id and task ids align with the URL feature id so UI polling is consistent.
    spec.dag_id = dag_id
    for node_id, node in spec.nodes.items():
        node.task.task_id = f"{dag_id}:{node_id}"
        node.task.repo_path = repo_root

    persist_dag_spec(spec)
    invalidate_features_and_triage_caches()

    start_background_job(
        meta={"dag_id": dag_id, "action": "edit-intent"},
        target=lambda _cancel_token: execute_dag(spec),
    )

    log_event(
        run_id=dag_id,
        hypothesis_id="INTENT",
        location="agenti_helix/api/task_commands_routes.py:edit_dag_intent",
        message="DAG intent edited and execution scheduled",
        data={"dag_id": dag_id},
    )

    return {"ok": True}


def _patch_chain_agent(chain: Dict[str, Any], *, step_id: str, agent_id: str) -> Dict[str, Any]:
    """
    Mutate a chain DSL dict to swap the `agent_id` used by a specific agent step.

    This keeps the surrounding tool steps intact so the chain I/O contracts remain valid.
    """
    out = copy.deepcopy(chain)
    steps = out.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Invalid chain DSL: missing `steps` list.")
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") == "agent" and step.get("id") == step_id:
            step["agent_id"] = agent_id
    return out


@router.post("/api/dags/run")
def run_dag_from_dashboard(body: ExecuteDagFromDashboardRequestBody, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    """
    UI entrypoint for starting a new DAG from:
      - a selected local repository (`repo_path`)
      - a command (`macro_intent`)
      - an execution `mode` ("patch" or "build", or null to let the LLM decide)
      - optional `extras` toggles (doc, diff_gate, lint_type)

    When mode is "build", the full TDD pipeline
    (librarian → sdet → coder_builder → governor → judge_evaluator) runs per node.
    When "patch", the fast single-file coder_patch_v1 + judge_v1 chain runs.
    When null, the orchestrator assigns the pipeline per node based on the LLM compiler's output.
    """
    dag_id = body.dag_id or f"dag-ui-run-{int(time.time())}"

    _macro_intent = body.macro_intent
    _repo_path = _validate_dashboard_repo_path(body.repo_path)
    try:
        _pipeline_mode = _resolve_internal_pipeline_mode(body.mode, body.extras)
    except ValueError as exc:
        raise_http_error(code="invalid_mode", message=str(exc), status_code=400)
    if _pipeline_mode is not None and _pipeline_mode not in PIPELINE_MODES:
        raise_http_error(
            code="invalid_pipeline_mode",
            message=f"Resolved pipeline_mode={_pipeline_mode!r} is not in {sorted(PIPELINE_MODES)}",
            status_code=500,
        )
    _doc_url_opt = (body.doc_url or "").strip() or None
    _doc_text = body.doc_text
    _doc_filename = body.doc_filename

    def _compile_and_execute(_cancel_token: object) -> None:
        """Compile the DAG spec via LLM and execute it — all in the background."""
        try:
            macro_for_compile, effective_doc, doc_merged_at_compile = enrich_macro_intent_with_doc_before_compile(
                _macro_intent,
                repo_path=_repo_path,
                dag_id=dag_id,
                doc_url=_doc_url_opt,
                doc_text=_doc_text,
                doc_filename=_doc_filename,
            )
        except ValueError as exc:
            log_event(
                run_id=dag_id,
                hypothesis_id="orchestrator",
                location="agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
                message="Dashboard doc attachment rejected — DAG will not run",
                data={"error": str(exc)},
            )
            return

        try:
            spec = compile_macro_intent_to_dag(
                macro_for_compile,
                repo_path=_repo_path,
                dag_id=dag_id,
                user_intent_label=_macro_intent.strip(),
            )
        except Exception as exc:
            log_event(
                run_id=dag_id,
                hypothesis_id="orchestrator",
                location="agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
                message="Intent compile failed — DAG will not run",
                data={"error": str(exc)},
            )
            return

        # Intent JSON may name a different dag_id than the dashboard assigned; features + UI key off `dag_id`.
        spec.dag_id = dag_id
        for nid, node in spec.nodes.items():
            node.task.task_id = f"{dag_id}:{nid}"

        if effective_doc:
            for node in spec.nodes.values():
                node.task.doc_url = effective_doc
                if doc_merged_at_compile:
                    node.task.skip_doc_chain_prefix = True

        if _pipeline_mode is not None:
            for node in spec.nodes.values():
                node.task.pipeline_mode = _pipeline_mode

        # Retry-loop opt-ins are orthogonal to pipeline_mode (they govern the
        # loop's behaviour *after* per-attempt judge returns, not the coder
        # chain). Apply unconditionally when enabled in extras.
        if body.extras.memory_summarizer or body.extras.supreme_court:
            for node in spec.nodes.values():
                if body.extras.memory_summarizer:
                    node.task.enable_memory_summarizer = True
                if body.extras.supreme_court:
                    node.task.enable_supreme_court = True

        # Ensure the DAG id and task ids align with the UI-requested feature id so UI polling is consistent.
        # The intent compiler may emit its own `dag_id` (e.g. a slug), but the dashboard run expects `dag_id`.
        spec.dag_id = dag_id
        for _node_id, node in spec.nodes.items():
            node.task.task_id = f"{dag_id}:{_node_id}"
            node.task.repo_path = str(_repo_path)

        persist_dag_spec(spec)
        invalidate_features_and_triage_caches()
        execute_dag(spec)

    start_background_job(
        meta={
            "dag_id": dag_id,
            "action": "ui-run",
            "mode": body.mode,
            "extras": body.extras.model_dump(),
            "pipeline_mode": _pipeline_mode,
        },
        target=_compile_and_execute,
    )

    return {"ok": True, "dag_id": dag_id}


@router.put("/api/dags/{dag_id}/nodes/{node_id}/chains")
def update_node_chains(
    dag_id: str, node_id: str, body: UpdateNodeChainsRequestBody, _role: Role = Depends(require_editor)
) -> Dict[str, Any]:
    spec_path = PATHS.dags_dir / f"{dag_id}.json"
    if not spec_path.exists():
        raise_http_error(code="dag_not_found", message="DAG not found", status_code=404)

    spec = read_json(spec_path)
    if not isinstance(spec, dict):
        raise_http_error(code="dag_spec_invalid", message="DAG spec is invalid JSON", status_code=500)

    nodes = spec.get("nodes")
    if not isinstance(nodes, dict):
        raise_http_error(code="dag_spec_invalid", message="DAG spec has no nodes object", status_code=500)

    node_data = nodes.get(node_id) or nodes.get(str(node_id))
    if not isinstance(node_data, dict):
        raise_http_error(code="node_not_found", message=f"Node not found: node_id={node_id!r}", status_code=404)

    task = node_data.get("task")
    if not isinstance(task, dict):
        raise_http_error(code="node_task_invalid", message=f"Node task missing/invalid for node_id={node_id!r}", status_code=500)

    # Avoid resetting fields the user didn't provide.
    fields_set = getattr(body, "model_fields_set", set())
    if "coder_chain" in fields_set:
        task["coder_chain"] = body.coder_chain
    if "judge_chain" in fields_set:
        task["judge_chain"] = body.judge_chain

    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")

    if body.start_execution:
        spec_obj = load_dag_spec(dag_id)
        start_background_job(
            meta={"dag_id": dag_id, "node_id": node_id, "action": "update-node-chains"},
            target=lambda _cancel_token: execute_dag(spec_obj),
        )

        log_event(
            run_id=dag_id,
            hypothesis_id=node_id,
            location="agenti_helix/api/task_commands_routes.py:update_node_chains",
            message="Node chains updated and execution scheduled",
            data={"dag_id": dag_id, "node_id": node_id},
        )

    return {"ok": True}


@router.get("/api/memory")
def get_episodic_memory(
    query: str = "",
    limit: int = 25,
    _role: Role = Depends(require_auth),
) -> Dict[str, Any]:
    """
    Return episodic memory episodes.

    When `query` is provided, returns the top `limit` episodes most similar
    to `query` (Jaccard token overlap).  When `query` is empty, returns the
    most recently added `limit` episodes.
    """
    from agenti_helix.memory.store import get_default_store

    store = get_default_store()

    if query:
        episodes = store.query(query, top_k=limit)
    else:
        all_eps = store.load_all()
        all_eps.sort(key=lambda e: e.created_at, reverse=True)
        episodes = all_eps[:limit]

    items = [
        {
            "episode_id": ep.episode_id,
            "task_id": ep.task_id,
            "dag_id": ep.dag_id,
            "target_file": ep.target_file,
            "error_text": ep.error_text,
            "resolution": ep.resolution,
            "created_at": ep.created_at,
        }
        for ep in episodes
    ]

    total = store.count()
    summary = (
        f"Episodic memory: {total} episode(s) stored."
        if total > 0
        else "Episodic memory is empty — no resolved errors indexed yet."
    )

    return {"summary": summary, "total": total, "items": items}


@router.post("/api/dags/{dag_id}/nodes/{node_id}/signoff-apply")
def apply_node_signoff(
    dag_id: str,
    node_id: str,
    body: SignoffApplyRequestBody,
    _role: Role = Depends(require_editor),
) -> Dict[str, Any]:
    """
    Materialize a judge-approved patch after manual sign-off (patch pipeline only).

    Expects checkpoint status ``PASSED_PENDING_SIGNOFF`` with staged ``post_state_ref``.
    """
    try:
        ref = find_task_ref(task_id=body.task_id, feature_id=dag_id, node_id=node_id)
    except KeyError:
        raise_http_error(code="task_not_found", message="Unknown task_id for this DAG/node", status_code=404)
    except RuntimeError as exc:
        raise_http_error(code="task_lookup_error", message=str(exc), status_code=500)

    try:
        checkpoint = load_checkpoint(body.checkpoint_id)
    except FileNotFoundError:
        raise_http_error(code="checkpoint_not_found", message="Unknown checkpoint_id", status_code=404)

    if checkpoint.task_id != ref.task.task_id:
        raise_http_error(code="checkpoint_mismatch", message="checkpoint_id does not belong to task_id", status_code=409)

    if checkpoint.status != VerificationStatus.PASSED_PENDING_SIGNOFF:
        raise_http_error(
            code="checkpoint_not_staged",
            message="Checkpoint must be PASSED_PENDING_SIGNOFF (judge-approved, not yet applied)",
            status_code=409,
        )

    try:
        apply_signed_off_checkpoint(task=ref.task, checkpoint=checkpoint, signed_by=body.signed_by)
    except ValueError as exc:
        raise_http_error(code="signoff_apply_failed", message=str(exc), status_code=409)

    _set_node_state(
        dag_id=dag_id,
        node_id=node_id,
        status=DagNodeStatus.PASSED_VERIFICATION,
        verification_status=VerificationStatus.PASSED,
        bump_attempts=False,
    )
    invalidate_features_and_triage_caches()

    log_event(
        run_id=dag_id,
        hypothesis_id=node_id,
        location="agenti_helix/api/task_commands_routes.py:apply_node_signoff",
        message="Manual sign-off applied — workspace updated to staged post-state",
        data={"task_id": body.task_id, "checkpoint_id": body.checkpoint_id},
    )

    return {"ok": True}


@router.post("/api/dags/{dag_id}/resume")
def resume_dag_execution(dag_id: str, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    """
    Continue DAG execution after upstream nodes reached ``PASSED_VERIFICATION``
    (e.g. following ``signoff-apply`` on an ``AWAITING_SIGNOFF`` node).
    """
    try:
        spec = load_dag_spec(dag_id)
    except FileNotFoundError:
        raise_http_error(code="dag_not_found", message=f"DAG not found for dag_id={dag_id!r}", status_code=404)

    start_background_job(
        meta={"dag_id": dag_id, "action": "resume-dag"},
        target=lambda _cancel_token: execute_dag(spec),
    )
    log_event(
        run_id=dag_id,
        hypothesis_id="resume",
        location="agenti_helix/api/task_commands_routes.py:resume_dag_execution",
        message="DAG resume scheduled",
        data={"dag_id": dag_id},
    )
    return {"ok": True}


@router.post("/api/tasks/merge")
def merge_task_to_main(body: MergeRequestBody, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    ref = find_task_ref(task_id=body.task_id, feature_id=None, node_id=None)
    checkpoint: Checkpoint = load_checkpoint(body.checkpoint_id)

    if checkpoint.task_id != ref.task.task_id:
        raise_http_error(code="checkpoint_mismatch", message="checkpoint_id does not belong to task_id", status_code=409)

    if checkpoint.status != VerificationStatus.PASSED:
        raise_http_error(code="checkpoint_not_verified", message="Checkpoint is not in PASSED state", status_code=409)

    try:
        materialize_passed_checkpoint_to_workspace(task=ref.task, checkpoint=checkpoint)
    except ValueError as exc:
        raise_http_error(code="checkpoint_materialize_failed", message=str(exc), status_code=409)

    # §4.6 — Real git commit (or simulation when AGENTI_HELIX_GIT_COMMIT_ENABLED is unset).
    repo_root_path = PATHS.repo_root.resolve()
    target_files = _merge_stage_paths(repo_root=repo_root_path, task=ref.task, checkpoint=checkpoint)
    if not target_files:
        raise_http_error(
            code="merge_no_target_files",
            message="Could not resolve target file paths for merge (missing task.target_file).",
            status_code=500,
        )

    try:
        commit_result = real_git_commit(
            repo_path=str(repo_root_path),
            target_files=target_files,
            commit_message=body.commit_message
            or f"feat(agenti): {ref.task.intent[:80] if hasattr(ref.task, 'intent') else body.task_id}",
            trace_id=getattr(checkpoint, "trace_id", None),
            dag_id=ref.dag_id,
            intent_summary=getattr(ref.task, "intent", None) if hasattr(ref, "task") else None,
            target_branch=body.target_branch or "main",
        )
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        raise_http_error(code="merge_commit_failed", message=str(exc), status_code=500)

    merges_dir = PATHS.agenti_root / "merges"
    merges_dir.mkdir(parents=True, exist_ok=True)
    merge_ref = f"merge_{uuid.uuid4().hex[:10]}.json"
    out_path = merges_dir / merge_ref
    out_path.write_text(
        json.dumps(
            {
                "task_id": body.task_id,
                "checkpoint_id": body.checkpoint_id,
                "dag_id": ref.dag_id,
                "target_branch": body.target_branch,
                "commit_message": body.commit_message,
                "diff": checkpoint.diff,
                "created_at": int(time.time()),
                # §4.6 — Commit SHA embedded for blame lookups.
                "commit_sha": commit_result.get("sha"),
                "git_simulated": commit_result.get("simulated", True),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    simulated_note = " (simulated — set AGENTI_HELIX_GIT_COMMIT_ENABLED=true for real commits)" if commit_result.get("simulated") else ""
    log_event(
        run_id=ref.dag_id,
        hypothesis_id=ref.node_id,
        location="agenti_helix/api/task_commands_routes.py:merge_task_to_main",
        message=f"Merged to main{simulated_note}",
        data={"task_id": body.task_id, "checkpoint_id": body.checkpoint_id, "mergeRef": merge_ref, "sha": commit_result.get("sha")},
    )

    invalidate_features_and_triage_caches()

    return {"ok": True, "mergeRef": merge_ref, "sha": commit_result.get("sha"), "simulated": commit_result.get("simulated", True)}

