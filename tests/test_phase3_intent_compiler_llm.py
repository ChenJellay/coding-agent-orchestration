from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import agenti_helix.orchestration.intent_compiler as ic


def test_compile_macro_intent_with_llm_uses_service_response(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    src = repo / "src" / "components"
    src.mkdir(parents=True)
    (src / "header.js").write_text("console.log('header');\n")

    macro_intent = "High-level feature description."

    class DummyResponse:
        def __init__(self, payload: Dict[str, Any]) -> None:
            self._payload = payload

        def read(self) -> bytes:
            import json

            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self) -> "DummyResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
            return None

    def fake_urlopen(request, timeout: float = 0.0):  # type: ignore[override]
        payload = {
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
        return DummyResponse(payload)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agenti_helix.orchestration.intent_compiler.urllib.request.urlopen", fake_urlopen)

    spec = ic.compile_macro_intent_with_llm(
        macro_intent,
        repo_path=str(repo),
        dag_id="dag-ignored",
        base_url="http://localhost:8000/intent-compiler",
        timeout_seconds=5.0,
    )

    assert spec.dag_id == "dag-llm-test"
    assert list(spec.nodes.keys()) == ["A", "B"]
    assert spec.edges == [("A", "B")]
    assert spec.nodes["A"].task.target_file == "src/components/header.js"
    assert macro_intent in spec.nodes["A"].task.intent

