from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Iterable, List, Literal, Optional


SupportedLanguage = Literal["javascript", "typescript", "python"]

# D4: Directories that should never be indexed — prevent tens-of-thousands of
# spurious entries from node_modules, build artifacts, and VCS internals.
IGNORE_DIRS: FrozenSet[str] = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        "dist",
        "build",
        "out",
        ".agenti_helix",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "coverage",
        ".next",
        ".nuxt",
    }
)


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


def _is_ignored(path: Path, root: Path, extra_ignore: FrozenSet[str]) -> bool:
    """Return True if any component of `path` is in the combined ignore set."""
    combined = IGNORE_DIRS | extra_ignore
    # Check each part of the path relative to root for ignored directory names.
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part in combined for part in rel.parts)


def scan_repository(
    root: str | Path,
    include_languages: Iterable[SupportedLanguage] | None = None,
    exclude_patterns: Optional[List[str]] = None,
) -> List[RepoFile]:
    """Walk the repository tree from `root` and return all supported source files.

    Args:
        root: Repository root directory.
        include_languages: If provided, only files in these languages are returned.
        exclude_patterns: Additional directory names to exclude on top of
            ``IGNORE_DIRS`` (e.g. ``["fixtures", "vendor"]``).
    """
    root_path = Path(root).resolve()
    if include_languages is not None:
        include_languages = set(include_languages)

    extra_ignore: FrozenSet[str] = frozenset(exclude_patterns or [])

    repo_files: List[RepoFile] = []
    for path in root_path.rglob("*"):
        if not path.is_file():
            continue

        # D4: Skip files inside ignored directories.
        if _is_ignored(path, root_path, extra_ignore):
            continue

        language = detect_language(path)
        if language is None:
            continue

        if include_languages is not None and language not in include_languages:
            continue

        repo_files.append(RepoFile(path=str(path.relative_to(root_path)), language=language))

    return repo_files

