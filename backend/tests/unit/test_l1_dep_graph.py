"""
L1.4 — Dependency graph and focused context tool tests.

Verifies that:
- build_dependency_graph resolves relative imports correctly.
- get_focused_files returns the target file and its import deps, and when depth>0
  also files that import those files (e.g. tests importing the module under edit).
- tool_get_focused_context returns the correct subset + full allowed_paths.
- Absolute / third-party imports are not resolved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenti_helix.core.repo_map import (
    RepoMap,
    RepoMapFile,
    build_dependency_graph,
    get_focused_files,
)


def _make_map(*files: RepoMapFile) -> RepoMap:
    return RepoMap(files=list(files))


def _f(path: str, imports: list | None = None) -> RepoMapFile:
    return RepoMapFile(path=path, language="javascript", symbols={}, imports=imports or [])


# ---------------------------------------------------------------------------
# build_dependency_graph
# ---------------------------------------------------------------------------

def test_no_imports_produces_empty_deps():
    rm = _make_map(_f("src/a.js"), _f("src/b.js"))
    graph = build_dependency_graph(rm)
    assert graph == {"src/a.js": [], "src/b.js": []}


def test_relative_import_resolved():
    rm = _make_map(_f("src/a.js", imports=["./b"]), _f("src/b.js"))
    graph = build_dependency_graph(rm)
    assert "src/b.js" in graph["src/a.js"]


def test_parent_dir_import_resolved():
    rm = _make_map(_f("src/components/button.js", imports=["../utils"]), _f("src/utils.js"))
    graph = build_dependency_graph(rm)
    assert "src/utils.js" in graph["src/components/button.js"]


def test_absolute_import_not_resolved():
    rm = _make_map(_f("src/a.js", imports=["react", "lodash"]), _f("src/b.js"))
    graph = build_dependency_graph(rm)
    assert graph["src/a.js"] == []


def test_self_import_not_included():
    """A file that appears to import itself (edge case) should not self-loop."""
    rm = _make_map(_f("src/a.js", imports=["./a"]))
    graph = build_dependency_graph(rm)
    assert "src/a.js" not in graph["src/a.js"]


# ---------------------------------------------------------------------------
# get_focused_files
# ---------------------------------------------------------------------------

def test_focused_files_returns_target():
    rm = _make_map(_f("src/a.js"), _f("src/b.js"))
    focused = get_focused_files(rm, ["src/a.js"], depth=0)
    assert any(f.path == "src/a.js" for f in focused)
    assert all(f.path != "src/b.js" for f in focused)


def test_focused_files_includes_1_hop_deps():
    rm = _make_map(_f("src/a.js", imports=["./b"]), _f("src/b.js"))
    focused = get_focused_files(rm, ["src/a.js"], depth=1)
    paths = {f.path for f in focused}
    assert "src/a.js" in paths
    assert "src/b.js" in paths


def test_focused_files_includes_importers_when_depth_positive():
    """Tests that import a module but are not imported from the app entry must appear in AST focus."""
    rm = _make_map(
        _f("src/index.js", imports=["./components/header"]),
        _f("src/components/header.js"),
        _f("src/components/header.test.js", imports=["./header"]),
    )
    focused = get_focused_files(rm, ["src/index.js"], depth=2)
    paths = {f.path for f in focused}
    assert "src/components/header.test.js" in paths
    assert "src/components/header.js" in paths


def test_focused_files_depth_zero_no_deps():
    rm = _make_map(_f("src/a.js", imports=["./b"]), _f("src/b.js"))
    focused = get_focused_files(rm, ["src/a.js"], depth=0)
    paths = {f.path for f in focused}
    assert "src/a.js" in paths
    assert "src/b.js" not in paths


def test_focused_files_depth_zero_excludes_reverse_importers():
    """Depth 0 stays entry-only even if another file imports the target."""
    rm = _make_map(_f("src/a.js"), _f("src/b.js", imports=["./a"]))
    focused = get_focused_files(rm, ["src/a.js"], depth=0)
    paths = {f.path for f in focused}
    assert paths == {"src/a.js"}


def test_focused_files_unknown_target_skipped():
    rm = _make_map(_f("src/a.js"))
    focused = get_focused_files(rm, ["not/a/real/file.js"], depth=1)
    assert focused == []


# ---------------------------------------------------------------------------
# tool_get_focused_context
# ---------------------------------------------------------------------------

def test_tool_get_focused_context_returns_repo_map_json(tmp_path):
    """tool_get_focused_context should return a JSON-serializable repo_map_json."""
    from unittest.mock import patch
    from agenti_helix.runtime.tools import tool_get_focused_context

    mock_map = _make_map(_f("src/header.js", imports=["./utils"]), _f("src/utils.js"))

    with patch("agenti_helix.runtime.tools.generate_repo_map", return_value=mock_map):
        result = tool_get_focused_context(
            repo_root=str(tmp_path),
            target_files=["src/header.js"],
            depth=1,
        )

    assert "repo_map_json" in result
    assert "allowed_paths" in result
    parsed = json.loads(result["repo_map_json"])
    focused_paths = {f["path"] for f in parsed}
    assert "src/header.js" in focused_paths
    assert "src/utils.js" in focused_paths
    # allowed_paths includes ALL repo files
    assert "src/header.js" in result["allowed_paths"]
    assert "src/utils.js" in result["allowed_paths"]
