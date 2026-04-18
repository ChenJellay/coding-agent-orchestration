from __future__ import annotations

import copy
import dataclasses
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agenti_helix.api.auth import Role, require_editor
from agenti_helix.api.errors import raise_http_error
from agenti_helix.api.response_caches import invalidate_features_and_triage_caches
from agenti_helix.api.job_registry import CancelToken, cancel_running_job_for_task, start_background_job
from agenti_helix.api.paths import PATHS, iter_jsonl, read_json
from agenti_helix.api.task_context_store import load_task_context, render_task_context_feedback, save_task_context
from agenti_helix.api.task_lookup import find_task_ref, persist_dag_state, try_load_dag_state
from agenti_helix.observability.debug_log import log_event
from agenti_helix.orchestration.orchestrator import DagNodeStatus, execute_dag, load_dag_spec, persist_dag_spec
from agenti_helix.orchestration.intent_compiler import compile_macro_intent_to_dag
from agenti_helix.runtime.chain_defaults import default_coder_chain, default_judge_chain
from agenti_helix.verification.checkpointing import (
    EditTaskSpec,
    Checkpoint,
    VerificationStatus,
    apply_signed_off_checkpoint,
    load_checkpoint,
    rollback_to_checkpoint,
)
from agenti_helix.verification.verification_loop import run_verification_loop
from agenti_helix.api.git_ops import real_git_commit


router = APIRouter()


@router.delete("/api/dags/{dag_id}")
def remove_dag_from_workflow(dag_id: str, _role: Role = Depends(require_editor)) -> Dict[str, Any]:
    """Remove a DAG from the control-plane workflow.

    - Cancels any running background jobs for its nodes (best-effort).
    - Deletes the persisted DAG spec + DAG state from disk.
    """
    spec_path = PATHS.dags_dir / f"{dag_id}.json"
    state_path = PATHS.dags_dir / f"{dag_id}_state.json"

    removed_spec = False
    removed_state = False

    # Best-effort cancel: parse nodes -> task_ids, then cancel jobs keyed by dag_id|node_id|task_id.
    if spec_path.exists():
        try:
            spec = read_json(spec_path)
            nodes = spec.get("nodes") if isinstance(spec, dict) else None
            if isinstance(nodes, dict):
                for node_id, node_data in nodes.items():
                    if not isinstance(node_data, dict):
                        continue
                    task = node_data.get("task")
                    if not isinstance(task, dict):
                        continue
                    tid = task.get("task_id")
                    if isinstance(tid, str) and tid:
                        cancel_running_job_for_task(dag_id=dag_id, node_id=str(node_id), task_id=tid)
        except Exception:
            pass

    if spec_path.exists():
        try:
            spec_path.unlink()
            removed_spec = True
        except OSError as exc:
            raise_http_error(code="dag_delete_failed", message=f"Could not delete DAG spec: {exc}", status_code=500)

    if state_path.exists():
        try:
            state_path.unlink()
            removed_state = True
        except OSError as exc:
            raise_http_error(code="dag_state_delete_failed", message=f"Could not delete DAG state: {exc}", status_code=500)

    if not removed_spec and not removed_state:
        raise_http_error(code="dag_not_found", message=f"No persisted DAG found for dag_id={dag_id!r}", status_code=404)

    invalidate_features_and_triage_caches()
    log_event(
        run_id=dag_id,
        hypothesis_id="workflow_removed",
        location="agenti_helix/api/task_commands_routes.py:remove_dag_from_workflow",
        message="DAG removed from workflow (spec/state deleted)",
        data={"dag_id": dag_id, "removed_spec": removed_spec, "removed_state": removed_state},
    )
    return {"ok": True, "removed_spec": removed_spec, "removed_state": removed_state}

# region agent log
def _debug_write(payload: Dict[str, Any]) -> None:
    # Minimal NDJSON logger for debug-mode sessions; avoid secrets/PII.
    try:
        import json as _json
        from pathlib import Path as _Path

        p = _Path(__file__).resolve().parents[3] / ".cursor" / "debug-a3db40.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("a", encoding="utf-8").write(_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return
# endregion agent log


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


def _run_rerun_job(
    *,
    cancel_token: CancelToken,
    dag_id: str,
    node_id: str,
    task_id: str,
    checkpoint_id: str,
    guidance: Optional[str],
) -> None:
    # The background thread does all state updates so the UI can poll safely.
    if cancel_token.is_cancelled():
        return

    log_event(
        run_id=dag_id,
        hypothesis_id=node_id,
        location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
        message="Re-run requested (background job)",
        data={"task_id": task_id, "checkpoint_id": checkpoint_id},
    )

    ref = find_task_ref(task_id=task_id, feature_id=dag_id, node_id=node_id)
    _set_node_state(
        dag_id=dag_id,
        node_id=node_id,
        status=DagNodeStatus.RUNNING,
        verification_status=None,
        bump_attempts=True,
    )

    if cancel_token.is_cancelled():
        # Best-effort: update state and stop early.
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.FAILED,
            verification_status=VerificationStatus.BLOCKED,
            bump_attempts=False,
        )
        return

    checkpoint: Checkpoint = load_checkpoint(checkpoint_id)
    if checkpoint.task_id != ref.task.task_id:
        raise_http_error(
            code="checkpoint_mismatch",
            message="checkpoint_id does not belong to task_id",
            status_code=409,
        )

    # Restore file to the provided checkpoint state so the next retry is scoped correctly.
    injected = _feedback_from_context_and_guidance(task_id=task_id, guidance=guidance)
    task_to_run = ref.task
    task_to_run = _build_task_intent_with_injected_guidance(task=task_to_run, injected=injected)
    rollback_to_checkpoint(ref.task, checkpoint)

    if cancel_token.is_cancelled():
        _set_node_state(
            dag_id=dag_id,
            node_id=node_id,
            status=DagNodeStatus.FAILED,
            verification_status=VerificationStatus.BLOCKED,
            bump_attempts=False,
        )
        return

    final_state = run_verification_loop(task_to_run, cancel_token=cancel_token)
    cp = getattr(final_state, "checkpoint", None)
    cp_status = getattr(cp, "status", None)

    # Map checkpoint status to DAG node status for UI tone.
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

    log_event(
        run_id=dag_id,
        hypothesis_id=node_id,
        location="agenti_helix/api/task_commands_routes.py:_run_rerun_job",
        message="Re-run finished (background job)",
        data={"task_id": task_id, "checkpoint_id": checkpoint_id, "cp_status": str(cp_status)},
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


class ExecuteDagFromDashboardRequestBody(BaseModel):
    repo_path: str = Field(description="Absolute or relative path to the target repository root.")
    macro_intent: str = Field(description="Helix command / macro intent that compiles into a DAG.")
    agent_ids: List[str] = Field(
        description="Selected agents. When pipeline_mode is set, agent_ids are informational only.",
        default=["coder_patch_v1", "judge_v1"],
    )
    dag_id: Optional[str] = Field(default=None, description="Optional DAG id (feature id).")
    use_llm: bool = Field(
        default=False,
        description="When true, compile intent via LLM (needs inference; can block POST until complete). "
        "When false or omitted, use the fast deterministic demo compiler (writes DAG immediately).",
    )
    pipeline_mode: Optional[str] = Field(
        default=None,
        description=(
            "Override pipeline for all DAG nodes: "
            '"patch" (fast single-file, coder_patch_v1 + judge_v1) or '
            '"build" (full TDD pipeline: librarian → sdet → coder_builder → governor → judge_evaluator). '
            "When null, the orchestrator assigns pipeline_mode per node (requires use_llm=true)."
        ),
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


@router.post("/api/tasks/rerun")
def rerun_task(body: RerunRequestBody) -> Dict[str, Any]:
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
    # Store job cancellation token now; cooperative cancellation will be added in a later step.
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

    return {"ok": True, "reRunId": task_key}


@router.post("/api/tasks/abort")
def abort_task(body: AbortRequestBody) -> Dict[str, Any]:
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
def attach_task_context(body: TaskContextRequestBody) -> Dict[str, Any]:
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
def apply_and_rerun(body: ApplyAndRerunRequestBody) -> Dict[str, Any]:
    if body.doc_url:
        save_task_context(task_id=body.task_id, doc_url=body.doc_url, notes=None)

    # Reuse the same behavior as rerun: schedule a background job and return immediately.
    resp = rerun_task(
        RerunRequestBody(
            task_id=body.task_id,
            checkpoint_id=body.checkpoint_id,
            guidance=body.guidance,
            feature_id=body.feature_id,
            node_id=body.node_id,
        )
    )
    return resp


@router.put("/api/dags/{dag_id}/intent")
def edit_dag_intent(dag_id: str, body: EditIntentRequestBody) -> Dict[str, Any]:
    # Recompile DAG spec and run it (background) so UI can poll.
    repo_root = str(PATHS.repo_root)
    try:
        spec = compile_macro_intent_to_dag(
            body.macro_intent,
            repo_path=repo_root,
            dag_id=dag_id,
            use_llm=True,
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
      - optional `pipeline_mode` override ("patch" | "build")

    When `pipeline_mode` is "build", the full TDD pipeline
    (librarian → sdet → coder_builder → governor → judge_evaluator) runs per node.
    When "patch" (default), the fast single-file coder_patch_v1 + judge_v1 chain runs.
    When null and use_llm=true, the orchestrator assigns pipeline_mode per node.
    """
    dag_id = body.dag_id or f"dag-ui-run-{int(time.time())}"

    # Capture body fields for use inside the closure (avoid late-binding issues).
    _macro_intent = body.macro_intent
    _repo_path = body.repo_path
    _use_llm = body.use_llm
    _pipeline_mode = body.pipeline_mode

    # region agent log
    _debug_write(
        {
            "sessionId": "a3db40",
            "runId": "pre-fix",
            "hypothesisId": "H3",
            "location": "agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
            "message": "UI requested DAG run",
            "data": {
                "dag_id": dag_id,
                "use_llm": bool(_use_llm),
                "pipeline_mode": _pipeline_mode,
                "macro_intent_len": len(_macro_intent or ""),
                "repo_path_tail": str(_repo_path)[-120:],
            },
            "timestamp": int(time.time() * 1000),
        }
    )
    # endregion agent log

    def _compile_and_execute(_cancel_token: object) -> None:
        """Compile the DAG spec (possibly via LLM) and execute it — all in the background."""
        try:
            spec = compile_macro_intent_to_dag(
                _macro_intent,
                repo_path=_repo_path,
                dag_id=dag_id,
                use_llm=_use_llm,
            )
        except Exception as exc:
            # region agent log
            _debug_write(
                {
                    "sessionId": "a3db40",
                    "runId": "pre-fix",
                    "hypothesisId": "H4",
                    "location": "agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
                    "message": "Intent compile failed; DAG execution aborted",
                    "data": {"dag_id": dag_id, "error": str(exc)[:2000]},
                    "timestamp": int(time.time() * 1000),
                }
            )
            # endregion agent log
            log_event(
                run_id=dag_id,
                hypothesis_id="orchestrator",
                location="agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
                message="Intent compile failed — DAG will not run",
                data={"error": str(exc)},
            )
            return

        # region agent log
        _debug_write(
            {
                "sessionId": "a3db40",
                "runId": "pre-fix",
                "hypothesisId": "H5",
                "location": "agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
                "message": "Intent compile succeeded; about to execute DAG",
                "data": {"dag_id": dag_id, "node_ids": sorted(list(spec.nodes.keys()))[:50], "node_count": len(spec.nodes)},
                "timestamp": int(time.time() * 1000),
            }
        )
        # endregion agent log

        # Apply pipeline_mode override when provided.
        for _node_id, node in spec.nodes.items():
            if _pipeline_mode is not None:
                node.task.pipeline_mode = _pipeline_mode
            elif not _use_llm:
                node.task.pipeline_mode = "patch"

        # Ensure the DAG id and task ids align with the UI-requested feature id so UI polling is consistent.
        # The intent compiler may emit its own `dag_id` (e.g. a slug), but the dashboard run expects `dag_id`.
        spec.dag_id = dag_id
        for _node_id, node in spec.nodes.items():
            node.task.task_id = f"{dag_id}:{_node_id}"
            node.task.repo_path = str(_repo_path)

        persist_dag_spec(spec)
        invalidate_features_and_triage_caches()
        # region agent log
        _debug_write(
            {
                "sessionId": "a3db40",
                "runId": "pre-fix",
                "hypothesisId": "H9",
                "location": "agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
                "message": "Calling execute_dag(spec)",
                "data": {"dag_id": dag_id},
                "timestamp": int(time.time() * 1000),
            }
        )
        # endregion agent log
        execute_dag(spec)
        # region agent log
        _debug_write(
            {
                "sessionId": "a3db40",
                "runId": "pre-fix",
                "hypothesisId": "H10",
                "location": "agenti_helix/api/task_commands_routes.py:run_dag_from_dashboard",
                "message": "execute_dag(spec) returned",
                "data": {"dag_id": dag_id},
                "timestamp": int(time.time() * 1000),
            }
        )
        # endregion agent log

    start_background_job(
        meta={"dag_id": dag_id, "action": "ui-run", "pipeline_mode": body.pipeline_mode},
        target=_compile_and_execute,
    )

    return {"ok": True, "dag_id": dag_id}


@router.put("/api/dags/{dag_id}/nodes/{node_id}/chains")
def update_node_chains(dag_id: str, node_id: str, body: UpdateNodeChainsRequestBody) -> Dict[str, Any]:
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
def get_episodic_memory(query: str = "", limit: int = 25) -> Dict[str, Any]:
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

    # §4.6 — Real git commit (or simulation when AGENTI_HELIX_GIT_COMMIT_ENABLED is unset).
    repo_path = str(PATHS.repo_root) if hasattr(PATHS, "repo_root") else str(PATHS.agenti_root.parent)
    # Derive target files from the checkpoint diff when available.
    target_files: List[str] = []
    if checkpoint.diff:
        try:
            diff_obj = json.loads(checkpoint.diff)
            if diff_obj.get("filePath"):
                target_files = [diff_obj["filePath"]]
        except Exception:
            pass

    commit_result = real_git_commit(
        repo_path=repo_path,
        target_files=target_files,
        commit_message=body.commit_message or f"feat(agenti): {ref.task.intent[:80] if hasattr(ref.task, 'intent') else body.task_id}",
        trace_id=getattr(checkpoint, "trace_id", None),
        dag_id=ref.dag_id,
        intent_summary=getattr(ref.task, "intent", None) if hasattr(ref, "task") else None,
    )

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

    return {"ok": True, "mergeRef": merge_ref, "sha": commit_result.get("sha"), "simulated": commit_result.get("simulated", True)}

