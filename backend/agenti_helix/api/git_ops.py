"""§4.6 — Semantic Git Blame / Traceability.

Real git operations for the merge endpoint and blame lookups.
Gated by AGENTI_HELIX_GIT_COMMIT_ENABLED=true; when unset the commit is
simulated (dev-safe) and a warning is logged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _git_enabled() -> bool:
    return os.environ.get("AGENTI_HELIX_GIT_COMMIT_ENABLED", "").lower() in ("1", "true", "yes")


def _checkout_target_branch(repo: Any, branch_name: str) -> None:
    """Switch the working tree to ``branch_name``, creating it if it does not exist."""
    heads = {h.name for h in repo.heads}
    if branch_name in heads:
        repo.git.checkout(branch_name)
    else:
        repo.git.checkout("-b", branch_name)


def real_git_commit(
    *,
    repo_path: str,
    target_files: List[str],
    commit_message: str,
    trace_id: Optional[str] = None,
    dag_id: Optional[str] = None,
    intent_summary: Optional[str] = None,
    target_branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Stage `target_files` and create a git commit with embedded trace metadata.

    Returns ``{"ok": True, "sha": "<hex>", "simulated": False}`` on success or
    ``{"ok": True, "sha": None, "simulated": True}`` when git commits are
    disabled (``AGENTI_HELIX_GIT_COMMIT_ENABLED`` is not set).

    Raises on git errors so the caller can surface a meaningful HTTP error.
    """
    if not _git_enabled():
        return {"ok": True, "sha": None, "simulated": True}

    try:
        import git as gitpython  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("gitpython is not installed; add 'gitpython' to requirements.txt") from exc

    repo_root = Path(repo_path).resolve()
    try:
        repo = gitpython.Repo(repo_root, search_parent_directories=True)
    except gitpython.InvalidGitRepositoryError as exc:
        raise RuntimeError(f"Not a git repository: {repo_root}") from exc

    work_tree = Path(repo.working_tree_dir or str(repo_root)).resolve()

    branch = (target_branch or "").strip()
    if branch:
        _checkout_target_branch(repo, branch)

    if not target_files:
        raise ValueError("merge commit requires at least one target file path to stage")

    # Stage the target files (paths are relative to the git working tree).
    for rel_path in target_files:
        norm = rel_path.replace("\\", "/").lstrip("/")
        abs_path = work_tree / norm
        if not abs_path.exists():
            raise FileNotFoundError(f"Cannot stage non-existent file: {abs_path}")
        repo.index.add([str(abs_path)])

    # Enrich commit message with trace metadata in git-trailer format.
    trailers: List[str] = []
    if trace_id:
        trailers.append(f"Trace-Id: {trace_id}")
    if dag_id:
        trailers.append(f"Dag-Id: {dag_id}")
    if intent_summary:
        # Truncate long summaries to keep git log readable.
        summary = intent_summary[:200].replace("\n", " ")
        trailers.append(f"Intent: {summary}")

    full_message = commit_message
    if trailers:
        full_message = f"{commit_message}\n\n" + "\n".join(trailers)

    commit = repo.index.commit(full_message)
    return {"ok": True, "sha": commit.hexsha, "simulated": False}


def git_blame_line(
    *,
    repo_path: str,
    file_path: str,
    line: int,
) -> Dict[str, Any]:
    """Return the git blame entry for a specific line in a file.

    Returns a dict with ``commit_sha``, ``author``, ``date``, ``message``,
    and any embedded ``trace_id`` / ``dag_id`` extracted from git trailers.
    Returns ``{"found": False}`` when the line cannot be blamed (e.g., file
    not tracked, line out of range, git not enabled).
    """
    try:
        import git as gitpython  # type: ignore[import]
    except ImportError:
        return {"found": False, "error": "gitpython not installed"}

    repo_root = Path(repo_path).resolve()
    try:
        repo = gitpython.Repo(repo_root, search_parent_directories=True)
    except gitpython.InvalidGitRepositoryError:
        return {"found": False, "error": "not a git repository"}

    try:
        blame_entries = repo.blame("HEAD", file_path)
    except Exception as exc:
        return {"found": False, "error": str(exc)}

    # blame_entries is a list of (commit, [lines]) tuples.
    current_line = 1
    for commit_obj, lines in blame_entries:
        for _ in lines:
            if current_line == line:
                msg = commit_obj.message or ""
                return {
                    "found": True,
                    "commit_sha": commit_obj.hexsha,
                    "author": str(commit_obj.author),
                    "date": commit_obj.authored_datetime.isoformat(),
                    "message": msg.strip(),
                    "trace_id": _extract_trailer(msg, "Trace-Id"),
                    "dag_id": _extract_trailer(msg, "Dag-Id"),
                    "intent": _extract_trailer(msg, "Intent"),
                }
            current_line += 1

    return {"found": False, "error": f"Line {line} not found in blame output"}


def _extract_trailer(message: str, key: str) -> Optional[str]:
    """Extract a git-trailer value from a commit message, e.g. 'Trace-Id: abc'."""
    prefix = f"{key}: "
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return None
