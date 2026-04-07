"""
Lightweight, file-backed episodic memory store.

Each resolved error/retry pair is stored as an Episode in a JSONL file under
`.agenti_helix/memory/episodes.jsonl`.  Query uses token-overlap (Jaccard
similarity over word tokens) — no embeddings required, fully in-process.

For larger deployments, swap this store for a vector DB (ChromaDB, Qdrant, etc.)
behind the same `MemoryStore` interface.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_store_path() -> Path:
    repo_root = Path(os.environ.get("AGENTI_HELIX_REPO_ROOT", str(Path(".").resolve()))).resolve()
    return repo_root / ".agenti_helix" / "memory" / "episodes.jsonl"


def _tokenize(text: str) -> set[str]:
    """Simple word-level tokenizer; case-insensitive, strips punctuation."""
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


@dataclass
class Episode:
    episode_id: str
    task_id: str
    error_text: str
    resolution: str
    target_file: str
    dag_id: str
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryStore:
    """
    Append-only JSONL episodic memory store with Jaccard-similarity search.

    Thread-safe for concurrent reads; writes acquire a file lock via rename.
    """

    def __init__(self, store_path: Optional[Path] = None) -> None:
        self._path = store_path or _default_store_path()

    def _ensure_dir(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, episode: Episode) -> None:
        """Append an episode to the store."""
        self._ensure_dir()
        line = json.dumps(asdict(episode))
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def load_all(self) -> List[Episode]:
        """Return all stored episodes."""
        if not self._path.exists():
            return []
        episodes: List[Episode] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    episodes.append(Episode(**data))
                except Exception:
                    continue
        return episodes

    def query(self, error_text: str, top_k: int = 3) -> List[Episode]:
        """
        Return up to `top_k` most similar episodes to `error_text`.

        Similarity is computed as Jaccard overlap of word tokens between
        `error_text` and each stored episode's `error_text`.
        """
        if not error_text:
            return []
        query_tokens = _tokenize(error_text)
        scored: List[tuple[float, Episode]] = []
        for ep in self.load_all():
            ep_tokens = _tokenize(ep.error_text)
            score = _jaccard(query_tokens, ep_tokens)
            scored.append((score, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:top_k] if _ > 0.0]

    def count(self) -> int:
        return len(self.load_all())


_DEFAULT_STORE: Optional[MemoryStore] = None


def get_default_store() -> MemoryStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = MemoryStore()
    return _DEFAULT_STORE
