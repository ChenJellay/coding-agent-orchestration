"""Smoke test for the LLM-driven intent compiler.

The compiler now runs the intent_compiler chain locally (via `run_chain`)
rather than POSTing to an external service, so the test stubs `run_chain`
inside the intent_compiler module to return a canned compiler payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import agenti_helix.orchestration.intent_compiler as ic


def test_compile_macro_intent_with_llm_uses_chain_response(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")

    macro_intent = "High-level feature description."

    canned_payload: Dict[str, Any] = {
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

    def fake_run_chain(*, chain_spec, initial_context, **_kwargs):
        return {**initial_context, "intent_compiler_output": canned_payload}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ic, "run_chain", fake_run_chain)

    spec = ic.compile_macro_intent_with_llm(
        macro_intent,
        repo_path=str(repo),
        dag_id="dag-ignored",
    )

    assert spec.dag_id == "dag-llm-test"
    assert list(spec.nodes.keys()) == ["A", "B"]
    assert spec.edges == [("A", "B")]
    assert spec.nodes["A"].task.target_file == "src/components/header.js"
    assert macro_intent in spec.nodes["A"].task.intent
