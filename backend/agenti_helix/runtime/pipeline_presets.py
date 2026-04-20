"""
Backwards-compat shim around ``runtime.run_plan``.

The old API (``PIPELINE_MODES``, ``resolve_preset_chains``,
``preset_fallback_build_chains``) is still consumed by tests and the
``task_commands_routes`` validation path. New code should call
``runtime.run_plan.build_coder_chain`` / ``build_judge_chain`` directly.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Tuple

from agenti_helix.runtime.chain_defaults import (
    default_full_pipeline_coder_chain,
    default_full_pipeline_judge_chain,
)
from agenti_helix.runtime.run_plan import (
    _LEGACY_MODE_TO_PLAN,
    build_coder_chain,
    build_judge_chain,
    plan_from_legacy_mode,
)
from agenti_helix.verification.checkpointing import EditTaskSpec

# Dashboard / API pipeline_mode strings (kept in sync with frontend types).
PIPELINE_MODES: FrozenSet[str] = frozenset(_LEGACY_MODE_TO_PLAN.keys())


def resolve_preset_chains(task: EditTaskSpec) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Return (coder_chain, judge_chain) for a named legacy preset, or None for
    ``patch`` / ``build`` (which the master orchestrator handles directly).
    """
    mode = (getattr(task, "pipeline_mode", None) or "patch").strip().lower()
    if mode in {"patch", "build"}:
        return None
    plan = plan_from_legacy_mode(mode)
    return build_coder_chain(task, plan), build_judge_chain(task, plan)


def preset_fallback_build_chains(task: EditTaskSpec) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Resolve coder/judge chains for legacy ``build`` mode (validation helpers)."""
    return default_full_pipeline_coder_chain(task), default_full_pipeline_judge_chain(task)
