"""Small utilities for SWE-bench / harness adapters."""

from __future__ import annotations

from typing import Optional


def first_relpath_from_unified_patch(patch: str) -> Optional[str]:
    """Return the first ``+++ b/`` path from a unified diff (e.g. gold ``patch`` field), repo-relative."""
    if not patch or not isinstance(patch, str):
        return None
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            rest = line[6:].strip()
            if "\t" in rest:
                rest = rest.split("\t", 1)[0].strip()
            rest = rest.strip()
            return rest or None
    return None
