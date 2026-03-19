from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import urllib.error
import urllib.request

from agenti_helix.observability.debug_log import log_event
from agenti_helix.verification.checkpointing import EditTaskSpec

from .orchestrator import DagNodeSpec, DagSpec, persist_dag_spec


@dataclass
class IntentNodeSpec:
    """Lightweight representation of a node produced by the intent compiler."""

    node_id: str
    description: str
    target_file: str
    acceptance_criteria: str


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


def _default_llm_compiler_url() -> str:
    return "http://localhost:8000/intent-compiler"


def compile_macro_intent_with_llm(
    macro_intent: str,
    repo_path: str,
    *,
    dag_id: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout_seconds: float = 90.0,
) -> DagSpec:
    repo_root = Path(repo_path).resolve()
    service_url = (base_url or _default_llm_compiler_url()).rstrip("/")

    payload = json.dumps({"macro_intent": macro_intent, "repo_path": str(repo_root)}).encode("utf-8")

    request = urllib.request.Request(
        service_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        log_event(
            run_id="intent",
            hypothesis_id="LLM",
            location="agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
            message="Intent compiler transport error",
            data={"base_url": service_url, "error": str(exc)},
        )
        raise RuntimeError(f"Transport error talking to intent compiler: {exc}") from exc

    try:
        data: Dict[str, object] = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover
        log_event(
            run_id="intent",
            hypothesis_id="LLM",
            location="agenti_helix/orchestration/intent_compiler.py:compile_macro_intent_with_llm",
            message="Intent compiler returned invalid JSON",
            data={"base_url": service_url, "error": str(exc), "raw": raw[:500]},
        )
        raise RuntimeError(f"Invalid JSON from intent compiler: {exc}; payload={raw!r}") from exc

    nodes_raw = data.get("nodes") or []
    edges_raw = data.get("edges") or []
    dag_identifier = str(data.get("dag_id") or dag_id or "dag-llm-intent")

    intent_nodes: List[IntentNodeSpec] = []
    for item in nodes_raw:
        item_dict = dict(item)  # type: ignore[arg-type]
        intent_nodes.append(
            IntentNodeSpec(
                node_id=str(item_dict["node_id"]),
                description=str(item_dict["description"]),
                target_file=str(item_dict["target_file"]),
                acceptance_criteria=str(item_dict["acceptance_criteria"]),
            )
        )

    edges: List[Tuple[str, str]] = []
    for pair in edges_raw:
        src, dst = pair  # type: ignore[misc]
        edges.append((str(src), str(dst)))

    dag_nodes: Dict[str, DagNodeSpec] = {}
    for n in intent_nodes:
        resolved_target = _resolve_target_file(repo_root, n.target_file)
        task = EditTaskSpec(
            task_id=f"{dag_identifier}:{n.node_id}",
            intent=f"{macro_intent}\n\nSubtask: {n.description}",
            target_file=resolved_target,
            acceptance_criteria=n.acceptance_criteria,
            repo_path=str(repo_root),
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

