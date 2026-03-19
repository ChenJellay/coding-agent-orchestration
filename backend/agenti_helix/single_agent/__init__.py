"""
Single-agent execution primitive.

This package hosts the end-to-end “single file edit” harness:
- builds a repo map from `agenti_helix.core`
- prompts a local model to emit a constrained JSON patch
- validates and applies the patch
- performs lightweight syntax checks

Higher layers (verification/orchestration) treat this as a black box.
"""

from .harness import run_single_agent_edit  # noqa: F401

