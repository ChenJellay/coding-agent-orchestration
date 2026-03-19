from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diff_builder import LinePatch, apply_line_patch_to_file
from .repo_map import generate_repo_map, save_repo_map


def _cmd_map(args: argparse.Namespace) -> int:
    root = args.root
    out = args.out

    repo_map = generate_repo_map(root)
    if out:
        save_repo_map(repo_map, out)
    else:
        sys.stdout.write(repo_map.to_json())
        sys.stdout.write("\n")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    file_path = args.file
    patch_json = args.patch
    patch_path = args.patch_file

    if (patch_json is None) == (patch_path is None):
        raise SystemExit("Provide exactly one of --patch or --patch-file")

    if patch_path is not None:
        text = Path(patch_path).read_text(encoding="utf8")
    else:
        text = patch_json

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid patch JSON: {exc}") from exc

    required_keys = {"filePath", "startLine", "endLine", "replacementLines"}
    if not required_keys.issubset(data):
        missing = required_keys - set(data.keys())
        raise SystemExit(f"Patch JSON missing keys: {', '.join(sorted(missing))}")

    if data["filePath"] != file_path:
        raise SystemExit("Patch filePath does not match --file argument")

    patch = LinePatch(
        file_path=file_path,
        start_line=int(data["startLine"]),
        end_line=int(data["endLine"]),
        replacement_lines=[str(line) for line in data["replacementLines"]],
    )

    apply_line_patch_to_file(file_path, patch)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agenti-helix", description="Agenti-Helix core CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    map_parser = subparsers.add_parser("map", help="Generate a Repo Map for a repository root")
    map_parser.add_argument("--root", type=str, default=".", help="Repository root directory")
    map_parser.add_argument("--out", type=str, default=None, help="Path to write Repo Map JSON (defaults to stdout)")
    map_parser.set_defaults(func=_cmd_map)

    diff_parser = subparsers.add_parser("diff", help="Apply a simple JSON line-based patch to a file")
    diff_parser.add_argument("--file", type=str, required=True, help="Target file path to patch")
    diff_parser.add_argument(
        "--patch",
        type=str,
        default=None,
        help="Inline JSON patch object with filePath, startLine, endLine, replacementLines",
    )
    diff_parser.add_argument(
        "--patch-file",
        type=str,
        default=None,
        help="Path to JSON file containing the patch object",
    )
    diff_parser.set_defaults(func=_cmd_diff)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())

