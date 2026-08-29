"""Env-var-gated context manager redirecting OPENAI_BASE_URL to a mock.

Separated from conftest.py so the gating/restore logic is directly unit
testable without resorting to pytest-in-pytest.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from .mock_llm_server import MockResponsesServer

MOCK_LLM_ENV_VAR = "LIGHTSPEED_E2E_USE_MOCK_LLM"


@contextmanager
def mock_llm_env_context() -> Iterator[None]:
    """No-op unless LIGHTSPEED_E2E_USE_MOCK_LLM is set.

    When set, starts a MockResponsesServer and points OPENAI_BASE_URL at
    it for the duration of the context, restoring the previous value (or
    removing it, if it wasn't set) on exit -- so spawn=none/local e2e
    tests hit the mock in CI, and real OpenAI otherwise.
    """
    if not os.environ.get(MOCK_LLM_ENV_VAR):
        yield
        return

    server = MockResponsesServer()
    server.start()
    original = os.environ.get("OPENAI_BASE_URL")
    os.environ["OPENAI_BASE_URL"] = server.base_url
    try:
        yield
    finally:
        if original is not None:
            os.environ["OPENAI_BASE_URL"] = original
        else:
            os.environ.pop("OPENAI_BASE_URL", None)
        server.stop()
