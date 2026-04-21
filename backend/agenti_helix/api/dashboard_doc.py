"""Persist dashboard-uploaded documentation for doc-first (product_eng) runs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_MAX_DOC_CHARS = 400_000


def resolve_dashboard_doc_url(
    *,
    repo_path: str,
    dag_id: str,
    doc_url: Optional[str] = None,
    doc_text: Optional[str] = None,
    doc_filename: Optional[str] = None,
) -> str:
    """
    If `doc_text` is non-empty, write it under `<repo>/.agenti_helix/` and return a file:// URI.
    Otherwise return stripped `doc_url` (may be empty).
    """
    text = (doc_text or "").strip()
    url = (doc_url or "").strip()
    if text:
        if len(text) > _MAX_DOC_CHARS:
            raise ValueError(f"doc_text exceeds {_MAX_DOC_CHARS} characters")
        repo = Path(repo_path).expanduser().resolve()
        agenti = repo / ".agenti_helix"
        agenti.mkdir(parents=True, exist_ok=True)
        ext = _safe_ext(doc_filename)
        safe_dag = re.sub(r"[^a-zA-Z0-9._-]+", "_", dag_id).strip("_")[:80] or "run"
        out = agenti / f"dashboard_doc_{safe_dag}{ext}"
        out.write_text(text, encoding="utf-8")
        return out.resolve().as_uri()
    return url


def _safe_ext(filename: Optional[str]) -> str:
    if not filename:
        return ".md"
    suf = Path(filename).suffix.lower()
    if suf in (".md", ".txt", ".markdown"):
        return suf
    return ".md"
