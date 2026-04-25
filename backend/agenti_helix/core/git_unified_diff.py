"""Collect unified git diffs for agent verification (tracked + untracked paths).

``git diff HEAD`` omits untracked files entirely. Callers that gate on a diff
(e.g. ``diff_validator_v1``) must use path-scoped collection so new files still
appear as ``git diff --no-index`` hunks.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_GIT_UNIFIED_DIFF_CHARS = 512_000


def collect_diff_paths(
    target_file: str,
    diff_json: Optional[Dict[str, Any]],
) -> List[str]:
    """Repo-relative paths to include (target + coder outputs from ``diff_json``)."""
    paths: List[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        raw = raw.strip().replace("\\", "/")
        if not raw or raw in seen or ".." in raw.split("/"):
            return
        seen.add(raw)
        paths.append(raw)

    if target_file:
        add(str(target_file))
    if not isinstance(diff_json, dict):
        return paths
    fp = diff_json.get("filePath")
    if isinstance(fp, str):
        add(fp)
    for key in ("files_written", "test_file_paths"):
        lst = diff_json.get(key)
        if isinstance(lst, list):
            for item in lst:
                if isinstance(item, str):
                    add(item)
    return paths


def build_git_unified_diff(repo_root: Path | str, paths: List[str]) -> str:
    """Unified diff: working tree vs ``HEAD`` for tracked files, vs ``/dev/null`` for untracked."""
    if not paths:
        return ""
    root = Path(repo_root).resolve()
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode != 0 or (probe.stdout or "").strip().lower() != "true":
            return ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""

    chunks: List[str] = []
    null_device = os.devnull
    for rel in paths:
        rel = rel.strip().replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            continue
        path = root / rel
        if not path.is_file():
            continue
        try:
            ls = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        tracked = ls.returncode == 0
        try:
            if tracked:
                diff = subprocess.run(
                    ["git", "-C", str(root), "diff", "--no-color", "HEAD", "--", rel],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            else:
                diff = subprocess.run(
                    ["git", "diff", "--no-color", "--no-index", null_device, rel],
                    capture_output=True,
                    text=True,
                    cwd=str(root),
                    timeout=120,
                )
            if diff.stdout:
                chunks.append(diff.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    out = "".join(chunks)
    if len(out) > MAX_GIT_UNIFIED_DIFF_CHARS:
        return out[:MAX_GIT_UNIFIED_DIFF_CHARS] + "\n... [truncated by agenti_helix: git unified diff cap]\n"
    return out
