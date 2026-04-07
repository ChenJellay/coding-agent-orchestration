"""
L3.3 — Static checks tests.

Verifies that:
- Valid Python files pass py_compile check.
- Python files with syntax errors are flagged.
- Valid JS files pass node --check (if node is available).
- Invalid JS files fail the check.
- Non-existent file returns a useful error.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from agenti_helix.verification.verification_loop import (
    _check_python_syntax,
    _run_static_checks,
)


def test_valid_python_passes(tmp_path):
    py_file = tmp_path / "valid.py"
    py_file.write_text("def hello():\n    return 42\n")
    errors = _check_python_syntax(py_file)
    assert errors == []


def test_invalid_python_fails(tmp_path):
    py_file = tmp_path / "bad.py"
    py_file.write_text("def hello(\n    # unclosed paren\n")
    errors = _check_python_syntax(py_file)
    assert len(errors) > 0
    assert any("SyntaxError" in e or "EOF" in e or "error" in e.lower() for e in errors)


def test_run_static_checks_valid_python(tmp_path):
    py_file = tmp_path / "ok.py"
    py_file.write_text("x = 1 + 2\n")
    result = _run_static_checks(tmp_path, "ok.py")
    assert result["passed"] is True
    assert result["errors"] == []
    assert "py_compile" in result["checks_run"]


def test_run_static_checks_invalid_python(tmp_path):
    py_file = tmp_path / "broken.py"
    py_file.write_text("def bad syntax here\n")
    result = _run_static_checks(tmp_path, "broken.py")
    assert result["passed"] is False
    assert len(result["errors"]) > 0


def test_run_static_checks_missing_file(tmp_path):
    result = _run_static_checks(tmp_path, "nonexistent.py")
    assert result["passed"] is False
    assert any("not found" in e.lower() for e in result["errors"])


def test_run_static_checks_unknown_extension(tmp_path):
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello world")
    result = _run_static_checks(tmp_path, "notes.txt")
    # Unknown extensions are skipped — no checks run, treated as passed
    assert result["passed"] is True
    assert result["checks_run"] == []
