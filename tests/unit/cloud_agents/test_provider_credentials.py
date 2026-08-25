"""Unit tests for provider-to-credentials-env-var resolution."""

from __future__ import annotations

import pytest

from workflow.provider_credentials import credentials_secret_for


@pytest.mark.parametrize(
    ("provider_name", "expected_env_key"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("claude", "ANTHROPIC_API_KEY"),
        ("gemini", "GOOGLE_API_KEY"),
        ("azure", "AZURE_OPENAI_API_KEY"),
    ],
)
def test_credentials_secret_for_known_providers(
    provider_name: str, expected_env_key: str
) -> None:
    """Known providers resolve to their conventional API key env var."""
    assert credentials_secret_for(provider_name) == expected_env_key


@pytest.mark.parametrize("provider_name", ["", "bedrock", "not-a-real-provider"])
def test_credentials_secret_for_unknown_providers_returns_none(
    provider_name: str,
) -> None:
    """Unknown/empty providers return None rather than guessing OpenAI.

    Regression test: a prior version defaulted unrecognized providers to
    "OPENAI_API_KEY", which would silently stamp the wrong env var name
    onto a sandbox for any non-OpenAI (or misspelled) provider.
    """
    assert credentials_secret_for(provider_name) is None
