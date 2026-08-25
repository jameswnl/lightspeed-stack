"""E2E test for POST /v1/agents/run with spawn=ephemeral.

Exercises run_agent_handler directly (same pattern as test_agents_e2e.py)
with a real "openshell" spawner_configuration, proving the fix for the
spawner=None bug: https://github.com/jameswnl/lightspeed-stack/issues/23.

Requires:
- OPENAI_API_KEY environment variable
- A running OpenShell gateway (default: localhost:17670, matching
  cloud_agents.spawner.factory's own default; override
  OPENSHELL_GATEWAY_URL=localhost:9080 for ~/ws/local-infra's
  Kind-deployed gateway -- see test_spawn_modes_e2e.py for setup)
- cloud_agents.spawner.factory.build_spawner (lightspeed-cloud-agents#182)
  available in the installed editable dependency. `uv sync` has been
  observed to silently drop the editable cloud-agents install (see
  ~/ws/lightspeed-stack CLAUDE.md), so this is skipped defensively
  rather than erroring if that's happened.

Usage:
    OPENSHELL_GATEWAY_URL=localhost:9080 \
    uv run pytest tests/e2e/cloud_agents/test_agents_ephemeral_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods

from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest
from fastapi import Request

from app.endpoints.agents import run_agent_handler
from authentication.interface import AuthTuple
from configuration import configuration
from models.api.requests.agents import AgentRunRequest
from models.config import Action

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
    pytest.mark.skipif(
        importlib.util.find_spec("cloud_agents.spawner.factory") is None,
        reason="cloud_agents.spawner.factory not installed -- "
        "re-run: uv pip install -e ~/ws/lightspeed-cloud-agents[local,kubernetes,openshell]",
    ),
]

_AUTH: AuthTuple = ("e2e-user", "e2e-tester", False, "")
_OPENSHELL_GATEWAY_URL = os.environ.get("OPENSHELL_GATEWAY_URL", "localhost:17670")
_SANDBOX_IMAGE = os.environ.get(
    "LIGHTSPEED_SANDBOX_IMAGE", "quay.io/jameswong/lightspeed-agentic-sandbox:latest"
)

_CONFIG = {
    "name": "e2e-agents-ephemeral-test",
    "service": {
        "host": "localhost",
        "port": 8080,
        "auth_enabled": False,
        "workers": 1,
    },
    "llama_stack": {
        "use_as_library_client": False,
        "url": "http://localhost:8321",
    },
    "user_data_collection": {"feedback_enabled": False},
    "authentication": {"module": "noop"},
    "spawner": {
        "type": "openshell",
        "openshell_gateway_url": _OPENSHELL_GATEWAY_URL,
        "sandbox_image": _SANDBOX_IMAGE,
    },
}


@pytest.fixture(name="e2e_config", scope="module")
def e2e_config_fixture() -> Any:
    """Load config with a real openshell spawner section.

    Skips (rather than erroring) if the gateway isn't reachable, same
    as test_spawn_modes_e2e.py's openshell_spawner fixture.
    """
    try:
        from openshell import SandboxClient

        SandboxClient(_OPENSHELL_GATEWAY_URL).health()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        pytest.skip(f"OpenShell gateway not available: {exc}")

    configuration.init_from_dict(_CONFIG)
    return configuration


@pytest.fixture(autouse=True)
def reset_spawner_singleton() -> Any:
    """Reset the module-level spawner singleton before and after each test."""
    from workflow.spawner_factory import reset_spawner

    reset_spawner()
    yield
    reset_spawner()


def _make_request() -> Request:
    """Create a minimal FastAPI Request with all actions authorized."""
    request = Request(
        scope={
            "type": "http",
            "query_string": b"",
            "headers": [],
        }
    )
    request.state.authorized_actions = set(Action)
    return request


class TestAgentRunEphemeralE2E:
    """POST /v1/agents/run with spawn=ephemeral, through the real handler."""

    @pytest.mark.asyncio
    async def test_ephemeral_spawn_executes_real_sandbox(self, e2e_config: Any) -> None:
        """spawn=ephemeral reaches a real OpenShell sandbox and returns output."""
        body = AgentRunRequest(
            prompt="What is 9+9? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
            spawn="ephemeral",
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert "18" in str(result["output"])
        assert result["transcript"]
