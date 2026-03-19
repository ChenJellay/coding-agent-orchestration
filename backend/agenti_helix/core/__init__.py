"""
Core code-intelligence primitives.

Responsibilities:
- Scan repositories for supported source files.
- Extract top-level symbols/imports using AST parsing.
- Generate a compact “repo map” suitable for retrieval/prompting.
- Provide deterministic line-based patch primitives.

This module intentionally has no knowledge of orchestration or verification.
"""

from .diff_builder import LinePatch, apply_line_patch, apply_line_patch_to_file  # noqa: F401
from .repo_map import RepoMap, RepoMapFile, generate_repo_map, save_repo_map  # noqa: F401
from .repo_scanner import RepoFile, SupportedLanguage, detect_language, scan_repository  # noqa: F401

