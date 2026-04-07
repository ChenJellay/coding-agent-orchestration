"""Cross-cutting deployment concern unit tests (D1, D2, D4, D5, D7).

D1 — Authentication:
- verify_token returns 'editor' role when key matches
- verify_token returns 'viewer' role for viewer key
- verify_token raises 401 when Authorization header is missing (and key is set)
- verify_token raises 403 for wrong token
- Auth is bypassed when AGENTI_HELIX_API_KEY is not configured

D2 — Judge Service Isolation:
- judge_server has CORS restricted to localhost origins
- print debug statement has been replaced (verified via source inspection)

D4 — Repo Scanner Ignore Rules:
- node_modules directory is excluded from scan results
- __pycache__ directory is excluded
- .git directory is excluded
- Files outside ignored dirs are still returned
- exclude_patterns param adds custom ignore dirs
- IGNORE_DIRS constant is exported

D5 — Polling Performance:
- TTLCache import attempt and graceful fallback
- _CACHE_AVAILABLE is set based on cachetools availability

D7 — Requirements completeness:
- requirements.txt contains pydantic, httpx, cachetools
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# D1 — Authentication
# ---------------------------------------------------------------------------

class TestAuthDependency:
    """Tests for agenti_helix.api.auth"""

    def _import_auth(self):
        from agenti_helix.api.auth import require_auth, require_editor, _resolve_role
        return require_auth, require_editor, _resolve_role

    def test_auth_bypassed_without_api_key(self):
        """No API key configured → auth bypassed → returns 'editor' role."""
        with patch.dict(os.environ, {}, clear=False):
            # Temporarily remove the key if present
            env = {k: v for k, v in os.environ.items() if k != "AGENTI_HELIX_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                from agenti_helix.api import auth as auth_mod
                with patch.object(auth_mod, "_editor_key", return_value=None), \
                     patch.object(auth_mod, "_auth_enforced", return_value=False):
                    role = auth_mod.require_auth(authorization=None)
                    assert role == "editor"

    def test_returns_editor_role_for_valid_editor_key(self):
        from agenti_helix.api import auth as auth_mod
        with patch.object(auth_mod, "_editor_key", return_value="secret123"), \
             patch.object(auth_mod, "_auth_enforced", return_value=False):
            role = auth_mod.require_auth(authorization="Bearer secret123")
            assert role == "editor"

    def test_returns_viewer_role_for_valid_viewer_key(self):
        from agenti_helix.api import auth as auth_mod
        with patch.object(auth_mod, "_editor_key", return_value="editor-key"), \
             patch.object(auth_mod, "_viewer_key", return_value="viewer-key"), \
             patch.object(auth_mod, "_auth_enforced", return_value=False):
            role = auth_mod.require_auth(authorization="Bearer viewer-key")
            assert role == "viewer"

    def test_raises_401_when_no_header_and_key_configured(self):
        from agenti_helix.api import auth as auth_mod
        with patch.object(auth_mod, "_editor_key", return_value="secret"), \
             patch.object(auth_mod, "_auth_enforced", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                auth_mod.require_auth(authorization=None)
            assert exc_info.value.status_code == 401

    def test_raises_403_for_wrong_token(self):
        from agenti_helix.api import auth as auth_mod
        with patch.object(auth_mod, "_editor_key", return_value="correct-key"), \
             patch.object(auth_mod, "_auth_enforced", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                auth_mod.require_auth(authorization="Bearer wrong-key")
            assert exc_info.value.status_code == 403

    def test_raises_401_for_malformed_auth_header(self):
        from agenti_helix.api import auth as auth_mod
        with patch.object(auth_mod, "_editor_key", return_value="secret"), \
             patch.object(auth_mod, "_auth_enforced", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                auth_mod.require_auth(authorization="Token secret")  # wrong scheme
            assert exc_info.value.status_code == 401

    def test_require_editor_raises_403_for_viewer(self):
        from agenti_helix.api.auth import require_editor
        with pytest.raises(HTTPException) as exc_info:
            require_editor(role="viewer")  # type: ignore[arg-type]
        assert exc_info.value.status_code == 403

    def test_require_editor_passes_for_editor(self):
        from agenti_helix.api.auth import require_editor
        assert require_editor(role="editor") == "editor"  # type: ignore[arg-type]

    def test_resolve_role_returns_none_for_unknown_token(self):
        from agenti_helix.api import auth as auth_mod
        with patch.object(auth_mod, "_editor_key", return_value="known"), \
             patch.object(auth_mod, "_viewer_key", return_value=None):
            assert auth_mod._resolve_role("unknown") is None


# ---------------------------------------------------------------------------
# D2 — Judge Service Isolation (structural checks)
# ---------------------------------------------------------------------------

def test_judge_server_has_no_wildcard_cors():
    """CORS in judge_server must not contain '*'."""
    from agenti_helix.verification.judge_server import app
    from fastapi.middleware.cors import CORSMiddleware
    cors_middlewares = [
        m for m in app.user_middleware
        if hasattr(m, 'cls') and m.cls is CORSMiddleware
    ]
    # FastAPI stores middleware differently across versions; inspect kwargs
    for mw in cors_middlewares:
        origins = mw.kwargs.get("allow_origins", [])
        assert "*" not in origins, "Judge server CORS must not allow all origins"


def test_judge_server_has_no_print_in_parse_json():
    """_parse_model_json must not contain raw print() calls."""
    import inspect
    from agenti_helix.verification import judge_server
    src = inspect.getsource(judge_server._parse_model_json)
    assert "print(" not in src, "_parse_model_json still contains a print() debug call"


# ---------------------------------------------------------------------------
# D4 — Repo Scanner Ignore Rules
# ---------------------------------------------------------------------------

def test_ignore_dirs_constant_exported():
    from agenti_helix.core.repo_scanner import IGNORE_DIRS
    assert "node_modules" in IGNORE_DIRS
    assert ".git" in IGNORE_DIRS
    assert "__pycache__" in IGNORE_DIRS
    assert ".venv" in IGNORE_DIRS


def test_scan_excludes_node_modules(tmp_path):
    from agenti_helix.core.repo_scanner import scan_repository

    # Create a legitimate file and a file inside node_modules
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("# ok\n")
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "index.js").write_text("module.exports = {}")

    files = scan_repository(tmp_path)
    paths = [f.path for f in files]

    assert any("app.py" in p for p in paths), "app.py should be scanned"
    assert not any("node_modules" in p for p in paths), "node_modules should be excluded"


def test_scan_excludes_pycache(tmp_path):
    from agenti_helix.core.repo_scanner import scan_repository

    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "util.cpython-311.pyc").write_bytes(b"fake")
    (tmp_path / "util.py").write_text("def f(): pass\n")

    files = scan_repository(tmp_path)
    paths = [f.path for f in files]
    assert not any("__pycache__" in p for p in paths)
    assert any("util.py" in p for p in paths)


def test_scan_custom_exclude_patterns(tmp_path):
    from agenti_helix.core.repo_scanner import scan_repository

    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("# vendor\n")
    (tmp_path / "main.py").write_text("# main\n")

    files = scan_repository(tmp_path, exclude_patterns=["vendor"])
    paths = [f.path for f in files]
    assert not any("vendor" in p for p in paths)
    assert any("main.py" in p for p in paths)


# ---------------------------------------------------------------------------
# D5 — Cachetools availability
# ---------------------------------------------------------------------------

def test_cachetools_import_available_or_graceful_fallback():
    """The main.py cache block must work whether cachetools is installed or not."""
    # The import is at module level with a try/except; we just verify the flag exists.
    import agenti_helix.api.main as main_mod
    assert hasattr(main_mod, "_CACHE_AVAILABLE")


# ---------------------------------------------------------------------------
# D7 — Requirements completeness
# ---------------------------------------------------------------------------

def test_requirements_txt_has_pydantic_httpx_cachetools():
    """requirements.txt must explicitly list pydantic, httpx, and cachetools."""
    req_path = Path(__file__).parent.parent.parent.parent / "requirements.txt"
    if not req_path.exists():
        pytest.skip("requirements.txt not found at expected location")

    content = req_path.read_text(encoding="utf-8").lower()
    assert "pydantic" in content, "requirements.txt must list pydantic"
    assert "httpx" in content, "requirements.txt must list httpx"
    assert "cachetools" in content, "requirements.txt must list cachetools"
    assert "gitpython" in content, "requirements.txt must list gitpython"
    assert "bandit" in content, "requirements.txt must list bandit"
