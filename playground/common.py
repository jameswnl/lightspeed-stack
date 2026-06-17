"""Shared model configuration for playground scripts.

Supports two backends via the PLAYGROUND_PROVIDER env var:

    # Ollama (default) — local, no API key needed
    uv run python playground/try_rhoso_upgrade.py

    # OpenAI — set your API key
    PLAYGROUND_PROVIDER=openai OPENAI_API_KEY=sk-... uv run python playground/try_rhoso_upgrade.py

    # Override model name
    PLAYGROUND_MODEL=gpt-4o uv run python playground/try_rhoso_upgrade.py

Environment variables:
    PLAYGROUND_PROVIDER  "ollama" (default) or "openai"
    PLAYGROUND_MODEL     Model name (default: qwen3.6:latest for ollama, gpt-4o-mini for openai)
    OLLAMA_URL           Ollama base URL (default: http://localhost:11434/v1)
    OPENAI_API_KEY       Required when PLAYGROUND_PROVIDER=openai
"""

import os

from pydantic_ai.models.openai import OpenAIResponsesModel

from pydantic_ai_lightspeed.llamastack import LlamaStackProvider

PROVIDER = os.environ.get("PLAYGROUND_PROVIDER", "ollama")

_DEFAULTS = {
    "ollama": {"model": "qwen3.6:latest", "base_url": "http://localhost:11434/v1"},
    "openai": {"model": "gpt-4o-mini"},
}


def make_model() -> OpenAIResponsesModel:
    """Create a model using the configured provider.

    Returns an OpenAIResponsesModel backed by either a local Ollama instance
    (via LlamaStackProvider) or the OpenAI API directly.
    """
    defaults = _DEFAULTS.get(PROVIDER, _DEFAULTS["ollama"])
    model_name = os.environ.get("PLAYGROUND_MODEL", defaults["model"])

    if PROVIDER == "openai":
        return OpenAIResponsesModel(model_name)

    base_url = os.environ.get("OLLAMA_URL", defaults["base_url"])
    provider = LlamaStackProvider(base_url=base_url)
    return OpenAIResponsesModel(model_name, provider=provider)
