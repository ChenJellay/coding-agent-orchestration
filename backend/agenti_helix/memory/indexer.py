"""
Episodic memory indexer.

Called by the verification loop after a successful retry to record the
error→resolution pair for future retrieval.
"""
from __future__ import annotations

import uuid
from typing import Optional

from .store import Episode, MemoryStore, get_default_store


def index_resolved_episode(
    store: MemoryStore,
    *,
    error_text: str,
    resolution: str,
    task_id: str,
    target_file: str = "",
    dag_id: str = "",
    metadata: Optional[dict] = None,
) -> Episode:
    """
    Create and persist an episode representing a resolved error.

    Parameters
    ----------
    store:
        The `MemoryStore` to write to.
    error_text:
        The error or judge justification that caused the retry.
    resolution:
        A description of the patch / change that resolved the error.
    task_id:
        The task that produced this episode.
    target_file:
        File that was edited.
    dag_id:
        Parent DAG id for traceability.
    metadata:
        Any additional key/value context to store.
    """
    episode = Episode(
        episode_id=str(uuid.uuid4()),
        task_id=task_id,
        error_text=error_text,
        resolution=resolution,
        target_file=target_file,
        dag_id=dag_id,
        metadata=metadata or {},
    )
    store.add(episode)
    return episode


def index_from_verification_state(
    state: object,
    *,
    store: Optional[MemoryStore] = None,
) -> Optional[Episode]:
    """
    Convenience wrapper: extract episode data from a `VerificationState` and index it.

    Only indexes when a retry occurred (retry_count > 0) and the final verdict
    is PASS — meaning the retry resolved an earlier failure.
    Returns `None` if the state does not represent a resolved episode.
    """
    from agenti_helix.verification.checkpointing import VerificationStatus

    retry_count = getattr(state, "retry_count", 0)
    if retry_count == 0:
        return None

    checkpoint = getattr(state, "checkpoint", None)
    if checkpoint is None:
        return None

    status = getattr(checkpoint, "status", None)
    if status is not VerificationStatus.PASSED:
        return None

    judge_response = getattr(state, "judge_response", None) or {}
    error_text = getattr(state, "feedback", "") or judge_response.get("justification", "")
    if not error_text:
        return None

    task = getattr(state, "task", None)
    task_id = getattr(task, "task_id", "") if task else ""
    target_file = getattr(task, "target_file", "") if task else ""
    dag_id = getattr(state, "dag_id", "") or ""

    diff_json = getattr(state, "diff_json", None) or {}
    resolution = f"Applied patch: {diff_json}" if diff_json else "Patch applied (details unavailable)"

    effective_store = store or get_default_store()
    return index_resolved_episode(
        effective_store,
        error_text=error_text,
        resolution=resolution,
        task_id=task_id,
        target_file=target_file,
        dag_id=dag_id,
    )
