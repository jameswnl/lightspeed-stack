"""Tests for the LIGHTSPEED_E2E_USE_MOCK_LLM env-var-gated context manager.

conftest.py's autouse fixture is a thin wrapper around
mock_llm_env_context() -- tested here directly so the gating/restore logic
has real coverage without resorting to pytest-in-pytest (pytester).
"""

from __future__ import annotations

import os

import httpx
import pytest

from .mock_llm_env import MOCK_LLM_ENV_VAR, mock_llm_env_context


def test_noop_when_env_var_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the toggle unset, OPENAI_BASE_URL is left completely untouched."""
    monkeypatch.delenv(MOCK_LLM_ENV_VAR, raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with mock_llm_env_context():
        assert "OPENAI_BASE_URL" not in os.environ

    assert "OPENAI_BASE_URL" not in os.environ


def test_redirects_and_restores_openai_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the toggle set, OPENAI_BASE_URL points at a live mock, then is restored."""
    monkeypatch.setenv(MOCK_LLM_ENV_VAR, "1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://original.example.com/v1")

    with mock_llm_env_context():
        redirected = os.environ["OPENAI_BASE_URL"]
        assert redirected != "https://original.example.com/v1"
        result = httpx.post(
            f"{redirected}/responses",
            json={"model": "gpt-4o", "input": "hi"},
            timeout=5.0,
        )
        assert result.status_code == 200

    assert os.environ["OPENAI_BASE_URL"] == "https://original.example.com/v1"


def test_pops_openai_base_url_when_previously_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If OPENAI_BASE_URL wasn't set before, it's removed again on exit."""
    monkeypatch.setenv(MOCK_LLM_ENV_VAR, "1")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with mock_llm_env_context():
        assert "OPENAI_BASE_URL" in os.environ

    assert "OPENAI_BASE_URL" not in os.environ
