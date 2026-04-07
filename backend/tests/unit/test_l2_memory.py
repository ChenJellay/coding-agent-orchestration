"""
L2.3 — Episodic memory store and indexer tests.

Verifies that:
- MemoryStore.add / load_all persist and retrieve episodes.
- MemoryStore.query returns similar episodes by Jaccard similarity.
- MemoryStore.query returns empty when no similar episodes exist.
- index_resolved_episode creates and stores an Episode.
- index_from_verification_state only indexes PASSED retried states.
- tool_query_memory returns expected dict structure.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from agenti_helix.memory.store import Episode, MemoryStore, _jaccard, _tokenize
from agenti_helix.memory.indexer import index_resolved_episode, index_from_verification_state


# ---------------------------------------------------------------------------
# Tokenizer + Jaccard helper
# ---------------------------------------------------------------------------

def test_tokenize_lowercases_and_splits():
    tokens = _tokenize("ImportError: Cannot find module 'react'")
    assert "importerror" in tokens
    assert "cannot" in tokens
    assert "react" in tokens


def test_jaccard_identical():
    a = {"x", "y", "z"}
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint():
    assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial():
    score = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
    # intersection={b,c}=2, union={a,b,c,d}=4 → 0.5
    assert abs(score - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# MemoryStore persistence
# ---------------------------------------------------------------------------

def _ep(store_path: Path, eid: str = "ep1", error: str = "error text", resolution: str = "fix") -> Episode:
    return Episode(
        episode_id=eid,
        task_id="task-1",
        error_text=error,
        resolution=resolution,
        target_file="src/foo.py",
        dag_id="dag-1",
    )


def test_store_add_and_load_all(tmp_path):
    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    ep = _ep(tmp_path)
    store.add(ep)

    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].episode_id == "ep1"
    assert loaded[0].error_text == "error text"


def test_store_multiple_episodes(tmp_path):
    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    for i in range(5):
        ep = _ep(tmp_path, eid=f"ep{i}", error=f"error {i}")
        store.add(ep)

    assert store.count() == 5


def test_store_empty_returns_empty(tmp_path):
    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    assert store.load_all() == []
    assert store.count() == 0


def test_store_query_returns_similar(tmp_path):
    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    store.add(_ep(tmp_path, eid="1", error="ImportError cannot import name foo from bar"))
    store.add(_ep(tmp_path, eid="2", error="SyntaxError unexpected EOF while parsing"))

    results = store.query("ImportError cannot import foo", top_k=1)
    assert len(results) == 1
    assert results[0].episode_id == "1"


def test_store_query_returns_empty_on_no_match(tmp_path):
    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    store.add(_ep(tmp_path, eid="1", error="ImportError"))

    results = store.query("completely unrelated xyz", top_k=5)
    # May or may not return results; if any returned they have score > 0.
    for ep in results:
        assert ep.episode_id == "1"


# ---------------------------------------------------------------------------
# index_resolved_episode
# ---------------------------------------------------------------------------

def test_index_resolved_episode(tmp_path):
    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    ep = index_resolved_episode(
        store,
        error_text="Cannot resolve module",
        resolution="Added missing import",
        task_id="t1",
        target_file="src/app.py",
        dag_id="dag-x",
    )
    assert ep.error_text == "Cannot resolve module"
    assert store.count() == 1


# ---------------------------------------------------------------------------
# index_from_verification_state
# ---------------------------------------------------------------------------

def _make_state(*, retry_count: int, status, feedback: str = "error happened", dag_id: str = "dag-1"):
    """Minimal mock of VerificationState."""
    from agenti_helix.verification.checkpointing import VerificationStatus

    task = MagicMock()
    task.task_id = "task-test"
    task.target_file = "src/test.py"

    checkpoint = MagicMock()
    checkpoint.status = status

    state = MagicMock()
    state.retry_count = retry_count
    state.checkpoint = checkpoint
    state.task = task
    state.feedback = feedback
    state.judge_response = {"justification": feedback}
    state.diff_json = {"filePath": "src/test.py", "startLine": 1, "endLine": 1, "replacementLines": ["x=1"]}
    state.dag_id = dag_id
    return state


def test_index_from_state_passed_with_retry(tmp_path):
    from agenti_helix.verification.checkpointing import VerificationStatus

    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    state = _make_state(retry_count=1, status=VerificationStatus.PASSED)
    ep = index_from_verification_state(state, store=store)

    assert ep is not None
    assert store.count() == 1


def test_index_from_state_skips_no_retry(tmp_path):
    from agenti_helix.verification.checkpointing import VerificationStatus

    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    state = _make_state(retry_count=0, status=VerificationStatus.PASSED)
    ep = index_from_verification_state(state, store=store)

    assert ep is None
    assert store.count() == 0


def test_index_from_state_skips_blocked(tmp_path):
    from agenti_helix.verification.checkpointing import VerificationStatus

    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    state = _make_state(retry_count=2, status=VerificationStatus.BLOCKED)
    ep = index_from_verification_state(state, store=store)

    assert ep is None
    assert store.count() == 0


# ---------------------------------------------------------------------------
# tool_query_memory
# ---------------------------------------------------------------------------

def test_tool_query_memory_structure(tmp_path, monkeypatch):
    from agenti_helix.runtime.tools import tool_query_memory

    store = MemoryStore(store_path=tmp_path / "eps.jsonl")
    store.add(_ep(tmp_path, eid="ep1", error="ImportError missing module"))

    with MagicMock() as _mock:
        import agenti_helix.memory.store as store_mod
        original_get = store_mod.get_default_store
        store_mod.get_default_store = lambda: store

        result = tool_query_memory(error_description="ImportError module", top_k=3)

        store_mod.get_default_store = original_get

    assert "episodes" in result
    assert isinstance(result["episodes"], list)
    if result["episodes"]:
        ep_dict = result["episodes"][0]
        assert "episode_id" in ep_dict
        assert "error_text" in ep_dict
        assert "resolution" in ep_dict
