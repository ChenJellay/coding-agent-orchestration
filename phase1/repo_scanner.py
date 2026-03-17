from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Literal


SupportedLanguage = Literal["javascript", "typescript", "python"]


@dataclass
class RepoFile:
    path: str
    language: SupportedLanguage


JS_EXTENSIONS = {".js", ".jsx"}
TS_EXTENSIONS = {".ts", ".tsx"}
PY_EXTENSIONS = {".py"}


def detect_language(path: Path) -> SupportedLanguage | None:
    suffix = path.suffix.lower()
    if suffix in JS_EXTENSIONS:
        return "javascript"
    if suffix in TS_EXTENSIONS:
        return "typescript"
    if suffix in PY_EXTENSIONS:
        return "python"
    return None


def scan_repository(
    root: str | Path,
    include_languages: Iterable[SupportedLanguage] | None = None,
) -> List[RepoFile]:
    """
    Walk the repository tree from `root` and return all supported source files.
    """
    root_path = Path(root).resolve()
    if include_languages is not None:
        include_languages = set(include_languages)

    repo_files: List[RepoFile] = []
    for path in root_path.rglob("*"):
        if not path.is_file():
            continue

        language = detect_language(path)
        if language is None:
            continue

        if include_languages is not None and language not in include_languages:
            continue

        repo_files.append(RepoFile(path=str(path.relative_to(root_path)), language=language))

    return repo_files

