from __future__ import annotations

from typing import Any, Callable, Dict, FrozenSet, Optional, Tuple

from agenti_helix.runtime.chain_defaults import (
    default_coder_chain,
    default_full_pipeline_coder_chain,
    default_full_pipeline_judge_chain,
    pipeline_judge_diff_guard_patch,
    pipeline_judge_full_tdd_with_diff_gate,
    pipeline_judge_lint_type_gate,
    pipeline_product_eng_coder_chain,
)
from agenti_helix.verification.checkpointing import EditTaskSpec

PipelineChainBuilder = Callable[[EditTaskSpec | None], Dict[str, Any]]

# Dashboard / API pipeline_mode strings (keep in sync with frontend `PipelineMode`).
PIPELINE_MODES: FrozenSet[str] = frozenset(
    {
        "patch",
        "build",
        "product_eng",
        "diff_guard_patch",
        "secure_build_plus",
        "lint_type_gate",
    }
)

_PRESET_CHAINS: Dict[str, Tuple[PipelineChainBuilder, PipelineChainBuilder]] = {
    "product_eng": (pipeline_product_eng_coder_chain, pipeline_judge_full_tdd_with_diff_gate),
    "diff_guard_patch": (default_coder_chain, pipeline_judge_diff_guard_patch),
    "secure_build_plus": (default_full_pipeline_coder_chain, pipeline_judge_full_tdd_with_diff_gate),
    "lint_type_gate": (default_full_pipeline_coder_chain, pipeline_judge_lint_type_gate),
}


def resolve_preset_chains(task: EditTaskSpec) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    When `task.pipeline_mode` matches a named preset, return (coder_chain, judge_chain).
    Returns None to fall back to legacy patch/build resolution in master_orchestrator.
    """
    mode = (getattr(task, "pipeline_mode", None) or "patch").strip()
    builders = _PRESET_CHAINS.get(mode)
    if not builders:
        return None
    c_fn, j_fn = builders
    coder_chain = c_fn(task)
    # Doc was merged into macro intent pre-compile; skip redundant per-node doc_fetcher prefix.
    if mode == "product_eng" and getattr(task, "skip_doc_chain_prefix", False):
        coder_chain = default_full_pipeline_coder_chain(task)
    return (coder_chain, j_fn(task))


def preset_fallback_build_chains(task: EditTaskSpec) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Resolve coder/judge chains for `build` mode without duplicating defaults here.
    Used only for validation helpers.
    """
    return (default_full_pipeline_coder_chain(task), default_full_pipeline_judge_chain(task))
