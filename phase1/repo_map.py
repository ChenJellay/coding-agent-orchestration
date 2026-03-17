from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Literal, Optional

from .repo_scanner import RepoFile, SupportedLanguage, scan_repository
from .ast_parser import extract_symbols


SupportedLanguageStr = Literal["javascript", "typescript", "python"]


@dataclass
class RepoMapFile:
    path: str
    language: SupportedLanguageStr
    symbols: dict
    imports: Optional[list] = None


@dataclass
class RepoMap:
    files: List[RepoMapFile]

    def to_json(self) -> str:
        return json.dumps({"files": [asdict(f) for f in self.files]}, indent=2)


def generate_repo_map(
    root: str | Path,
    include_languages: Optional[List[SupportedLanguage]] = None,
) -> RepoMap:
    root_path = Path(root).resolve()
    repo_files: List[RepoFile] = scan_repository(root_path, include_languages=include_languages)

    map_files: List[RepoMapFile] = []
    for rf in repo_files:
        abs_path = root_path / rf.path
        info = extract_symbols(abs_path, rf.language)
        map_files.append(
            RepoMapFile(
                path=str(rf.path),
                language=rf.language,
                symbols=info["symbols"],
                imports=info.get("imports") or None,
            )
        )

    return RepoMap(files=map_files)


def save_repo_map(repo_map: RepoMap, out_path: str | Path) -> None:
    out = Path(out_path)
    out.write_text(repo_map.to_json(), encoding="utf8")

