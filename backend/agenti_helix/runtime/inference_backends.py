from __future__ import annotations

import concurrent.futures
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol


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


def _mlx_stream_progress_interval() -> int:
    """How many tokens between llm_progress event writes (0 = disabled)."""
    return int(os.environ.get("AGENTI_HELIX_MLX_PROGRESS_INTERVAL", "50"))


def _mlx_enable_thinking() -> bool:
    """When True, use Qwen chat template with reasoning enabled and preserve `<redacted_thinking>` in output."""
    return os.environ.get("AGENTI_HELIX_ENABLE_THINKING", "").strip().lower() in {"1", "true", "yes", "on"}


def _openai_max_tokens_default() -> int:
    return int(os.environ.get("OPENAI_MAX_TOKENS", "8192"))


# ---------------------------------------------------------------------------
# Think-block stripping
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """Remove <think>…</think> blocks from model output and strip surrounding whitespace."""
    return _THINK_RE.sub("", text).strip()


def _apply_qwen_chat_template(prompt: str, tokenizer: Any, *, enable_thinking: bool) -> str:
    """Qwen3/3.5 chat template (``enable_thinking`` on/off); tokenizer fallback if unsupported."""
    try:
        messages = [{"role": "user", "content": prompt}]
        formatted: str = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
        )
        return formatted
    except TypeError:
        try:
            messages = [{"role": "user", "content": prompt}]
            formatted2: str = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            return formatted2
        except (TypeError, AttributeError):
            return prompt
    except AttributeError:
        return prompt


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class InferenceBackend(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        max_tokens: Optional[int],
        temperature: float,
        on_progress: Optional[Callable[[int, float, str], None]] = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# MLX local
# ---------------------------------------------------------------------------


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

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: Optional[int],
        temperature: float,
        on_progress: Optional[Callable[[int, float, str], None]] = None,
    ) -> str:
        """Run inference, returning only the answer (think blocks stripped).

        Key behaviours:
        - Wraps the prompt in a Qwen3/3.5 no-think chat template so the model
          skips extended reasoning and emits the answer directly.
        - Uses stream_generate so we can fire on_progress callbacks every
          AGENTI_HELIX_MLX_PROGRESS_INTERVAL tokens (default 50).
        - Enforces AGENTI_HELIX_MLX_TIMEOUT_SECONDS (default 300 s) by checking
          elapsed time inside the streaming loop, then raising TimeoutError.
        """
        import mlx_lm  # type: ignore

        model, tokenizer = self._get_mlx_model()
        mt = max_tokens if max_tokens is not None else _mlx_max_tokens_default()
        progress_interval = _mlx_stream_progress_interval()
        timeout = _mlx_inference_timeout()

        # Apply no-think template so Qwen3/3.5 skips the <think>…</think> block.
        use_thinking = _mlx_enable_thinking()
        formatted_prompt = _apply_qwen_chat_template(prompt, tokenizer, enable_thinking=use_thinking)

        make_sampler = getattr(mlx_lm, "make_sampler", None)
        sampler_kwargs: dict[str, Any] = {}
        if callable(make_sampler):
            sampler_kwargs["sampler"] = make_sampler(temp=float(temperature))

        def _stream() -> str:
            chunks: list[str] = []
            start = time.monotonic()
            last_progress = 0

            for response in mlx_lm.stream_generate(
                model,
                tokenizer,
                prompt=formatted_prompt,
                max_tokens=mt,
                **sampler_kwargs,
            ):
                chunks.append(response.text)
                n = response.generation_tokens

                # Wall-clock timeout check (fires mid-stream, not just at start).
                if timeout is not None and (time.monotonic() - start) > timeout:
                    raise TimeoutError(
                        f"MLX inference timed out after {timeout:.0f} s "
                        f"({n} tokens generated). "
                        "Increase AGENTI_HELIX_MLX_TIMEOUT_SECONDS or set to 0 to disable."
                    )

                # Periodic progress callback.
                if on_progress and progress_interval > 0 and n - last_progress >= progress_interval:
                    last_progress = n
                    snippet = "".join(chunks)[-120:]  # last 120 chars for a preview
                    on_progress(n, response.generation_tps, snippet)

            raw = "".join(chunks)
            if use_thinking:
                return raw.strip()
            return strip_think_blocks(raw)

        # Run the streaming loop in a background thread so the caller's thread
        # stays responsive and we can enforce the timeout from outside if needed.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_stream)
            try:
                # Give an extra 10 s grace over the in-loop timeout for cleanup.
                outer_timeout = (timeout + 10) if timeout is not None else None
                return future.result(timeout=outer_timeout)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(
                    f"MLX inference timed out after {timeout:.0f} s (outer guard). "
                    "Increase AGENTI_HELIX_MLX_TIMEOUT_SECONDS or set to 0 to disable."
                )


# ---------------------------------------------------------------------------
# OpenAI-compatible HTTP
# ---------------------------------------------------------------------------


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

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: Optional[int],
        temperature: float,
        on_progress: Optional[Callable[[int, float, str], None]] = None,
    ) -> str:
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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


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
