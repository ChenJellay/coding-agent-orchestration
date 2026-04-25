"""Tests for SWE-bench adapter helpers."""

from agenti_helix.evals.swebench_utils import first_relpath_from_unified_patch


def test_first_relpath_from_unified_patch_basic() -> None:
    patch = """diff --git a/sympy/core/sympify.py b/sympy/core/sympify.py
index 111..222 100644
--- a/sympy/core/sympify.py
+++ b/sympy/core/sympify.py
@@ -1 +1 @@
-x
+y
"""
    assert first_relpath_from_unified_patch(patch) == "sympy/core/sympify.py"


def test_first_relpath_from_unified_patch_tab_suffix() -> None:
    line = "+++ b/foo/bar.py\t1970-01-01 00:00:00.000000000 +0000"
    patch = f"--- a/foo/bar.py\n{line}\n"
    assert first_relpath_from_unified_patch(patch) == "foo/bar.py"


def test_first_relpath_from_unified_patch_empty() -> None:
    assert first_relpath_from_unified_patch("") is None
    assert first_relpath_from_unified_patch("no diff here") is None
