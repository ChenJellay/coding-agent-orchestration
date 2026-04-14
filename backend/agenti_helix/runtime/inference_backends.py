from __future__ import annotations

import concurrent.futures
import json
import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol


def _mlx_max_tokens_default() -> int:
    """Generation ceiling when callers omit max_tokens (read at call time for correct env/tests)."""
    return int(os.environ.get("AGENTI_HELIX_MLX_MAX_TOKENS", "262144"))


def _mlx_inference_timeout() -> Optional[float]:
    """Wall-clock timeout in seconds for a single MLX generate() call.

    Defaults to 300 s (5 min).  Set AGENTI_HELIX_MLX_TIMEOUT_SECONDS=0 to
    disable the timeout entirely.
    """
    raw = os.environ.get("AGENTI_HELIX_MLX_TIMEOUT_SECONDS", "300").strip()
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return 300.0


def _openai_max_tokens_default() -> int:
    return int(os.environ.get("OPENAI_MAX_TOKENS", "8192"))


class InferenceBackend(Protocol):
    def generate(self, prompt: str, *, max_tokens: Optional[int], temperature: float) -> str: ...


@dataclass(frozen=True)
class MLXModelConfig:
    model_path: str


_CACHED_MODEL: Any | None = None
_CACHED_TOKENIZER: Any | None = None
_CACHED_MODEL_ID: str | None = None


class MLXLocalInferenceBackend:
    def __init__(self, cfg: MLXModelConfig) -> None:
        self._cfg = cfg

    def _get_mlx_model(self) -> tuple[Any, Any]:
        global _CACHED_MODEL, _CACHED_TOKENIZER, _CACHED_MODEL_ID
        if (
            _CACHED_MODEL is not None
            and _CACHED_TOKENIZER is not None
            and _CACHED_MODEL_ID == self._cfg.model_path
        ):
            return _CACHED_MODEL, _CACHED_TOKENIZER

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
                "This is required to run the local model."
                f"\nOriginal error: {e}"
            ) from e

        try:
            model, tokenizer = mlx_lm.load(self._cfg.model_path)
        except (
            RepositoryNotFoundError,
            GatedRepoError,
            HfHubHTTPError,
            FileNotFoundError,
        ) as e:  # type: ignore[name-defined]
            raise RuntimeError(
                "Failed to load MLX model.\n"
                f"- model_path: {self._cfg.model_path!r}\n"
                f"Original error: {e}"
            ) from e

        _CACHED_MODEL = model
        _CACHED_TOKENIZER = tokenizer
        _CACHED_MODEL_ID = self._cfg.model_path
        return model, tokenizer

    def generate(self, prompt: str, *, max_tokens: Optional[int], temperature: float) -> str:
        # Import lazily so this module can be imported in environments without MLX deps.
        import mlx_lm  # type: ignore

        model, tokenizer = self._get_mlx_model()
        mt = max_tokens if max_tokens is not None else _mlx_max_tokens_default()
        make_sampler = getattr(mlx_lm, "make_sampler", None)

        def _run_generate() -> str:
            if callable(make_sampler):
                sampler = make_sampler(temp=float(temperature))
                return str(mlx_lm.generate(model, tokenizer, prompt=prompt, max_tokens=mt, sampler=sampler))
            # Older mlx_lm versions may not accept sampler.
            return str(mlx_lm.generate(model, tokenizer, prompt=prompt, max_tokens=mt))

        timeout = _mlx_inference_timeout()
        if timeout is None:
            return _run_generate()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_generate)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(
                    f"MLX inference timed out after {timeout:.0f} s. "
                    "Increase AGENTI_HELIX_MLX_TIMEOUT_SECONDS or set to 0 to disable."
                )


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    base_url: str


class OpenAIChatBackend:
    """
    OpenAI-compatible chat completion backend.

    Reads from environment variables by default:
      - OPENAI_API_KEY
      - OPENAI_MODEL  (default: gpt-4o-mini)
      - OPENAI_BASE_URL  (default: https://api.openai.com/v1)
    """

    def __init__(self, cfg: OpenAIConfig) -> None:
        self._cfg = cfg

    def generate(self, prompt: str, *, max_tokens: Optional[int], temperature: float) -> str:
        try:
            import httpx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("httpx is required for the OpenAI backend. Install with: pip install httpx") from exc

        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        mt = max_tokens if max_tokens is not None else _openai_max_tokens_default()
        body = {
            "model": self._cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": mt,
            "temperature": temperature,
        }
        url = self._cfg.base_url.rstrip("/") + "/chat/completions"
        response = httpx.post(url, headers=headers, json=body, timeout=120.0)
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"])


def get_default_inference_backend(runtime_cfg: Optional[dict[str, Any]] = None) -> InferenceBackend:
    """
    Choose an inference backend.

    `runtime_cfg` is intentionally permissive (JSON-safe) so future agent/model
    providers can be plugged in without changing the chain DSL.

    Supported backend_type values:
      - "mlx_local"  (default): local MLX model for *all* agents via QWEN_MODEL_PATH
        (or Hugging Face repo id; default id if unset: mlx-community/Qwen3.5-9B-MLX-4bit)
      - "openai": optional OpenAI-compatible HTTP API (OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL)
    """
    cfg = runtime_cfg or {}
    backend_type = str(cfg.get("backend_type") or os.environ.get("AGENTI_HELIX_BACKEND_TYPE") or "mlx_local")

    if backend_type == "mlx_local":
        model_path = str(cfg.get("model_path") or os.environ.get("QWEN_MODEL_PATH") or "mlx-community/Qwen3.5-9B-MLX-4bit")
        return MLXLocalInferenceBackend(MLXModelConfig(model_path=model_path))

    if backend_type == "openai":
        api_key = str(cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or "")
        if not api_key:
            raise ValueError(
                "OpenAI backend requires OPENAI_API_KEY environment variable or "
                "runtime_cfg['api_key'] to be set."
            )
        model = str(cfg.get("model") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")
        base_url = str(cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
        return OpenAIChatBackend(OpenAIConfig(api_key=api_key, model=model, base_url=base_url))

    raise ValueError(
        f"Unknown inference backend_type={backend_type!r}. "
        "Supported values: 'mlx_local', 'openai'."
    )

