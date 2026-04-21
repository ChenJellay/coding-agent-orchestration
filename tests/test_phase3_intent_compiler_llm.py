"""Smoke test for the LLM-driven intent compiler.

The compiler now runs the intent_compiler chain locally (via `run_chain`)
rather than POSTing to an external service, so the test stubs `run_chain`
inside the intent_compiler module to return a canned compiler payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import agenti_helix.orchestration.intent_compiler as ic


def _make_canned_payload() -> Dict[str, Any]:
    return {
        "dag_id": "dag-llm-test",
        "nodes": [
            {
                "node_id": "A",
                "description": "First step",
                "target_file": "src/components/header.js",
                "acceptance_criteria": "A criteria",
            },
            {
                "node_id": "B",
                "description": "Second step",
                "target_file": "src/components/header.js",
                "acceptance_criteria": "B criteria",
            },
        ],
        "edges": [["A", "B"]],
    }


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")
    return repo


def _install_chain_stub(monkeypatch, payload: Dict[str, Any]) -> None:
    def fake_run_chain(*, chain_spec, initial_context, **_kwargs):
        return {**initial_context, "intent_compiler_output": payload}

    monkeypatch.setattr(ic, "run_chain", fake_run_chain)


def test_compile_macro_intent_with_llm_uses_chain_response(monkeypatch, tmp_path: Path) -> None:
    """The parsed DAG shape (nodes, edges, target file, intent) must round-trip.

    ``dag_id`` precedence is covered separately below; here we omit the
    caller-supplied id so the LLM-returned ``dag-llm-test`` wins.
    """
    repo = _setup_repo(tmp_path)
    macro_intent = "High-level feature description."
    monkeypatch.chdir(tmp_path)
    _install_chain_stub(monkeypatch, _make_canned_payload())

    spec = ic.compile_macro_intent_with_llm(
        macro_intent,
        repo_path=str(repo),
    )

    assert spec.dag_id == "dag-llm-test"
    assert list(spec.nodes.keys()) == ["A", "B"]
    assert spec.edges == [("A", "B")]
    assert spec.nodes["A"].task.target_file == "src/components/header.js"
    assert macro_intent in spec.nodes["A"].task.intent


def test_caller_supplied_dag_id_overrides_llm_payload(monkeypatch, tmp_path: Path) -> None:
    """Dashboard / CLI ``dag_id`` must win over the LLM's ``dag_id``.

    Otherwise a single run persists two different ``*.json`` specs (one under
    the caller's id, one under the LLM's) and the UI shows duplicate DAGs.
    The precedence flip is intentional (see ``intent_compiler.py`` comment).
    """
    repo = _setup_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    _install_chain_stub(monkeypatch, _make_canned_payload())

    spec = ic.compile_macro_intent_with_llm(
        "High-level feature description.",
        repo_path=str(repo),
        dag_id="dashboard-supplied-id",
    )

    assert spec.dag_id == "dashboard-supplied-id"
