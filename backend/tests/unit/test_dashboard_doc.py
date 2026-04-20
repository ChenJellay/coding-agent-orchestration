"""Dashboard documentation attachment for product_eng (doc-first) runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenti_helix.api.dashboard_doc import resolve_dashboard_doc_url
from agenti_helix.api.task_commands_routes import _DASHBOARD_LLM_INTENT_MODES
from agenti_helix.runtime.tools import tool_fetch_doc_content


def test_resolve_dashboard_doc_writes_under_agenti_helix(tmp_path: Path) -> None:
    uri = resolve_dashboard_doc_url(
        repo_path=str(tmp_path),
        dag_id="dag-ui-run-123",
        doc_text="# Spec\n\nDo the thing.\n",
        doc_filename="prd.md",
    )
    assert uri.startswith("file:")
    written = tmp_path / ".agenti_helix" / "dashboard_doc_dag-ui-run-123.md"
    assert written.is_file()
    assert "Do the thing" in written.read_text(encoding="utf-8")


def test_resolve_dashboard_doc_url_only(tmp_path: Path) -> None:
    out = resolve_dashboard_doc_url(
        repo_path=str(tmp_path),
        dag_id="x",
        doc_url=" https://example.com/spec ",
        doc_text=None,
    )
    assert out == "https://example.com/spec"


def test_resolve_dashboard_doc_text_oversize(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds"):
        resolve_dashboard_doc_url(
            repo_path=str(tmp_path),
            dag_id="x",
            doc_text="x" * 400_001,
        )


def test_tool_fetch_doc_content_reads_local_file_uri(tmp_path: Path) -> None:
    spec = tmp_path / "notes.md"
    spec.write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    uri = spec.resolve().as_uri()
    out = tool_fetch_doc_content(repo_root=str(tmp_path), doc_url=uri)
    assert out["fetch_error"] == ""
    assert "Hello" in out["doc_content"]
    assert out["doc_title"] == "notes.md"


def test_dashboard_forces_llm_intent_for_product_eng() -> None:
    assert "product_eng" in _DASHBOARD_LLM_INTENT_MODES
    assert "patch" not in _DASHBOARD_LLM_INTENT_MODES


def test_tool_fetch_doc_content_rejects_file_outside_repo(tmp_path: Path) -> None:
    other = tmp_path.parent / "outside.md"
    other.write_text("secret", encoding="utf-8")
    uri = other.resolve().as_uri()
    out = tool_fetch_doc_content(repo_root=str(tmp_path), doc_url=uri)
    assert "inside repo_root" in (out["fetch_error"] or "").lower()
