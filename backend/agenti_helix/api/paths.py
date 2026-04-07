from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional


@dataclass(frozen=True)
class HelixPaths:
    repo_root: Path

    @property
    def agenti_root(self) -> Path:
        return self.repo_root / ".agenti_helix"

    @property
    def dags_dir(self) -> Path:
        return self.agenti_root / "dags"

    @property
    def checkpoints_dir(self) -> Path:
        return self.agenti_root / "checkpoints"

    @property
    def logs_dir(self) -> Path:
        return self.agenti_root / "logs"

    @property
    def events_path(self) -> Path:
        return self.logs_dir / "events.jsonl"

    @property
    def rules_path(self) -> Path:
        return self.agenti_root / "rules.json"


def _repo_root_from_env() -> Path:
    # Keep consistent with existing `backend/agenti_helix/api/main.py` behavior.
    return Path(os.environ.get("AGENTI_HELIX_REPO_ROOT", Path(".").resolve())).resolve()


PATHS = HelixPaths(repo_root=_repo_root_from_env())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def try_read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    return read_json(path)


def iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return iter(())
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload

