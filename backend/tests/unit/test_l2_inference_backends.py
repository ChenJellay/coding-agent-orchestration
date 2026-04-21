"""
L2.1 — Inference backend tests.

Verifies that:
- get_default_inference_backend dispatches to MLXLocalInferenceBackend by default.
- get_default_inference_backend dispatches to OpenAIChatBackend for backend_type='openai'.
- OpenAI backend raises ValueError when api_key is missing.
- Unknown backend_type raises ValueError.
- AgentSpec.backend_type is respected by run_agent.
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# get_default_inference_backend dispatch
# ---------------------------------------------------------------------------

def test_default_backend_is_mlx_local():
    from agenti_helix.runtime.inference_backends import (
        MLXLocalInferenceBackend,
        get_default_inference_backend,
    )
    backend = get_default_inference_backend({})
    assert isinstance(backend, MLXLocalInferenceBackend)


def test_mlx_backend_uses_large_default_when_max_tokens_none(monkeypatch):
    """Omitting max_tokens should use AGENTI_HELIX_MLX_MAX_TOKENS (default 262144)."""
    pytest.importorskip("mlx_lm", reason="MLX not installed")
    import mlx_lm  # type: ignore
    from agenti_helix.runtime.inference_backends import MLXLocalInferenceBackend, MLXModelConfig

    monkeypatch.setenv("AGENTI_HELIX_MLX_MAX_TOKENS", "99999")
    captured: dict[str, int] = {}

    def fake_get_mlx(self: MLXLocalInferenceBackend):
        return object(), object()

    def fake_stream_generate(model, tokenizer, *, prompt: str, max_tokens: int, **kwargs):
        captured["max_tokens"] = max_tokens

        class _Chunk:
            text = "{}"
            generation_tokens = 1
            generation_tps = 0.0

        yield _Chunk()

    backend = MLXLocalInferenceBackend(MLXModelConfig(model_path="/fake/model"))
    with patch.object(MLXLocalInferenceBackend, "_get_mlx_model", fake_get_mlx):
        with patch.object(mlx_lm, "stream_generate", fake_stream_generate):
            backend.generate("hi", max_tokens=None, temperature=0.0)

    assert captured.get("max_tokens") == 99999


def test_openai_backend_requires_api_key():
    from agenti_helix.runtime.inference_backends import get_default_inference_backend

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        get_default_inference_backend({"backend_type": "openai"})


def test_openai_backend_created_with_key(monkeypatch):
    from agenti_helix.runtime.inference_backends import (
        OpenAIChatBackend,
        get_default_inference_backend,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    backend = get_default_inference_backend({"backend_type": "openai"})
    assert isinstance(backend, OpenAIChatBackend)


def test_unknown_backend_type_raises():
    from agenti_helix.runtime.inference_backends import get_default_inference_backend

    with pytest.raises(ValueError, match="Unknown inference backend_type"):
        get_default_inference_backend({"backend_type": "unknown_backend"})


def test_env_var_selects_backend(monkeypatch):
    """AGENTI_HELIX_BACKEND_TYPE env var should select the backend."""
    from agenti_helix.runtime.inference_backends import (
        MLXLocalInferenceBackend,
        get_default_inference_backend,
    )
    monkeypatch.setenv("AGENTI_HELIX_BACKEND_TYPE", "mlx_local")
    backend = get_default_inference_backend({})
    assert isinstance(backend, MLXLocalInferenceBackend)


# ---------------------------------------------------------------------------
# OpenAIChatBackend.generate
# ---------------------------------------------------------------------------

def test_openai_backend_generate(monkeypatch):
    """OpenAIChatBackend.generate should POST to the correct URL and return content."""
    import httpx
    from agenti_helix.runtime.inference_backends import OpenAIChatBackend, OpenAIConfig

    fake_response_data = {
        "choices": [{"message": {"content": "Hello from OpenAI"}}]
    }
    mock_response = MagicMock()
    mock_response.json.return_value = fake_response_data
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response) as mock_post:
        backend = OpenAIChatBackend(
            OpenAIConfig(api_key="sk-test", model="gpt-4o-mini", base_url="https://api.openai.com/v1")
        )
        result = backend.generate("Say hello", max_tokens=50, temperature=0.0)

    assert result == "Hello from OpenAI"
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "chat/completions" in call_kwargs[0][0]


# ---------------------------------------------------------------------------
# AgentSpec.backend_type
# ---------------------------------------------------------------------------

def test_agent_spec_backend_type_field():
    from agenti_helix.agents.registry import get_agent
    judge = get_agent("judge_v1")
    assert judge.backend_type == "mlx_local"


def test_agent_spec_render_generic_roster_agent():
    """Generic roster agents (no specialised render) should render without raising."""
    from agenti_helix.agents.registry import get_agent
    spec = get_agent("sdet_v1")
    # Render should not raise; it falls through to render_prompt with raw_input.
    # We patch load_prompt_template and render_prompt to avoid file I/O.
    with patch("agenti_helix.agents.registry.load_prompt_template", return_value="{intent}"):
        with patch("agenti_helix.agents.registry.render_prompt", return_value="rendered") as mock_render:
            result = spec.render({"intent": "write tests"})
    assert result == "rendered"
