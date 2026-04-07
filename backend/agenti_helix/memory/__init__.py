"""
Episodic memory package for Agenti-Helix.

Provides a lightweight, file-backed store for indexing resolved errors so
agents can retrieve relevant historical context before retrying a fix.

Usage::

    from agenti_helix.memory.store import MemoryStore
    from agenti_helix.memory.indexer import index_resolved_episode

    store = MemoryStore()
    index_resolved_episode(store, error_text="...", resolution="...", task_id="...")
    episodes = store.query("dependency conflict")
"""
from .store import Episode, MemoryStore
from .indexer import index_resolved_episode

__all__ = ["Episode", "MemoryStore", "index_resolved_episode"]
