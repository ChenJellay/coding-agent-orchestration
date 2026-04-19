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
from urllib.request import Request, urlopen

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
    """
    repo_root_path = Path(repo_root).resolve()
    repo_map = generate_repo_map(repo_root_path)
    focused = get_focused_files(repo_map, target_files, depth=depth)

    repo_files: List[Dict[str, Any]] = []
    allowed_paths: List[str] = []
    for f in focused:
        repo_files.append({"path": f.path, "language": f.language, "symbols": f.symbols})
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
        repo_files.append({"path": f.path, "language": f.language, "symbols": f.symbols})
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
    """Return a focused repo map (AST-level detail) for the context librarian."""
    result = tool_get_focused_context(repo_root=repo_root, target_files=target_files or [], depth=2)
    # Expose the JSON under the name the librarian prompt expects.
    result["ast_repo_map_json"] = result["repo_map_json"]
    return result


def tool_load_file_contents(
    *,
    repo_root: str | Path,
    required_files: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Read the full content of each file identified by the context librarian."""
    repo_root_path = Path(repo_root).resolve()
    file_contexts: List[Dict[str, Any]] = []
    for req in required_files:
        file_path = str(req.get("file_path") or req.get("path") or "")
        required_symbols = req.get("required_symbols") or []
        content: str = ""
        if file_path:
            try:
                content = (repo_root_path / file_path).read_text(encoding="utf-8")
            except OSError:
                content = f"# File not found: {file_path}"
        file_contexts.append({"file_path": file_path, "required_symbols": required_symbols, "content": content})
    return {"file_contexts_json": json.dumps(file_contexts, indent=2)}


def tool_write_all_files(
    *,
    repo_root: str | Path,
    modified_files: Optional[List[Dict[str, Any]]] = None,
    test_files: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Write code and test files to disk (output of coder_builder_v1 / sdet_v1).

    Returns a `diff_json`-compatible dict with `files_written`, `test_file_paths`,
    and a pre-serialised `diff_json_str` for template injection.
    """
    repo_root_path = Path(repo_root).resolve()
    files_written: List[str] = []
    test_file_paths: List[str] = []

    for f in (modified_files or []):
        file_path = str(f.get("file_path") or f.get("path") or "")
        content = str(f.get("content") or "")
        if not file_path or not content:
            continue
        target = repo_root_path / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        files_written.append(file_path)

    for f in (test_files or []):
        file_path = str(f.get("file_path") or f.get("path") or "")
        content = str(f.get("content") or "")
        if not file_path or not content:
            continue
        target = repo_root_path / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        test_file_paths.append(file_path)

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
        return {"passed": True, "terminal_logs": "No test files provided — skipping test run.", "test_count": 0}

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


def tool_map_evaluator_verdict(
    *,
    pass_tests: bool,
    evaluation_reasoning: str = "",
    feedback_for_coder: str = "",
    is_safe: bool = True,
    violations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Translate judge_evaluator_v1 + security_governor_v1 outputs into the
    `judge_response` shape expected by the verification loop.
    """
    if not is_safe and violations:
        verdict = "FAIL"
        justification = "Security violations: " + "; ".join(str(v) for v in violations[:5])
    elif pass_tests:
        verdict = "PASS"
        justification = evaluation_reasoning or "All tests passed."
    else:
        verdict = "FAIL"
        justification = feedback_for_coder or evaluation_reasoning or "Tests failed."
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

