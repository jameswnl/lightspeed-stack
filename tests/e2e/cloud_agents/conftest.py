"""Shared fixtures for cloud-agents e2e tests.

See mock_llm_env.py for the LIGHTSPEED_E2E_USE_MOCK_LLM gating logic this
autouse fixture wraps.

LIGHTSPEED_E2E_USE_MOCK_LLM is only safe for test_agents_workflow_http_e2e.py
(plus this module's own self-tests) -- that file's assertions are
structural only (status/key-presence), by design, so a canned response
satisfies them. Every other file in this directory
(test_agents_e2e.py, test_spawn_modes_e2e.py's TestSpawnNone/TestSpawnLocal,
etc.) asserts real-world semantic content (e.g. "paris" in output) that
only a real LLM can produce -- running those with the mock active fails
confusingly, not because of a real bug. Since this fixture is autouse at
session scope, point pytest at the specific compatible file(s) rather than
the whole tests/e2e/cloud_agents/ directory when the mock is enabled (see
the CI job in .github/workflows/cloud_agents_tests.yaml and the
test-e2e-agents-workflows-mock Makefile target for the supported scope).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from .mock_llm_env import mock_llm_env_context


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server_env() -> Generator[None, None, None]:
    """Redirect OPENAI_BASE_URL to a local mock server when opted in."""
    with mock_llm_env_context():
        yield
