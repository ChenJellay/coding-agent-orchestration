from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import ValidationError

from agenti_helix.agents.models import IntentCompilerOutput as _IntentCompilerOutputModel
from agenti_helix.observability.debug_log import log_event
from agenti_helix.runtime.chain_defaults import default_intent_compiler_chain
from agenti_helix.runtime.chain_runtime import run_chain
from agenti_helix.verification.checkpointing import EditTaskSpec

from .orchestrator import DagNodeSpec, DagSpec, load_dag_spec, persist_dag_spec

_MAX_COMPILE_RETRIES = 2


@dataclass
class IntentNodeSpec:
    """Lightweight representation of a node produced by the intent compiler."""

    node_id: str
    description: str
    target_file: str
    acceptance_criteria: str
    pipeline_mode: str = "patch"
    workflow: Optional[List[str]] = None


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

# region agent log
def _debug_write(payload: Dict[str, object]) -> None:
    # Minimal NDJSON logger for debug-mode sessions; avoid secrets/PII.
    try:
        import json as _json  # local import to avoid global overhead
        from pathlib import Path as _Path

        p = _Path(__file__).resolve().parents[3] / ".cursor" / "debug-a3db40.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("a", encoding="utf-8").write(_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return
# endregion agent log


def compile_macro_intent_with_llm(
    macro_intent: str,
    repo_path: str,
    *,
    dag_id: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout_seconds: float = 90.0,
) -> DagSpec:
    repo_root = Path(repo_path).resolve()
    if base_url:
        log_event(
            run_id="intent",
            hypothesis_id="LLM",
            location="agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
            message="llm_base_url ignored (in-process chain runtime mode)",
            data={"base_url": base_url},
        )

    last_error: str = ""
    typed: Dict[str, object] = {}

    for attempt in range(1, _MAX_COMPILE_RETRIES + 1):
        raw = _run_intent_chain(macro_intent, repo_root, feedback=last_error)
        # region agent log
        _debug_write(
            {
                "sessionId": "a3db40",
                "runId": "pre-fix",
                "hypothesisId": "H1",
                "location": "agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
                "message": "Intent compiler raw output shape",
                "data": {
                    "attempt": attempt,
                    "raw_type": type(raw).__name__,
                    "raw_keys": sorted(list(raw.keys()))[:50] if isinstance(raw, dict) else [],
                    "has_nodes": isinstance(raw, dict) and ("nodes" in raw),
                    "has_edges": isinstance(raw, dict) and ("edges" in raw),
                    "has_node_id": isinstance(raw, dict) and ("node_id" in raw),
                    "has_target_file": isinstance(raw, dict) and ("target_file" in raw),
                    "has_targetFile": isinstance(raw, dict) and ("targetFile" in raw),
                },
                "timestamp": __import__("time").time_ns() // 1_000_000,
            }
        )
        # endregion agent log
        try:
            validated = _IntentCompilerOutputModel.model_validate(raw)
        except (ValidationError, Exception) as exc:
            last_error = f"Output failed Pydantic validation: {exc}"
            # region agent log
            _debug_write(
                {
                    "sessionId": "a3db40",
                    "runId": "pre-fix",
                    "hypothesisId": "H2",
                    "location": "agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
                    "message": "Intent compiler validation failed",
                    "data": {"attempt": attempt, "error": str(exc)[:2000]},
                    "timestamp": __import__("time").time_ns() // 1_000_000,
                }
            )
            # endregion agent log
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
    dag_identifier = str(typed.get("dag_id") or dag_id or "dag-llm-intent")

    intent_nodes: List[IntentNodeSpec] = []
    for item in nodes_raw:
        if not isinstance(item, dict):
            continue
        raw_workflow = item.get("workflow")
        workflow_list: Optional[List[str]] = None
        if isinstance(raw_workflow, list) and raw_workflow:
            workflow_list = [str(x) for x in raw_workflow if isinstance(x, str) and x.strip()]
            if not workflow_list:
                workflow_list = None
        intent_nodes.append(
            IntentNodeSpec(
                node_id=str(item.get("node_id") or ""),
                description=str(item.get("description") or ""),
                target_file=str(item.get("target_file") or ""),
                acceptance_criteria=str(item.get("acceptance_criteria") or ""),
                pipeline_mode=str(item.get("pipeline_mode") or "patch"),
                workflow=workflow_list,
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
        # Accept "patch", "build", or "custom". A bespoke `workflow` list is only honored
        # when `pipeline_mode == "custom"` (explicit opt-in), otherwise we fall back to
        # the named preset and ignore any stray workflow list the LLM emitted.
        if n.pipeline_mode in ("patch", "build", "custom"):
            pipeline_mode = n.pipeline_mode
        else:
            pipeline_mode = "patch"
        workflow = n.workflow if pipeline_mode == "custom" else None
        task = EditTaskSpec(
            task_id=f"{dag_identifier}:{n.node_id}",
            intent=f"{macro_intent}\n\nSubtask: {n.description}",
            target_file=resolved_target,
            acceptance_criteria=n.acceptance_criteria,
            repo_path=str(repo_root),
            pipeline_mode=pipeline_mode,
            workflow=workflow,
        )
        dag_nodes[n.node_id] = DagNodeSpec(
            node_id=n.node_id,
            description=n.description,
            task=task,
        )

    spec = DagSpec(
        dag_id=dag_identifier,
        macro_intent=macro_intent,
        nodes=dag_nodes,
        edges=edges,
    )
    persist_dag_spec(spec)
    return spec


def compile_macro_intent_deterministic(
    macro_intent: str,
    repo_path: str,
    *,
    dag_id: Optional[str] = None,
) -> DagSpec:
    repo_root = Path(repo_path).resolve()
    dag_identifier = dag_id or "dag-demo-header"

    node1 = DagNodeSpec(
        node_id="N1-change-color",
        description="Update header button background color to green.",
        task=EditTaskSpec(
            task_id="header-color-primary",
            intent=macro_intent + "\n\nSubtask: Change the header button background color to green.",
            target_file="src/components/header.js",
            acceptance_criteria=(
                "Header has exactly one visible button whose background color is green. "
                "No unrelated logic is modified."
            ),
            repo_path=str(repo_root),
        ),
    )

    node2 = DagNodeSpec(
        node_id="N2-refine-styles",
        description="Refine header button styling to remain accessible and consistent.",
        task=EditTaskSpec(
            task_id="header-style-refine",
            intent=(
                macro_intent
                + "\n\nSubtask: Ensure the header button styling is consistent and accessible "
                "(contrast preserved, padding and radius intact)."
            ),
            target_file="src/components/header.js",
            acceptance_criteria=(
                "Header button remains green with good contrast, spacing, and radius. "
                "Structure of Header component is preserved."
            ),
            repo_path=str(repo_root),
        ),
    )

    node3 = DagNodeSpec(
        node_id="N3-verify-structure",
        description="Verify Header markup still renders a single primary button.",
        task=EditTaskSpec(
            task_id="header-structure-verify",
            intent=(
                macro_intent
                + "\n\nSubtask: Confirm the Header component still renders a single primary button "
                "inside a header wrapper with padding styles."
            ),
            target_file="src/components/header.js",
            acceptance_criteria=(
                "Header component renders one primary button inside a header element; "
                "styling changes must not introduce extra buttons or remove the wrapper."
            ),
            repo_path=str(repo_root),
        ),
    )

    nodes: Dict[str, DagNodeSpec] = {
        node1.node_id: node1,
        node2.node_id: node2,
        node3.node_id: node3,
    }
    edges: List[Tuple[str, str]] = [
        (node1.node_id, node2.node_id),
        (node2.node_id, node3.node_id),
    ]

    spec = DagSpec(
        dag_id=dag_identifier,
        macro_intent=macro_intent,
        nodes=nodes,
        edges=edges,
    )
    persist_dag_spec(spec)
    return spec


def compile_macro_intent_to_dag(
    macro_intent: str,
    repo_path: str,
    *,
    dag_id: Optional[str] = None,
    use_llm: bool = True,
    llm_base_url: Optional[str] = None,
) -> DagSpec:
    """
    Compile `macro_intent` into a `DagSpec`.

    When `use_llm=True` (default), the LLM intent compiler is used and a
    `ValueError` is raised if it fails after all retries — callers must handle
    this rather than silently falling back to the demo stub.

    When `use_llm=False`, the deterministic demo compiler runs; this is only
    appropriate for the demo repo layout (src/components/header.js). Callers
    should not use `use_llm=False` in production.
    """
    if use_llm:
        return compile_macro_intent_with_llm(
            macro_intent,
            repo_path,
            dag_id=dag_id,
            base_url=llm_base_url,
        )
    return compile_macro_intent_deterministic(
        macro_intent,
        repo_path,
        dag_id=dag_id,
    )

