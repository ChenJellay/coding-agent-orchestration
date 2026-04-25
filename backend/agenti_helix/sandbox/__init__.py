"""Ephemeral workspace isolation (Docker path) — see ``manager`` module."""

from agenti_helix.sandbox.manager import SandboxManager, log_sandbox_status_for_task

__all__ = ["SandboxManager", "log_sandbox_status_for_task"]
