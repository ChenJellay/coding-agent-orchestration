from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from agenti_helix.agents.registry import get_agent
from agenti_helix.core.ast_parser import parse_file
from agenti_helix.core.diff_builder import LinePatch, apply_line_patch_to_file
from agenti_helix.core.repo_map import generate_repo_map
from agenti_helix.core.repo_scanner import detect_language

# MLX-LM model id (Hugging Face) or local directory path.
#
#   export QWEN_MODEL_PATH="mlx-community/Qwen3.5-9B-MLX-4bit"
#
MODEL_PATH = os.environ.get("QWEN_MODEL_PATH", "mlx-community/Qwen3.5-9B-MLX-4bit")

_CACHED_MODEL: Any | None = None
_CACHED_TOKENIZER: Any | None = None
_CACHED_MODEL_ID: str | None = None


def _get_mlx_model() -> tuple[Any, Any]:
    global _CACHED_MODEL, _CACHED_TOKENIZER, _CACHED_MODEL_ID
    if _CACHED_MODEL is not None and _CACHED_TOKENIZER is not None and _CACHED_MODEL_ID == MODEL_PATH:
        return _CACHED_MODEL, _CACHED_TOKENIZER

    # Import MLX-related dependencies lazily so importing this module does not
    # hard-crash environments that can't import `mlx_lm` (e.g., test runners).
    try:
        import mlx_lm  # type: ignore
        from huggingface_hub.errors import (  # type: ignore
            GatedRepoError,
            HfHubHTTPError,
            RepositoryNotFoundError,
        )
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "mlx-lm dependencies could not be imported. "
            "This is required to run the local model, but tests can mock the caller.\n\n"
            f"Original error: {e}"
        ) from e

    try:
        model, tokenizer = mlx_lm.load(MODEL_PATH)
    except (RepositoryNotFoundError, GatedRepoError, HfHubHTTPError, FileNotFoundError) as e:  # type: ignore[name-defined]
        raise RuntimeError(
            "Failed to load Qwen MLX model via mlx-lm.\n\n"
            f"- QWEN_MODEL_PATH is set to: {MODEL_PATH!r}\n"
            "- If this is a Hugging Face repo id (e.g. 'mlx-community/Qwen3.5-9B-MLX-4bit'):\n"
            "    * Make sure the repo exists and is public, or\n"
            "    * Run `huggingface-cli login` or set HUGGINGFACE_HUB_TOKEN if it's gated/private.\n"
            "- You can also pre-download to a local folder and point QWEN_MODEL_PATH there.\n"
        ) from e
    _CACHED_MODEL = model
    _CACHED_TOKENIZER = tokenizer
    _CACHED_MODEL_ID = MODEL_PATH
    return model, tokenizer


@dataclass
class RepoMapFileView:
    path: str
    language: str
    symbols: Dict[str, Any]


def _build_repo_map_view(root: Path) -> List[RepoMapFileView]:
    repo_map = generate_repo_map(root)
    views: List[RepoMapFileView] = []
    for f in repo_map.files:
        views.append(
            RepoMapFileView(
                path=f.path,
                language=f.language,
                symbols=f.symbols,
            )
        )
    return views


def _repo_map_for_prompt(files: List[RepoMapFileView]) -> List[Dict[str, Any]]:
    return [
        {
            "path": f.path,
            "language": f.language,
            "symbols": f.symbols,
        }
        for f in files
    ]


def _build_prompt(intent: str, repo_files: List[RepoMapFileView]) -> str:
    agent = get_agent("coder_patch_v1")
    repo_map_snippet = json.dumps(_repo_map_for_prompt(repo_files), indent=2)
    return agent.render({"repo_map_json": repo_map_snippet, "intent": intent})


def _call_local_model(prompt: str) -> Dict[str, Any]:
    import mlx_lm  # type: ignore

    model, tokenizer = _get_mlx_model()
    make_sampler = getattr(mlx_lm, "make_sampler", None)
    if callable(make_sampler):
        sampler = make_sampler(temp=0.0)
        content = mlx_lm.generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=512,
            sampler=sampler,
        )
    else:
        content = mlx_lm.generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=512,
        )

    # Always log raw model output to help debug reasoning / formatting issues.
    print("\n===== Raw model output start =====")
    print(content)
    print("===== Raw model output end =====\n")

    start = content.find("{")
    if start == -1:
        raise ValueError(
            "Model did not return any JSON object (no '{' found).\n"
            f"Raw output:\n{content}"
        )

    depth = 0
    end = None
    for i in range(start, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end is None:
        raise ValueError(
            "Model output appears to start a JSON object but never closes it.\n"
            f"Raw output:\n{content}"
        )

    fragment = content[start : end + 1]
    try:
        return json.loads(fragment)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Failed to parse JSON from model output.\n"
            f"Extracted fragment:\n{fragment}\n\n"
            f"Full raw output:\n{content}"
        ) from e


def _validate_patch_json(
    patch_json: Dict[str, Any],
    repo_files: List[RepoMapFileView],
) -> LinePatch:
    required_keys = {"filePath", "startLine", "endLine", "replacementLines"}
    if not required_keys.issubset(patch_json):
        missing = required_keys - set(patch_json.keys())
        raise ValueError(f"Patch JSON missing keys: {', '.join(sorted(missing))}")

    file_path = str(patch_json["filePath"])
    allowed_paths = {f.path for f in repo_files}
    if file_path not in allowed_paths:
        raise ValueError(f"filePath {file_path} is not in Repo Map")

    start_line = int(patch_json["startLine"])
    end_line = int(patch_json["endLine"])
    replacement_lines = [str(l) for l in patch_json["replacementLines"]]

    return LinePatch(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        replacement_lines=replacement_lines,
    )


def _syntax_check_js_ts(path: Path) -> None:
    lang = detect_language(path)
    if lang not in ("javascript", "typescript"):
        return
    # Will raise on parse failure.
    parse_file(path, lang)


def run_single_agent_edit(
    repo_root: str | Path,
    intent: str,
) -> LinePatch:
    """
    End-to-end flow:
    - Generate Repo Map.
    - Prompt local model with Repo Map + intent.
    - Validate and apply patch.
    - Re-parse the file to ensure syntax is not broken.
    """
    root_path = Path(repo_root).resolve()
    repo_files = _build_repo_map_view(root_path)

    prompt = _build_prompt(intent, repo_files)
    patch_json = _call_local_model(prompt)

    agent = get_agent("coder_patch_v1")
    patch_typed = agent.output_model.model_validate(patch_json)
    patch = _validate_patch_json(patch_typed.model_dump(), repo_files)

    target_path = root_path / patch.file_path
    apply_line_patch_to_file(target_path, patch)

    _syntax_check_js_ts(target_path)

    return patch


def main() -> None:
    repo_root = Path("demo-repo").resolve()
    intent = "Change the button color in header.js to green."
    patch = run_single_agent_edit(repo_root, intent)
    print("Applied patch:")
    print(json.dumps(patch.__dict__, indent=2))


if __name__ == "__main__":
    main()

