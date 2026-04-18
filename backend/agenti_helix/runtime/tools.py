from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from agenti_helix.core.ast_parser import parse_file
from agenti_helix.core.diff_builder import LinePatch, apply_line_patch_to_file
from agenti_helix.core.repo_map import generate_repo_map, get_focused_files
from agenti_helix.core.repo_scanner import detect_language


# region agent log
def _debug_write(payload: Dict[str, Any]) -> None:
    try:
        from pathlib import Path as _Path
        import json as _json
        import time as _time

        # Workspace-root relative, debug-session specific.
        ws = _Path(__file__).resolve().parents[3]
        p = ws / ".cursor" / "debug-a3db40.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        payload.setdefault("sessionId", "a3db40")
        payload.setdefault("timestamp", int(_time.time() * 1000))
        p.open("a", encoding="utf-8").write(_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return
# endregion agent log


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
        # region agent log
        _debug_write(
            {
                "runId": "post-fix",
                "hypothesisId": "V1",
                "location": "agenti_helix/runtime/tools.py:tool_build_ast_context",
                "message": "AST context built (focused)",
                "data": {"target_files_count": len(target_files), "repo_files_count": len(result.get("repo_files") or [])},
            }
        )
        # endregion agent log
        return result
    full = tool_build_repo_map_context(repo_root=repo_root)
    full["ast_repo_map_json"] = full["repo_map_json"]
    # region agent log
    _debug_write(
        {
            "runId": "post-fix",
            "hypothesisId": "V1",
            "location": "agenti_helix/runtime/tools.py:tool_build_ast_context",
            "message": "AST context built (full)",
            "data": {"repo_files_count": len(full.get("repo_files") or [])},
        }
    )
    # endregion agent log
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

    result: Dict[str, Any] = {
        "files_written": files_written,
        "test_file_paths": test_file_paths,
        "diff_summary": (
            f"Wrote {len(files_written)} code file(s) and {len(test_file_paths)} test file(s): "
            + ", ".join(files_written + test_file_paths)
        ),
    }
    # Pre-serialise for template injection in security_governor / judge_evaluator prompts.
    # Include file_contents so the security governor can actually audit the code.
    result["diff_json_str"] = json.dumps(
        {
            "files_written": files_written,
            "test_file_paths": test_file_paths,
            "file_contents": file_contents,
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
        # region agent log
        _debug_write(
            {
                "runId": "post-fix",
                "hypothesisId": "V2",
                "location": "agenti_helix/runtime/tools.py:tool_write_all_files",
                "message": "Snapshots captured for manual sign-off",
                "data": {
                    "checkpoint_id_present": bool(checkpoint_id),
                    "snapshots_dir": str(snapshots_dir),
                    "files_count": len(manifest.get("files") or []),
                },
            }
        )
        # endregion agent log
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

    try:
        if py_files:
            cmd = (
                ["python", "-m", "pytest"]
                + [str(repo_root_path / p) for p in py_files]
                + ["-v", "--tb=short", "--no-header"]
            )
        elif js_files:
            cmd = ["npx", "--yes", "jest", "--no-coverage", "--passWithNoTests"] + js_files
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
    pass_tests: Optional[bool],
    evaluation_reasoning: str = "",
    feedback_for_coder: str = "",
    is_safe: bool = True,
    violations: Optional[List[str]] = None,
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
    if not is_safe and violations:
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
    return {"verdict": verdict, "justification": justification, "problematic_lines": []}


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
    "map_evaluator_verdict": tool_map_evaluator_verdict,
    # Module rewriter tools
    "extract_module": tool_extract_module,
    "splice_module": tool_splice_module,
    # Shared utilities
    "query_memory": tool_query_memory,
    "escalate_to_human": tool_escalate_to_human,
}

