from __future__ import annotations

from typing import Any, Dict

from agenti_helix.runtime.chain_defaults import (
    default_coder_chain,
    default_judge_chain,
    default_full_pipeline_coder_chain,
    default_full_pipeline_judge_chain,
    default_module_rewriter_chain,
)
from agenti_helix.verification.checkpointing import EditTaskSpec


def resolve_coder_chain(task: EditTaskSpec) -> Dict[str, Any]:
    """
    Choose the coder chain for a task.

    Priority:
      1. Explicit `task.coder_chain` (user override via API).
      2. `task.pipeline_mode == "build"` → full TDD pipeline.
      3. `task.pipeline_mode == "module"` → module-rewriter pipeline.
      4. Default → single-file patch chain.
    """
    if task.coder_chain:
        return task.coder_chain
    mode = getattr(task, "pipeline_mode", "patch")
    if mode == "build":
        return default_full_pipeline_coder_chain(task)
    if mode == "module":
        return default_module_rewriter_chain(task)
    return default_coder_chain(task)


def resolve_judge_chain(task: EditTaskSpec) -> Dict[str, Any]:
    """
    Choose the judge chain for a task.

    Priority:
      1. Explicit `task.judge_chain` (user override via API).
      2. `task.pipeline_mode == "build"` → full TDD judge pipeline.
      3. Default → snippet-comparison judge chain.
    """
    if task.judge_chain:
        return task.judge_chain
    if getattr(task, "pipeline_mode", "patch") == "build":
        return default_full_pipeline_judge_chain(task)
    return default_judge_chain(task)
