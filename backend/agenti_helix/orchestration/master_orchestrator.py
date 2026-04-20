"""
Single entry point for resolving coder/judge chains for a task.

The verification loop calls ``resolve_coder_chain`` and ``resolve_judge_chain``
once per cycle; both delegate to ``runtime.run_plan`` so chain composition
lives in a single place.

Resolution priority:
  1. Explicit ``task.coder_chain`` / ``task.judge_chain`` overrides (API).
  2. ``RunPlan`` derived from ``task.pipeline_mode`` (legacy bridge for the
     existing dashboard ``mode`` + ``extras`` API surface).
"""
from __future__ import annotations

from typing import Any, Dict

from agenti_helix.runtime.run_plan import (
    build_coder_chain,
    build_judge_chain,
    plan_from_legacy_mode,
)
from agenti_helix.verification.checkpointing import EditTaskSpec


def resolve_coder_chain(task: EditTaskSpec) -> Dict[str, Any]:
    if task.coder_chain:
        return task.coder_chain
    plan = plan_from_legacy_mode(getattr(task, "pipeline_mode", "patch"))
    return build_coder_chain(task, plan)


def resolve_judge_chain(task: EditTaskSpec) -> Dict[str, Any]:
    if task.judge_chain:
        return task.judge_chain
    plan = plan_from_legacy_mode(getattr(task, "pipeline_mode", "patch"))
    return build_judge_chain(task, plan)
