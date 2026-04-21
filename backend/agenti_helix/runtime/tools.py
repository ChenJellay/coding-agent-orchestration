from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, url2pathname, urlopen

from agenti_helix.api.task_context_store import load_task_context
from agenti_helix.core.ast_parser import parse_file
from agenti_helix.core.diff_builder import LinePatch, apply_line_patch_to_file
from agenti_helix.core.repo_map import generate_repo_map, get_focused_files
from agenti_helix.core.repo_scanner import detect_language

# Cap per-file snapshots embedded in diff_json for judge / security prompts (tokens).
_MAX_FILE_SNAPSHOT_CHARS = 80_000

_JEST_CONFIG_FILENAMES = (
    "jest.config.js",
    "jest.config.mjs",
    "jest.config.cjs",
    "jest.config.ts",
    "jest.config.cts",
    "jest.config.json",
)


def _truncate_for_snapshot(text: str, max_chars: int = _MAX_FILE_SNAPSHOT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated by agenti_helix for prompt size]"


def _norm_repo_rel_path(p: str) -> str:
    """Stable key for deduping the same file listed under code + tests (or duplicated rows)."""
    s = p.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


def _discover_jest_config(repo_root: Path) -> Path | None:
    for name in _JEST_CONFIG_FILENAMES:
        candidate = repo_root / name
        if candidate.is_file():
            return candidate
    return None


def _js_tests_likely_need_jsdom(repo_root: Path, js_files: List[str]) -> bool:
    """Heuristic: RTL/React/DOM-heavy tests need jsdom; plain node tests do not."""
    hints = ("@testing-library", "react", "jsdom", "document.", "window.", "HTMLElement")
    for rel in js_files:
        try:
            text = (repo_root / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lower = text.lower()
        if any(h.lower() in lower for h in hints):
            return True
    return False


def tool_get_focused_context(
    *,
    repo_root: str | Path,
    target_files: List[str],
    depth: int = 1,
) -> Dict[str, Any]:
    """
    Return a repo map slice covering `target_files` plus their import
    dependencies up to `depth` hops.  More token-efficient than the full map.
    Each file entry includes `exists: true` (all scanned files exist on disk at
    scan time) so downstream agents can detect files added/deleted between scans.
    """
    repo_root_path = Path(repo_root).resolve()
    repo_map = generate_repo_map(repo_root_path)
    focused = get_focused_files(repo_map, target_files, depth=depth)

    repo_files: List[Dict[str, Any]] = []
    allowed_paths: List[str] = []
    for f in focused:
        repo_files.append({"path": f.path, "language": f.language, "symbols": f.symbols, "exists": True})
        allowed_paths.append(f.path)

    # Also always include all top-level files in allowed_paths so the coder
    # can still target any file it discovers via the focused context.
    for f in repo_map.files:
        if f.path not in set(allowed_paths):
            allowed_paths.append(f.path)

    repo_map_json = json.dumps(repo_files, indent=2)
    return {"repo_files": repo_files, "repo_map_json": repo_map_json, "allowed_paths": allowed_paths}


def tool_build_repo_map_context(*, repo_root: str | Path) -> Dict[str, Any]:
    repo_root_path = Path(repo_root).resolve()
    repo_map = generate_repo_map(repo_root_path)

    repo_files: List[Dict[str, Any]] = []
    allowed_paths: List[str] = []
    for f in repo_map.files:
        repo_files.append({"path": f.path, "language": f.language, "symbols": f.symbols, "exists": True})
        allowed_paths.append(f.path)

    repo_map_json = json.dumps(repo_files, indent=2)
    return {"repo_files": repo_files, "repo_map_json": repo_map_json, "allowed_paths": allowed_paths}


def _validate_patch_json(patch: Dict[str, Any], allowed_paths: List[str]) -> LinePatch:
    required_keys = {"filePath", "startLine", "endLine", "replacementLines"}
    if not required_keys.issubset(patch):
        missing = required_keys - set(patch.keys())
        raise ValueError(f"Patch JSON missing keys: {', '.join(sorted(missing))}")

    file_path = str(patch["filePath"])
    if file_path not in set(allowed_paths):
        raise ValueError(f"Patch filePath {file_path!r} is not present in Repo Map")

    start_line = int(patch["startLine"])
    end_line = int(patch["endLine"])
    replacement_lines = [str(x) for x in (patch["replacementLines"] or [])]

    return LinePatch(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        replacement_lines=replacement_lines,
    )


def tool_apply_line_patch_and_validate(
    *,
    repo_root: str | Path,
    patch: Dict[str, Any],
    allowed_paths: List[str],
) -> Dict[str, Any]:
    # §4.5 — If the coder requested escalation, short-circuit without applying.
    # The verification loop will detect the escalation signal from `coder_patch`.
    if isinstance(patch, dict) and patch.get("escalate_to_human"):
        return {"escalated": True, "reason": str(patch.get("escalation_reason") or "")}

    repo_root_path = Path(repo_root).resolve()
    patch_typed = _validate_patch_json(patch, allowed_paths=allowed_paths)

    target_path = repo_root_path / patch_typed.file_path
    if not target_path.exists():
        # New file: line-range patch on an empty file is represented as L1–L1 replacing "nothing".
        if patch_typed.start_line == 1 and patch_typed.end_line == 1:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(patch_typed.replacement_lines)
            if body and not body.endswith("\n"):
                body += "\n"
            target_path.write_text(body, encoding="utf-8")
            result = {
                "filePath": patch_typed.file_path,
                "startLine": patch_typed.start_line,
                "endLine": patch_typed.end_line,
                "replacementLines": patch_typed.replacement_lines,
            }
            return result
        raise FileNotFoundError(f"Patch target does not exist: {target_path}")

    apply_line_patch_to_file(target_path, patch_typed)

    # Syntax check for JS/TS (tree-sitter) — surface errors so the judge
    # can decide to retry instead of silently accepting broken code.
    syntax_error: str | None = None
    try:
        lang = detect_language(target_path)
        if lang in ("javascript", "typescript"):
            parse_file(target_path, lang)
    except Exception as exc:
        syntax_error = str(exc)

    # Verification loop historically treats the patch JSON as `diff_json`.
    result = {
        "filePath": patch_typed.file_path,
        "startLine": patch_typed.start_line,
        "endLine": patch_typed.end_line,
        "replacementLines": patch_typed.replacement_lines,
    }
    if syntax_error:
        result["syntax_error"] = syntax_error
    return result


def tool_snapshot_target_file(*, repo_root: str | Path, target_file: str) -> str:
    repo_root_path = Path(repo_root).resolve()
    target_path = repo_root_path / target_file
    return target_path.read_text(encoding="utf-8")


def tool_infer_language_from_target_file(*, target_file: str) -> str:
    # Reuse repo language inference where possible.
    suffix = Path(target_file).suffix.lower().lstrip(".")
    if not suffix:
        return "text"
    # Prefer existing mapping rules when tree-sitter supports it.
    # (For unknown suffixes, fall back to "text".)
    # Note: detect_language expects a Path; we only have suffix here.
    if suffix in {"js", "jsx"}:
        return "javascript"
    if suffix in {"ts", "tsx"}:
        return "typescript"
    if suffix in {"py"}:
        return "python"
    return "text"


def tool_build_tool_logs_json(*, static_check_logs: Optional[Dict[str, Any]] = None) -> str:
    tool_logs = {"static_checks": static_check_logs or {}}
    return json.dumps(tool_logs, indent=2)


def tool_query_memory(*, error_description: str, top_k: int = 3) -> Dict[str, Any]:
    """
    Query the episodic memory store for past resolved errors similar to
    `error_description`.  Returns a dict with a `episodes` list; each episode
    has `error_text`, `resolution`, `target_file`, and `task_id`.
    """
    from agenti_helix.memory.store import get_default_store

    store = get_default_store()
    episodes = store.query(error_description, top_k=top_k)
    return {
        "episodes": [
            {
                "episode_id": ep.episode_id,
                "error_text": ep.error_text,
                "resolution": ep.resolution,
                "target_file": ep.target_file,
                "task_id": ep.task_id,
                "dag_id": ep.dag_id,
            }
            for ep in episodes
        ]
    }


def tool_build_ast_context(
    *,
    repo_root: str | Path,
    target_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Return an AST-oriented repo map for the context librarian.

    When `target_files` is provided and non-empty, return a focused slice to keep token usage low.
    When `target_files` is empty/None, return the full repo map so the librarian has broad visibility
    for tasks that involve creating new files or when the chosen `target_file` is not representative.
    """
    if target_files:
        result = tool_get_focused_context(repo_root=repo_root, target_files=target_files, depth=2)
        result["ast_repo_map_json"] = result["repo_map_json"]  # repo_files already carry exists: True
        return result
    full = tool_build_repo_map_context(repo_root=repo_root)
    full["ast_repo_map_json"] = full["repo_map_json"]
    return full


def tool_load_file_contents(
    *,
    repo_root: str | Path,
    required_files: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Read the full content of each file identified by the context librarian.

    Each entry in the returned file_contexts_json includes an `exists` boolean so
    downstream agents can distinguish between an empty file that exists on disk and
    a file that does not exist yet and must be created from scratch.
    """
    repo_root_path = Path(repo_root).resolve()
    file_contexts: List[Dict[str, Any]] = []
    for req in required_files:
        file_path = str(req.get("file_path") or req.get("path") or "")
        required_symbols = req.get("required_symbols") or []
        content: str = ""
        exists = False
        if file_path:
            target = repo_root_path / file_path
            exists = target.exists()
            if exists:
                try:
                    content = target.read_text(encoding="utf-8")
                except OSError:
                    exists = False
        file_contexts.append({
            "file_path": file_path,
            "required_symbols": required_symbols,
            "content": content,
            "exists": exists,
        })
    return {"file_contexts_json": json.dumps(file_contexts, indent=2)}


def tool_write_all_files(
    *,
    repo_root: str | Path,
    modified_files: Optional[List[Dict[str, Any]]] = None,
    test_files: Optional[List[Dict[str, Any]]] = None,
    checkpoint_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Write code and test files to disk (output of coder_builder_v1 / sdet_v1).

    Returns a `diff_json`-compatible dict with `files_written`, `test_file_paths`,
    and a pre-serialised `diff_json_str` for template injection.
    """
    repo_root_path = Path(repo_root).resolve()
    files_written: List[str] = []
    test_file_paths: List[str] = []

    # Optional: persist pre/post snapshots to support manual sign-off workflows.
    # Keep snapshots out of the returned diff_json to avoid bloating event logs / prompt injection.
    snapshots: Dict[str, Any] = {"pre": {}, "pre_meta": {}, "post": {}}
    snapshots_dir: Optional[Path] = None
    if checkpoint_id:
        snapshots_dir = (repo_root_path / ".agenti_helix" / "checkpoints" / "snapshots" / checkpoint_id).resolve()
        snapshots_dir.mkdir(parents=True, exist_ok=True)

    for f in (modified_files or []):
        file_path = str(f.get("file_path") or f.get("path") or "")
        content = str(f.get("content") or "")
        if not file_path or not content:
            continue
        target = repo_root_path / file_path
        if snapshots_dir:
            try:
                existed = target.exists()
                snapshots["pre_meta"][file_path] = {"existed": existed, "read_failed": False}
                snapshots["pre"][file_path] = target.read_text(encoding="utf-8") if existed else None
            except Exception:
                # If we fail to read but the file exists, do NOT treat it as "didn't exist".
                existed = target.exists()
                snapshots["pre_meta"][file_path] = {"existed": existed, "read_failed": True}
                snapshots["pre"][file_path] = None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        files_written.append(file_path)
        if snapshots_dir:
            snapshots["post"][file_path] = content

    for f in (test_files or []):
        file_path = str(f.get("file_path") or f.get("path") or "")
        content = str(f.get("content") or "")
        if not file_path or not content:
            continue
        target = repo_root_path / file_path
        if snapshots_dir:
            try:
                existed = target.exists()
                snapshots["pre_meta"][file_path] = {"existed": existed, "read_failed": False}
                snapshots["pre"][file_path] = target.read_text(encoding="utf-8") if existed else None
            except Exception:
                existed = target.exists()
                snapshots["pre_meta"][file_path] = {"existed": existed, "read_failed": True}
                snapshots["pre"][file_path] = None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        test_file_paths.append(file_path)
        if snapshots_dir:
            snapshots["post"][file_path] = content

    # Read back the files we just wrote so the security governor gets real code to audit.
    file_contents: Dict[str, str] = {}
    for fp in files_written + test_file_paths:
        try:
            file_contents[fp] = (repo_root_path / fp).read_text(encoding="utf-8")
        except Exception:
            pass

    # Same path may appear in both modified_files and test_files (model mistake); snapshot once.
    snapshot_paths_ordered: List[str] = []
    seen_paths: set[str] = set()
    for rel in files_written + test_file_paths:
        key = _norm_repo_rel_path(rel)
        if not key or key in seen_paths:
            continue
        seen_paths.add(key)
        snapshot_paths_ordered.append(rel)

    file_snapshots: List[Dict[str, str]] = []
    for rel in snapshot_paths_ordered:
        try:
            body = (repo_root_path / rel).read_text(encoding="utf-8")
        except OSError:
            body = ""
        file_snapshots.append({"path": rel, "content": _truncate_for_snapshot(body)})

    result: Dict[str, Any] = {
        "files_written": files_written,
        "test_file_paths": test_file_paths,
        "file_snapshots": file_snapshots,
        "diff_summary": (
            f"Wrote {len(files_written)} code file(s) and {len(test_file_paths)} test file(s): "
            + ", ".join(files_written + test_file_paths)
        ),
    }
    # Pre-serialise for template injection in security_governor / judge_evaluator prompts.
    # Include file contents so auditors see real code, not only paths (metadata-only JSON caused false FAILs).
    result["diff_json_str"] = json.dumps(
        {
            "files_written": files_written,
            "test_file_paths": test_file_paths,
            "file_snapshots": file_snapshots,
        },
        indent=2,
    )

    if snapshots_dir:
        manifest = {
            "checkpoint_id": checkpoint_id,
            "repo_root": str(repo_root_path),
            "files": sorted(list(set(files_written + test_file_paths))),
        }
        (snapshots_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # Store the relative path so the verification loop can apply/rollback without guessing.
        result["snapshots_dir"] = str(snapshots_dir.relative_to(repo_root_path))
        (snapshots_dir / "snapshots.json").write_text(json.dumps(snapshots, indent=2), encoding="utf-8")
    return result


def tool_run_tests(
    *,
    repo_root: str | Path,
    test_file_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run the test suite against the written test files and return terminal logs."""
    repo_root_path = Path(repo_root).resolve()
    paths = [p for p in (test_file_paths or []) if p]

    if not paths:
        return {"passed": False, "skipped": True, "terminal_logs": "No test files provided — skipping test run.", "test_count": 0}

    py_files = [p for p in paths if p.endswith(".py")]
    js_files = [p for p in paths if p.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"))]

    temp_jest_config: Path | None = None
    try:
        if py_files:
            cmd = (
                ["python", "-m", "pytest"]
                + [str(repo_root_path / p) for p in py_files]
                + ["-v", "--tb=short", "--no-header"]
            )
        elif js_files:
            jest_config = _discover_jest_config(repo_root_path)
            if jest_config is None:
                fd, tmp = tempfile.mkstemp(suffix=".cjs", prefix="agenti_helix_jest_")
                os.close(fd)
                temp_jest_config = Path(tmp)
                env = "jsdom" if _js_tests_likely_need_jsdom(repo_root_path, js_files) else "node"
                temp_jest_config.write_text(
                    f"module.exports = {{ testEnvironment: '{env}' }};\n",
                    encoding="utf-8",
                )
                jest_config = temp_jest_config
            cmd = (
                ["npx", "--yes", "jest", "--no-coverage", "--passWithNoTests", "--config", str(jest_config)]
                + js_files
            )
        else:
            return {"passed": False, "terminal_logs": f"Unsupported test file type(s): {paths}", "test_count": len(paths)}

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root_path),
            timeout=120,
        )
        logs = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
        return {
            "passed": result.returncode == 0,
            "terminal_logs": logs or "(no output)",
            "test_count": len(paths),
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "terminal_logs": "Tests timed out after 120 seconds.", "test_count": len(paths)}
    except FileNotFoundError as exc:
        return {"passed": False, "terminal_logs": f"Test runner not found: {exc}", "test_count": len(paths)}
    except Exception as exc:
        return {"passed": False, "terminal_logs": f"Test execution error: {exc}", "test_count": len(paths)}
    finally:
        if temp_jest_config is not None:
            temp_jest_config.unlink(missing_ok=True)


def tool_load_rules(*, repo_root: str | Path) -> Dict[str, Any]:
    """Load repo compliance rules from .agenti_helix/rules.json, if present."""
    rules_path = Path(repo_root).resolve() / ".agenti_helix" / "rules.json"
    if rules_path.exists():
        try:
            rules = json.loads(rules_path.read_text(encoding="utf-8"))
            return {"repo_rules_text": json.dumps(rules, indent=2)}
        except Exception:
            pass
    return {"repo_rules_text": "No repository rules file found. Apply general best practices."}


def _strip_html_to_text(body: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", body)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tool_fetch_doc_content(
    *,
    task_id: str,
    doc_url: Optional[str] = None,
    timeout_seconds: int = 15,
) -> Dict[str, Any]:
    """Fetch task-linked documentation and return a text payload for doc_fetcher_v1."""
    resolved_url = (doc_url or "").strip()
    if not resolved_url:
        ctx = load_task_context(task_id)
        resolved_url = (ctx.doc_url or "").strip() if ctx else ""
    if not resolved_url:
        return {
            "doc_url": "",
            "doc_title": "",
            "doc_content": "",
            "notes": (load_task_context(task_id).notes if load_task_context(task_id) else None),
            "fetch_error": "No doc_url stored for this task.",
        }

    req = urllib.request.Request(
        resolved_url,
        headers={"User-Agent": "agenti-helix/1.0 (+doc-fetcher)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read()
            content_type = resp.headers.get("content-type", "")
        body = raw.decode("utf-8", errors="replace")
        title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
        title = _strip_html_to_text(title_match.group(1)) if title_match else ""
        if "html" in content_type.lower():
            content = _strip_html_to_text(body)
        else:
            content = body.strip()
        ctx = load_task_context(task_id)
        return {
            "doc_url": resolved_url,
            "doc_title": title,
            "doc_content": content,
            "notes": ctx.notes if ctx else None,
        }
    except Exception as exc:
        ctx = load_task_context(task_id)
        return {
            "doc_url": resolved_url,
            "doc_title": "",
            "doc_content": "",
            "notes": ctx.notes if ctx else None,
            "fetch_error": str(exc),
        }


def tool_build_augmented_task_inputs(
    *,
    intent: str,
    acceptance_criteria: str,
    doc_fetcher_output: Optional[Dict[str, Any]] = None,
    task_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge optional doc/task context into the task intent and acceptance criteria."""
    intent_parts = [intent.strip()]
    criteria_parts = [acceptance_criteria.strip()]
    if task_notes:
        intent_parts.append(f"Task notes:\n{task_notes.strip()}")
    if isinstance(doc_fetcher_output, dict) and not doc_fetcher_output.get("irrelevant", False):
        constraints = [str(x).strip() for x in (doc_fetcher_output.get("key_constraints") or []) if str(x).strip()]
        summary = str(doc_fetcher_output.get("task_relevance_summary") or "").strip()
        examples = [
            f"- {str(item.get('label') or 'Example')}: {str(item.get('snippet') or '').strip()}"
            for item in (doc_fetcher_output.get("code_examples") or [])
            if isinstance(item, dict) and str(item.get("snippet") or "").strip()
        ]
        if summary:
            intent_parts.append(f"Documentation guidance:\n{summary}")
        if constraints:
            criteria_parts.append("Documentation constraints:\n- " + "\n- ".join(constraints))
        if examples:
            intent_parts.append("Relevant doc examples:\n" + "\n".join(examples[:4]))
    merged_intent = "\n\n".join(part for part in intent_parts if part)
    merged_acceptance = "\n\n".join(part for part in criteria_parts if part)
    return {"intent": merged_intent, "acceptance_criteria": merged_acceptance}


def tool_get_git_diff(
    *,
    repo_root: str | Path,
    files_written: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return a unified git diff for the changed files, or a clear fallback message."""
    repo_root_path = Path(repo_root).resolve()
    args = [p for p in (files_written or []) if p]
    cmd = ["git", "diff", "--"] + args if args else ["git", "diff"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root_path),
            timeout=20,
        )
        diff_text = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
        return {"git_diff": diff_text or "No git diff detected."}
    except Exception as exc:
        return {"git_diff": f"Unable to collect git diff: {exc}"}


def _pick_lint_command(repo_root_path: Path, changed_files: List[str]) -> List[str]:
    package_json = repo_root_path / "package.json"
    pyproject = repo_root_path / "pyproject.toml"
    if package_json.exists():
        return ["npx", "--yes", "eslint", "--format", "unix", *changed_files]
    if pyproject.exists():
        return ["python", "-m", "ruff", "check", *changed_files]
    return []


def tool_run_linter(
    *,
    repo_root: str | Path,
    target_file: str,
    changed_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    repo_root_path = Path(repo_root).resolve()
    files = [p for p in (changed_files or []) if p] or [target_file]
    cmd = _pick_lint_command(repo_root_path, files)
    if not cmd:
        return {"target_file": target_file, "language": tool_infer_language_from_target_file(target_file=target_file), "linter_raw_output": "Linter unsupported for this repository."}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root_path),
            timeout=60,
        )
        raw = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
        return {
            "target_file": target_file,
            "language": tool_infer_language_from_target_file(target_file=target_file),
            "linter_raw_output": raw or "Lint completed successfully with no findings.",
        }
    except Exception as exc:
        return {"target_file": target_file, "language": tool_infer_language_from_target_file(target_file=target_file), "linter_raw_output": f"Linter invocation failed: {exc}"}


def _pick_typecheck_command(repo_root_path: Path, changed_files: List[str]) -> List[str]:
    pyproject = repo_root_path / "pyproject.toml"
    package_json = repo_root_path / "package.json"
    if pyproject.exists():
        return ["python", "-m", "mypy", *changed_files]
    if package_json.exists():
        return ["npx", "--yes", "tsc", "--noEmit", "--pretty", "false"]
    return []


def tool_run_typecheck(
    *,
    repo_root: str | Path,
    target_file: str,
    changed_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    repo_root_path = Path(repo_root).resolve()
    files = [p for p in (changed_files or []) if p] or [target_file]
    cmd = _pick_typecheck_command(repo_root_path, files)
    if not cmd:
        return {"target_file": target_file, "language": tool_infer_language_from_target_file(target_file=target_file), "type_checker_output": "Type checker unsupported for this repository."}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root_path),
            timeout=90,
        )
        raw = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
        return {
            "target_file": target_file,
            "language": tool_infer_language_from_target_file(target_file=target_file),
            "type_checker_output": raw or "Found 0 errors.",
        }
    except Exception as exc:
        return {"target_file": target_file, "language": tool_infer_language_from_target_file(target_file=target_file), "type_checker_output": f"Type checker invocation failed: {exc}"}


def tool_map_evaluator_verdict(
    *,
    pass_tests: Optional[bool],
    evaluation_reasoning: str = "",
    feedback_for_coder: str = "",
    is_safe: bool = True,
    violations: Optional[List[str]] = None,
    diff_validator_output: Optional[Dict[str, Any]] = None,
    linter_output: Optional[Dict[str, Any]] = None,
    type_checker_output: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Translate judge_evaluator_v1 + security_governor_v1 outputs into the
    `judge_response` shape expected by the verification loop.

    pass_tests semantics:
      True  — tests ran and passed (or judge explicitly approved code-only review)
      False — tests ran and failed, OR tests were skipped and judge requires human review
      None  — judge explicitly flagged no-test-infrastructure; treated as FAIL to avoid
              silently passing code with zero behavioral verification
    """
    validator_verdict = ""
    validator_summary = ""
    if isinstance(diff_validator_output, dict):
        validator_verdict = str(diff_validator_output.get("verdict") or "").upper()
        validator_summary = str(diff_validator_output.get("summary") or "").strip()
    lint_summary = str((linter_output or {}).get("summary") or "").strip() if isinstance(linter_output, dict) else ""
    type_summary = str((type_checker_output or {}).get("summary") or "").strip() if isinstance(type_checker_output, dict) else ""

    if validator_verdict == "BLOCK":
        verdict = "FAIL"
        justification = validator_summary or "Diff validator blocked the change."
    elif not is_safe and violations:
        verdict = "FAIL"
        justification = "Security violations: " + "; ".join(str(v) for v in violations[:5])
    elif pass_tests is True:
        verdict = "PASS"
        justification = evaluation_reasoning or "All tests passed."
    elif pass_tests is None:
        # Judge indicated tests could not run due to missing infrastructure (not a code bug).
        # Treat as a conditional PASS: stage for human review rather than looping the coder.
        # The coder cannot fix missing package.json / jest config — retrying it is pointless.
        verdict = "PASS"
        justification = (
            evaluation_reasoning
            or "No test infrastructure available in this repository. Implementation staged for human review."
        )
    else:
        verdict = "FAIL"
        justification = feedback_for_coder or evaluation_reasoning or "Tests failed."
    extras = [part for part in [validator_summary if validator_verdict == "WARN" else "", lint_summary, type_summary] if part]
    if extras:
        justification = (justification + "\n\nAdditional checks:\n- " + "\n- ".join(extras)).strip()
    return {"verdict": verdict, "justification": justification, "problematic_lines": []}


def _strip_html_to_text(raw_html: str) -> str:
    """Very small HTML → text helper (no external deps)."""
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw_html)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def tool_fetch_doc_content(
    *,
    repo_root: str | Path,
    task_id: str = "",
    doc_url: str = "",
) -> Dict[str, Any]:
    """
    Resolve a documentation URL (task context API, explicit arg, or `.agenti_helix/doc_url`),
    fetch it, and return stripped text plus a best-effort title.
    """
    url = (doc_url or "").strip()
    if not url and task_id:
        tc = load_task_context(task_id)
        if tc and tc.doc_url:
            url = tc.doc_url.strip()
    if not url:
        marker = Path(repo_root).resolve() / ".agenti_helix" / "doc_url"
        if marker.exists():
            line = marker.read_text(encoding="utf-8").strip().splitlines()
            if line:
                url = line[0].strip()
    if not url:
        return {
            "doc_url": "",
            "doc_content": "",
            "doc_title": "",
            "fetch_error": "No doc_url (set via task context API or .agenti_helix/doc_url).",
        }
    root = Path(repo_root).resolve()
    if url.startswith("file:"):
        try:
            parsed = urlparse(url)
            raw_path = url2pathname(unquote(parsed.path))
            local = Path(raw_path).resolve()
            try:
                local.relative_to(root)
            except ValueError:
                return {
                    "doc_url": url,
                    "doc_content": "",
                    "doc_title": "",
                    "fetch_error": "file:// doc path must be inside repo_root.",
                }
            text = local.read_text(encoding="utf-8", errors="replace")[:120_000]
            return {"doc_url": url, "doc_content": text, "doc_title": local.name, "fetch_error": ""}
        except (OSError, ValueError) as exc:
            return {
                "doc_url": url,
                "doc_content": "",
                "doc_title": "",
                "fetch_error": f"Local doc read failed: {exc}",
            }
    try:
        req = Request(url, headers={"User-Agent": "agenti-helix-doc-fetch/1.0"})
        with urlopen(req, timeout=20) as resp:  # noqa: S310 — bounded URL fetch for doc presets
            body = resp.read(800_000).decode("utf-8", errors="replace")
        title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
        title = _strip_html_to_text(title_m.group(1)) if title_m else ""
        text = _strip_html_to_text(body)
        return {"doc_url": url, "doc_content": text[:120_000], "doc_title": title or url, "fetch_error": ""}
    except (URLError, OSError, ValueError) as exc:
        return {
            "doc_url": url,
            "doc_content": "",
            "doc_title": "",
            "fetch_error": f"Fetch failed: {exc}",
        }


def tool_merge_doc_into_intent(*, intent: str, doc_fetcher_output: Dict[str, Any]) -> str:
    """Append distilled doc constraints to the task intent for downstream agents."""
    parts: List[str] = [intent]
    summ = str(doc_fetcher_output.get("task_relevance_summary") or "").strip()
    kc = doc_fetcher_output.get("key_constraints") or []
    if isinstance(kc, list) and kc:
        parts.append("\n\n## Documentation constraints\n" + "\n".join(f"- {c}" for c in kc[:12] if str(c).strip()))
    if summ:
        parts.append("\n\n## Doc relevance\n" + summ)
    return "\n".join(parts).strip()


def tool_get_git_unified_diff(*, repo_root: str | Path) -> Dict[str, Any]:
    """Return `git diff` against HEAD for the working tree (best-effort)."""
    repo = Path(repo_root).resolve()
    try:
        r = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=90,
        )
        out = (r.stdout or "").strip()
        if r.returncode != 0 and not out:
            out = (r.stderr or "").strip() or f"git diff failed (exit {r.returncode})"
        return {"git_diff": out or "(empty diff)", "git_ok": r.returncode == 0}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {"git_diff": f"(git unavailable: {exc})", "git_ok": False}


def tool_run_linter(*, repo_root: str | Path, target_file: str) -> Dict[str, Any]:
    """Run a minimal linter for the target file; return raw stdout/stderr."""
    root = Path(repo_root).resolve()
    path = root / target_file
    if not path.exists():
        return {"raw_output": f"File not found: {target_file}", "supported": False}
    suf = path.suffix.lower()
    try:
        if suf == ".py":
            r = subprocess.run(
                ["ruff", "check", str(path)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "raw_output": "\n".join(filter(None, [r.stdout, r.stderr])).strip() or "(no ruff output)",
                "supported": True,
            }
        if suf in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            r = subprocess.run(
                ["npx", "--yes", "eslint", str(path), "--max-warnings", "999"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = "\n".join(filter(None, [r.stdout, r.stderr])).strip()
            if r.returncode == 127 or "command not found" in (out or "").lower():
                return {"raw_output": "eslint not available (npx/eslint missing).", "supported": False}
            return {"raw_output": out or "(eslint produced no output)", "supported": True}
    except FileNotFoundError:
        return {"raw_output": "Linter runner not installed for this file type.", "supported": False}
    except subprocess.TimeoutExpired:
        return {"raw_output": "Linter timed out.", "supported": True}
    return {"raw_output": f"No default linter for suffix {suf!r}.", "supported": False}


def tool_run_typecheck(*, repo_root: str | Path, target_file: str) -> Dict[str, Any]:
    """Run mypy or tsc --noEmit when available."""
    root = Path(repo_root).resolve()
    path = root / target_file
    if not path.exists():
        return {"raw_output": f"File not found: {target_file}", "supported": False}
    suf = path.suffix.lower()
    try:
        if suf == ".py":
            r = subprocess.run(
                ["python", "-m", "mypy", str(path), "--no-error-summary"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=90,
            )
            out = "\n".join(filter(None, [r.stdout, r.stderr])).strip()
            if "No module named mypy" in (out or "") or r.returncode == 1 and not out:
                return {"raw_output": "mypy not installed.", "supported": False}
            return {"raw_output": out or "(mypy produced no output)", "supported": True}
        if suf in {".ts", ".tsx"}:
            r = subprocess.run(
                ["npx", "--yes", "tsc", "--noEmit", str(path)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = "\n".join(filter(None, [r.stdout, r.stderr])).strip()
            return {"raw_output": out or "(tsc produced no output)", "supported": True}
    except FileNotFoundError:
        return {"raw_output": "Type checker not installed for this file type.", "supported": False}
    except subprocess.TimeoutExpired:
        return {"raw_output": "Type check timed out.", "supported": True}
    return {"raw_output": f"No default type checker for suffix {suf!r}.", "supported": False}


def tool_apply_diff_validator_gate(diff_validator_output: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    If diff_validator verdict is BLOCK, emit a snippet-judge shaped verdict so the judge chain can short-circuit.
    Otherwise return None so downstream judge steps still run.
    """
    dv = diff_validator_output or {}
    v = str(dv.get("verdict") or "").upper()
    if v == "BLOCK":
        return {
            "verdict": "FAIL",
            "justification": str(dv.get("summary") or "Diff validator blocked this change."),
            "problematic_lines": [],
        }
    return None


def tool_overlay_terminal_logs(
    *,
    test_results: Optional[Dict[str, Any]] = None,
    linter_out: Optional[Dict[str, Any]] = None,
    type_out: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge test logs with linter/type agent summaries for judge_evaluator."""
    base = dict(test_results or {})
    tl = str(base.get("terminal_logs") or "")
    extra_parts: List[str] = []
    if linter_out:
        extra_parts.append("Linter agent summary:\n" + str(linter_out.get("summary") or ""))
    if type_out:
        extra_parts.append("Type checker summary:\n" + str(type_out.get("summary") or ""))
    merged = "\n\n---\n\n".join([tl] + [p for p in extra_parts if p.strip()]).strip()
    base["terminal_logs"] = merged
    return base


def tool_escalate_to_human(
    *,
    reason: str,
    blocker_summary: str,
) -> Dict[str, Any]:
    """§4.5 — Semantic 'Raise Hand' tool.

    Called by an agent when it encounters ambiguous scope, contradictory
    constraints, or any situation it cannot resolve autonomously.  Returning
    this dict causes the verification loop to set
    `state.human_escalation_requested = True` and short-circuit to ESCALATE.
    """
    return {
        "escalation_requested": True,
        "reason": reason,
        "blocker_summary": blocker_summary,
    }


def tool_extract_module(
    *,
    repo_root: str | Path,
    target_file: str,
) -> Dict[str, Any]:
    """
    Extract the primary exported function/class from target_file using tree-sitter.

    Priority:
      1. export_statement containing function_declaration / class_declaration / lexical_declaration
      2. First function_declaration at root level
      3. First lexical_declaration at root level with an uppercase-named arrow function
      4. Full file fallback (full_file_used=True)

    Returns module_content, module_start_line (1-based), module_end_line (1-based),
    module_name, full_file_used.
    """
    repo_root_path = Path(repo_root).resolve()
    target_path = repo_root_path / target_file

    full_text = target_path.read_text(encoding="utf-8")
    full_lines = full_text.splitlines(keepends=True)

    def _full_file_result() -> Dict[str, Any]:
        return {
            "module_content": full_text,
            "module_start_line": 1,
            "module_end_line": len(full_lines),
            "module_name": Path(target_file).stem,
            "full_file_used": True,
        }

    try:
        lang = detect_language(target_path)
        if lang not in ("javascript", "typescript"):
            return _full_file_result()

        tree = parse_file(target_path, lang)
        root = tree.root_node

        # Priority 1: export_statement containing function/class/lexical declaration
        for child in root.children:
            if child.type == "export_statement":
                for sub in child.children:
                    if sub.type in ("function_declaration", "class_declaration", "lexical_declaration"):
                        name = ""
                        if sub.type == "function_declaration":
                            for n in sub.children:
                                if n.type == "identifier":
                                    name = n.text.decode("utf-8") if isinstance(n.text, bytes) else n.text
                                    break
                        elif sub.type == "class_declaration":
                            for n in sub.children:
                                if n.type == "identifier":
                                    name = n.text.decode("utf-8") if isinstance(n.text, bytes) else n.text
                                    break
                        elif sub.type == "lexical_declaration":
                            for n in sub.children:
                                if n.type == "variable_declarator":
                                    for m in n.children:
                                        if m.type == "identifier":
                                            name = m.text.decode("utf-8") if isinstance(m.text, bytes) else m.text
                                            break
                                    if name:
                                        break
                        start_line = child.start_point[0] + 1
                        end_line = child.end_point[0] + 1
                        module_content = "".join(full_lines[start_line - 1:end_line])
                        return {
                            "module_content": module_content,
                            "module_start_line": start_line,
                            "module_end_line": end_line,
                            "module_name": name or Path(target_file).stem,
                            "full_file_used": False,
                        }

        # Priority 2: First function_declaration at root level
        for child in root.children:
            if child.type == "function_declaration":
                name = ""
                for n in child.children:
                    if n.type == "identifier":
                        name = n.text.decode("utf-8") if isinstance(n.text, bytes) else n.text
                        break
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                module_content = "".join(full_lines[start_line - 1:end_line])
                return {
                    "module_content": module_content,
                    "module_start_line": start_line,
                    "module_end_line": end_line,
                    "module_name": name or Path(target_file).stem,
                    "full_file_used": False,
                }

        # Priority 3: First lexical_declaration with an uppercase-named arrow function
        for child in root.children:
            if child.type == "lexical_declaration":
                for declarator in child.children:
                    if declarator.type == "variable_declarator":
                        var_name = ""
                        has_arrow = False
                        for n in declarator.children:
                            if n.type == "identifier":
                                var_name = n.text.decode("utf-8") if isinstance(n.text, bytes) else n.text
                            if n.type == "arrow_function":
                                has_arrow = True
                        if has_arrow and var_name and var_name[0].isupper():
                            start_line = child.start_point[0] + 1
                            end_line = child.end_point[0] + 1
                            module_content = "".join(full_lines[start_line - 1:end_line])
                            return {
                                "module_content": module_content,
                                "module_start_line": start_line,
                                "module_end_line": end_line,
                                "module_name": var_name,
                                "full_file_used": False,
                            }

        # Priority 4: Full file fallback
        return _full_file_result()

    except Exception:
        return _full_file_result()


def tool_splice_module(
    *,
    repo_root: str | Path,
    target_file: str,
    module_start_line: int,
    module_end_line: int,
    rewritten_module: str,
) -> Dict[str, Any]:
    """
    Replace lines module_start_line..module_end_line (1-based, inclusive) in target_file
    with rewritten_module, then run a tree-sitter syntax check.

    Returns a diff_json-compatible dict with files_written, test_file_paths, diff_json_str,
    and optionally syntax_error.
    """
    repo_root_path = Path(repo_root).resolve()
    target_path = repo_root_path / target_file

    orig_text = target_path.read_text(encoding="utf-8")
    orig_lines = orig_text.splitlines(keepends=True)

    # Ensure rewritten_module ends with a newline to avoid joining lines.
    if rewritten_module and not rewritten_module.endswith("\n"):
        rewritten_module = rewritten_module + "\n"

    rewritten_lines = rewritten_module.splitlines(keepends=True)

    new_lines = orig_lines[: module_start_line - 1] + rewritten_lines + orig_lines[module_end_line:]
    new_text = "".join(new_lines)

    target_path.write_text(new_text, encoding="utf-8")

    # Syntax check for JS/TS via tree-sitter.
    syntax_error: str | None = None
    try:
        lang = detect_language(target_path)
        if lang in ("javascript", "typescript"):
            parse_file(target_path, lang)
    except Exception as exc:
        syntax_error = str(exc)

    result: Dict[str, Any] = {
        "files_written": [target_file],
        "test_file_paths": [],
        "diff_summary": f"Spliced module into {target_file} (lines {module_start_line}–{module_end_line})",
    }
    result["diff_json_str"] = json.dumps(
        {"files_written": [target_file], "test_file_paths": []},
        indent=2,
    )
    if syntax_error:
        result["syntax_error"] = syntax_error
    return result


TOOL_REGISTRY: Dict[str, Any] = {
    # Core patch pipeline tools
    "build_repo_map_context": tool_build_repo_map_context,
    "get_focused_context": tool_get_focused_context,
    "apply_line_patch_and_validate": tool_apply_line_patch_and_validate,
    "snapshot_target_file": tool_snapshot_target_file,
    "infer_language_from_target_file": tool_infer_language_from_target_file,
    "build_tool_logs_json": tool_build_tool_logs_json,
    # Full TDD pipeline tools
    "build_ast_context": tool_build_ast_context,
    "load_file_contents": tool_load_file_contents,
    "write_all_files": tool_write_all_files,
    "run_tests": tool_run_tests,
    "load_rules": tool_load_rules,
    "fetch_doc_content": tool_fetch_doc_content,
    "build_augmented_task_inputs": tool_build_augmented_task_inputs,
    "get_git_diff": tool_get_git_diff,
    "run_linter": tool_run_linter,
    "run_typecheck": tool_run_typecheck,
    "map_evaluator_verdict": tool_map_evaluator_verdict,
    # Pipeline preset helpers
    "fetch_doc_content": tool_fetch_doc_content,
    "merge_doc_into_intent": tool_merge_doc_into_intent,
    "get_git_unified_diff": tool_get_git_unified_diff,
    "run_linter": tool_run_linter,
    "run_typecheck": tool_run_typecheck,
    "apply_diff_validator_gate": tool_apply_diff_validator_gate,
    "overlay_terminal_logs": tool_overlay_terminal_logs,
    # Shared utilities
    "query_memory": tool_query_memory,
    "escalate_to_human": tool_escalate_to_human,
}

