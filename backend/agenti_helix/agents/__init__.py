"""
Centralized definitions for LLM-backed agents used by Agenti-Helix.

Agents are defined by:
- a prompt template stored under `prompts/`
- typed input/output models
- a registry entry addressable by agent id
"""

from .registry import get_agent, list_agents  # noqa: F401

