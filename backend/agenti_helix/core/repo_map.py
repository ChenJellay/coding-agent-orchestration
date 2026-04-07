from __future__ import annotations

import json
import posixpath
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set

from .ast_parser import extract_symbols
from .repo_scanner import RepoFile, SupportedLanguage, scan_repository


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


def _resolve_import_to_path(
    import_str: str,
    source_file: str,
    all_paths: Set[str],
) -> Optional[str]:
    """
    Attempt to resolve a relative import string to a known repo-relative file path.

    Handles:
    - JS/TS relative imports: ./foo, ../bar/baz, ./utils/index
    - Python relative imports: .module, ..module (treated as ./module and ../module)

    Absolute / third-party imports (no leading dot) are not resolved.
    """
    if not import_str:
        return None

    # Only process relative imports (start with ".")
    if not import_str.startswith("."):
        return None

    # Normalise Python dotted relative imports that lack a slash separator:
    #   ".module"  → "./module"
    #   "..module" → "../module"
    # Imports already using "./" or "../" are left unchanged.
    normalised = import_str
    if not import_str.startswith("./") and not import_str.startswith("../"):
        if import_str.startswith(".."):
            normalised = "../" + import_str[2:]
        else:
            normalised = "./" + import_str[1:]

    source_dir = posixpath.dirname(source_file) or "."
    # Use posixpath.normpath to resolve ".." components correctly
    candidate_base = posixpath.normpath(posixpath.join(source_dir, normalised))

    # Try with and without common extensions
    for ext in ("", ".js", ".jsx", ".ts", ".tsx", ".py", "/index.js", "/index.ts"):
        candidate = candidate_base + ext
        if candidate in all_paths:
            return candidate

    return None


def build_dependency_graph(repo_map: RepoMap) -> Dict[str, List[str]]:
    """
    Build a file → [files it directly imports] adjacency map.

    Only resolves *relative* imports; absolute / third-party imports are skipped
    since they cannot be resolved without a full package graph.

    Returns a dict keyed by repo-relative file path whose values are sorted
    lists of repo-relative paths that the file imports.
    """
    all_paths: Set[str] = {f.path for f in repo_map.files}
    graph: Dict[str, List[str]] = {f.path: [] for f in repo_map.files}

    for rm_file in repo_map.files:
        if not rm_file.imports:
            continue
        deps: List[str] = []
        for imp in rm_file.imports:
            if not isinstance(imp, str):
                continue
            resolved = _resolve_import_to_path(imp, rm_file.path, all_paths)
            if resolved and resolved != rm_file.path and resolved not in deps:
                deps.append(resolved)
        graph[rm_file.path] = sorted(deps)

    return graph


def get_focused_files(
    repo_map: RepoMap,
    target_files: List[str],
    depth: int = 1,
) -> List[RepoMapFile]:
    """
    Return `RepoMapFile` entries for `target_files` plus their import
    dependencies up to `depth` hops.  Files not found in the map are skipped.
    """
    dep_graph = build_dependency_graph(repo_map)
    file_index: Dict[str, RepoMapFile] = {f.path: f for f in repo_map.files}

    visited: Set[str] = set()
    frontier: Set[str] = set(target_files)

    for _ in range(depth):
        next_frontier: Set[str] = set()
        for path in frontier:
            if path in visited:
                continue
            visited.add(path)
            for dep in dep_graph.get(path, []):
                if dep not in visited:
                    next_frontier.add(dep)
        frontier = next_frontier

    visited.update(frontier)

    result: List[RepoMapFile] = []
    for path in sorted(visited):
        if path in file_index:
            result.append(file_index[path])
    return result


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

