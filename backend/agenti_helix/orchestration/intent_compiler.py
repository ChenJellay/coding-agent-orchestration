from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from agenti_helix.agents.models import IntentCompilerOutput as _IntentCompilerOutputModel
from agenti_helix.observability.debug_log import log_event
from agenti_helix.api.dashboard_doc import resolve_dashboard_doc_url
from agenti_helix.runtime.chain_defaults import (
    default_intent_compiler_chain,
    precompile_doc_enrichment_chain,
)
from agenti_helix.runtime.chain_runtime import run_chain
from agenti_helix.runtime.tools import tool_build_repo_map_context, tool_fetch_doc_content
from agenti_helix.verification.checkpointing import EditTaskSpec

from .orchestrator import DagNodeSpec, DagSpec

_MAX_COMPILE_RETRIES = 2

_PRECOMPILE_DOC_ACCEPTANCE_CRITERIA = (
    "Break the macro intent into a small DAG of verifiable coding steps, using the documentation "
    "constraints below when deciding scope, ordering, and acceptance criteria per node."
)


@dataclass
class IntentNodeSpec:
    """Lightweight representation of a node produced by the intent compiler."""

    node_id: str
    description: str
    target_file: str
    acceptance_criteria: str
    pipeline_mode: str = "patch"


def _coder_task_intent_for_node(
    *,
    node_id: str,
    description: str,
    acceptance_criteria: str,
    macro_intent: str,
    max_macro_chars: int = 600,
) -> str:
    """
    Primary instructions for the coder: one node's goal and acceptance, plus a short product context line.

    The full macro intent is easy to overfit; keep it as reference-only so the agent does not try to
    implement the entire feature in one node.
    """
    macro_compact = " ".join(macro_intent.strip().split())
    if len(macro_compact) > max_macro_chars:
        macro_compact = macro_compact[: max_macro_chars - 3] + "..."
    return (
        f"You are implementing node **{node_id}** only. Do not expand scope beyond this node.\n\n"
        f"### Goal\n{description.strip()}\n\n"
        f"### Acceptance criteria\n{acceptance_criteria.strip()}\n\n"
        f"### Product context (reference only; do not treat as a separate task list)\n{macro_compact}"
    )


def enrich_macro_intent_with_doc_before_compile(
    macro_intent: str,
    *,
    repo_path: str,
    dag_id: str,
    doc_url: Optional[str] = None,
    doc_text: Optional[str] = None,
    doc_filename: Optional[str] = None,
) -> Tuple[str, str, bool]:
    """
    When the dashboard attached a URL or uploaded doc text, resolve it, fetch content, run doc_fetcher,
    and merge distilled constraints into `macro_intent` **before** the intent compiler plans the DAG.

    Returns `(intent_for_compiler, effective_doc_url, doc_merged_into_intent)`.
    """
    effective_doc = resolve_dashboard_doc_url(
        repo_path=repo_path,
        dag_id=dag_id,
        doc_url=doc_url,
        doc_text=doc_text,
        doc_filename=doc_filename,
    )
    url = (effective_doc or "").strip()
    if not url:
        return macro_intent, "", False

    repo_root = Path(repo_path).resolve()
    fd = tool_fetch_doc_content(repo_root=str(repo_root), task_id="", doc_url=url)
    if (fd.get("fetch_error") or "").strip() and not (str(fd.get("doc_content") or "").strip()):
        log_event(
            run_id=dag_id,
            hypothesis_id="INTENT",
            location="agenti_helix/orchestration/intent_compiler.py:enrich_macro_intent_with_doc_before_compile",
            message="Skipping pre-compile doc enrichment (fetch returned no content)",
            data={"fetch_error": fd.get("fetch_error")},
        )
        return macro_intent, effective_doc, False

    rctx = tool_build_repo_map_context(repo_root=str(repo_root))
    paths = list(rctx.get("allowed_paths") or [])
    target_file = paths[0] if paths else "."

    initial_context: Dict[str, Any] = {
        "repo_root": str(repo_root),
        "task_id": f"{dag_id}:intent-precompile",
        "doc_url": url,
        "macro_intent": macro_intent,
        "target_file": target_file,
        "acceptance_criteria": _PRECOMPILE_DOC_ACCEPTANCE_CRITERIA,
    }
    macro_before = macro_intent.strip()
    try:
        final_ctx = run_chain(
            chain_spec=precompile_doc_enrichment_chain(),
            initial_context=initial_context,
            cancel_token=None,
            run_id=dag_id,
            hypothesis_id="INTENT",
            location_prefix="agenti_helix/orchestration/intent_compiler.py:precompile_doc",
        )
    except Exception as exc:
        log_event(
            run_id=dag_id,
            hypothesis_id="INTENT",
            location="agenti_helix/orchestration/intent_compiler.py:enrich_macro_intent_with_doc_before_compile",
            message="Pre-compile doc chain failed; compiling without doc enrichment",
            data={"error": str(exc)[:500]},
        )
        return macro_intent, effective_doc, False

    merged = final_ctx.get("macro_intent")
    if isinstance(merged, str) and merged.strip():
        out = merged.strip()
        doc_merged = out != macro_before
        return out, effective_doc, doc_merged
    return macro_intent, effective_doc, False


def _resolve_target_file(repo_root: Path, target_file: str) -> str:
    """
    Best-effort resolver for LLM-returned paths.

    The LLM may guess wrong casing or extension.
    We try:
    - Exact match
    - Case-insensitive full path match within repo
    - Same directory + same stem (any extension)
    - Global match by stem (any extension), prefer shortest path
    """
    candidate = (repo_root / target_file).resolve()
    if candidate.exists():
        return target_file

    parts = Path(target_file).parts
    lowered_parts = [p.lower() for p in parts]

    all_files = [p for p in repo_root.rglob("*") if p.is_file()]

    for p in all_files:
        rel = p.relative_to(repo_root)
        if [x.lower() for x in rel.parts] == lowered_parts:
            return rel.as_posix()

    wanted = Path(target_file)
    wanted_dir = wanted.parent.as_posix().lower()
    wanted_stem = wanted.stem.lower()
    same_dir_matches: list[Path] = []
    for p in all_files:
        rel = p.relative_to(repo_root)
        if rel.parent.as_posix().lower() == wanted_dir and rel.stem.lower() == wanted_stem:
            same_dir_matches.append(rel)
    if same_dir_matches:
        same_dir_matches.sort(key=lambda x: x.as_posix())
        return same_dir_matches[0].as_posix()

    stem_matches: list[Path] = []
    for p in all_files:
        rel = p.relative_to(repo_root)
        if rel.stem.lower() == wanted_stem:
            stem_matches.append(rel)
    if stem_matches:
        stem_matches.sort(key=lambda x: (len(x.as_posix()), x.as_posix()))
        return stem_matches[0].as_posix()

    return target_file


def _run_intent_chain(macro_intent: str, repo_root: Path, *, feedback: str = "") -> Dict[str, object]:
    """Execute the intent compiler chain once, returning the validated output dict."""
    prompt_intent = macro_intent
    if feedback:
        prompt_intent = (
            f"{macro_intent}\n\n"
            f"Previous compilation attempt failed. Please correct the following issues and output valid JSON:\n{feedback}"
        )
    ctx: Dict[str, object] = {
        "macro_intent": prompt_intent,
        "repo_path": str(repo_root),
    }
    ctx = run_chain(
        chain_spec=default_intent_compiler_chain(),
        initial_context=ctx,
        cancel_token=None,
        run_id="intent",
        hypothesis_id="LLM",
        location_prefix="agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
    )
    raw = ctx.get("intent_compiler_output")
    return raw if isinstance(raw, dict) else {}


def compile_macro_intent_with_llm(
    macro_intent: str,
    repo_path: str,
    *,
    dag_id: Optional[str] = None,
    user_intent_label: Optional[str] = None,
) -> DagSpec:
    repo_root = Path(repo_path).resolve()

    last_error: str = ""
    typed: Dict[str, object] = {}

    for attempt in range(1, _MAX_COMPILE_RETRIES + 1):
        raw = _run_intent_chain(macro_intent, repo_root, feedback=last_error)
        try:
            validated = _IntentCompilerOutputModel.model_validate(raw)
        except (ValidationError, Exception) as exc:
            last_error = f"Output failed Pydantic validation: {exc}"
            log_event(
                run_id="intent",
                hypothesis_id="LLM",
                location="agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
                message=f"Intent compiler output invalid (attempt {attempt}/{_MAX_COMPILE_RETRIES})",
                data={"error": last_error},
            )
            continue

        if not validated.nodes:
            last_error = "Compiler returned an empty nodes list; at least one DAG node is required."
            log_event(
                run_id="intent",
                hypothesis_id="LLM",
                location="agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
                message=f"Intent compiler returned empty DAG (attempt {attempt}/{_MAX_COMPILE_RETRIES})",
                data={"error": last_error},
            )
            continue

        typed = validated.model_dump()
        log_event(
            run_id="intent",
            hypothesis_id="LLM",
            location="agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
            message="Intent compiler succeeded",
            data={"attempt": attempt, "node_count": len(validated.nodes)},
        )
        break
    else:
        raise ValueError(
            f"Intent compiler failed after {_MAX_COMPILE_RETRIES} attempt(s). Last error: {last_error}"
        )

    nodes_raw = typed.get("nodes") or []
    edges_raw = typed.get("edges") or []
    # Caller-supplied dag_id (dashboard / CLI) must win over the optional id in LLM JSON,
    # otherwise we persist two different `*.json` specs and the UI shows duplicate DAGs.
    dag_identifier = str(dag_id or typed.get("dag_id") or "dag-llm-intent")

    intent_nodes: List[IntentNodeSpec] = []
    for item in nodes_raw:
        if not isinstance(item, dict):
            continue
        intent_nodes.append(
            IntentNodeSpec(
                node_id=str(item.get("node_id") or ""),
                description=str(item.get("description") or ""),
                target_file=str(item.get("target_file") or ""),
                acceptance_criteria=str(item.get("acceptance_criteria") or ""),
                pipeline_mode=str(item.get("pipeline_mode") or "patch").strip() or "patch",
            )
        )

    edges: List[Tuple[str, str]] = []
    for pair in edges_raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        src, dst = pair
        edges.append((str(src), str(dst)))

    dag_nodes: Dict[str, DagNodeSpec] = {}
    for n in intent_nodes:
        resolved_target = _resolve_target_file(repo_root, n.target_file)
        task = EditTaskSpec(
            task_id=f"{dag_identifier}:{n.node_id}",
            intent=_coder_task_intent_for_node(
                node_id=n.node_id,
                description=n.description,
                acceptance_criteria=n.acceptance_criteria,
                macro_intent=macro_intent,
            ),
            target_file=resolved_target,
            acceptance_criteria=n.acceptance_criteria,
            repo_path=str(repo_root),
            pipeline_mode=n.pipeline_mode,
        )
        dag_nodes[n.node_id] = DagNodeSpec(
            node_id=n.node_id,
            description=n.description,
            task=task,
        )

    display_label = (user_intent_label if user_intent_label is not None else macro_intent).strip()
    spec = DagSpec(
        dag_id=dag_identifier,
        macro_intent=macro_intent,
        nodes=dag_nodes,
        edges=edges,
        user_intent_label=display_label,
    )
    return spec


def compile_macro_intent_to_dag(
    macro_intent: str,
    repo_path: str,
    *,
    dag_id: Optional[str] = None,
    user_intent_label: Optional[str] = None,
) -> DagSpec:
    """
    Compile `macro_intent` into a `DagSpec` via the LLM intent compiler.

    Raises `ValueError` if the LLM intent compiler fails after all retries.
    Callers must handle this; there is no deterministic fallback.
    """
    return compile_macro_intent_with_llm(
        macro_intent,
        repo_path,
        dag_id=dag_id,
        user_intent_label=user_intent_label,
    )

