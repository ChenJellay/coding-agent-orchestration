from __future__ import annotations

from typing import Any, Dict

from agenti_helix.runtime.chain_defaults import (
    default_coder_chain,
    default_full_pipeline_coder_chain,
    default_full_pipeline_judge_chain,
    default_judge_chain,
)
from agenti_helix.runtime.pipeline_presets import resolve_preset_chains
from agenti_helix.verification.checkpointing import EditTaskSpec


def resolve_coder_chain(task: EditTaskSpec) -> Dict[str, Any]:
    """
    Choose the coder chain for a task.

    Priority:
      1. Explicit `task.coder_chain` (user override via API).
      2. Named pipeline preset (e.g. product_eng, lint_type_gate) when `pipeline_mode` matches.
      3. `task.pipeline_mode == "build"` → full TDD pipeline.
      4. Default → single-file patch chain.
    """
    if task.coder_chain:
        return task.coder_chain
    preset = resolve_preset_chains(task)
    if preset is not None:
        return preset[0]
    if getattr(task, "pipeline_mode", "patch") == "build":
        return default_full_pipeline_coder_chain(task)
    return default_coder_chain(task)


def resolve_judge_chain(task: EditTaskSpec) -> Dict[str, Any]:
    """
    Choose the judge chain for a task.

    Priority:
      1. Explicit `task.judge_chain` (user override via API).
      2. Named pipeline preset when `pipeline_mode` matches.
      3. `task.pipeline_mode == "build"` → full TDD judge pipeline.
      4. Default → snippet-comparison judge chain.
    """
    if task.judge_chain:
        return task.judge_chain
    preset = resolve_preset_chains(task)
    if preset is not None:
        return preset[1]
    if getattr(task, "pipeline_mode", "patch") == "build":
        return default_full_pipeline_judge_chain(task)
    return default_judge_chain(task)
