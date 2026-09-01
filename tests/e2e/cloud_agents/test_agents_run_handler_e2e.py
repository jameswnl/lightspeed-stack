"""Handler-direct e2e tests for POST /v1/agents/run.

Calls `run_agent_handler.__wrapped__(...)` directly, bypassing the
`@authorize` decorator and FastAPI routing/request-validation entirely --
see test_agents_run_http_e2e.py for the real-HTTP equivalent of this
file's spawn=none/local/ephemeral coverage, and test_step_executor_e2e.py
for coverage one layer further down (the step-executor dispatch itself,
bypassing this handler too).

Runs against a real LLM backend (OpenAI via pydantic-ai). Requires
OPENAI_API_KEY. spawn=ephemeral tests additionally require a reachable
OpenShell gateway (OPENSHELL_GATEWAY_URL, default localhost:17670) and
`cloud_agents.spawner.factory` installed, and are marked `ephemeral` so CI
can deselect them with `-m "not ephemeral"`.

Usage:
    uv run pytest tests/e2e/cloud_agents/test_agents_run_handler_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods,unused-argument

from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest
from pydantic import ValidationError

from app.endpoints.agents import run_agent_handler
from configuration import configuration
from models.api.requests.agents import AgentRunRequest

from .conftest import AUTH, make_request, skip_if_gateway_unreachable

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)

_OPENSHELL_GATEWAY_URL = os.environ.get("OPENSHELL_GATEWAY_URL", "localhost:17670")
_SANDBOX_IMAGE = os.environ.get(
    "LIGHTSPEED_SANDBOX_IMAGE", "quay.io/jameswong/lightspeed-agentic-sandbox:latest"
)

_CONFIG_NONE = {
    "name": "e2e-agents-run-handler-test",
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
}

_CONFIG_EPHEMERAL = {
    **_CONFIG_NONE,
    "name": "e2e-agents-run-handler-ephemeral-test",
    "spawner": {
        "type": "openshell",
        "openshell_gateway_url": _OPENSHELL_GATEWAY_URL,
        "sandbox_image": _SANDBOX_IMAGE,
    },
}


@pytest.fixture(name="e2e_config", scope="module")
def e2e_config_fixture() -> Any:
    """Load config for handler-direct tests with no spawner section.

    No Llama Stack needed -- pydantic-ai talks to OpenAI directly.
    """
    configuration.init_from_dict(_CONFIG_NONE)
    return configuration


@pytest.fixture(name="e2e_config_ephemeral")
def e2e_config_ephemeral_fixture() -> Any:
    """Load config with a real openshell spawner section.

    Skips (rather than erroring) if the gateway isn't reachable, same as
    test_step_executor_e2e.py's openshell_spawner fixture.

    Note: like `e2e_config`, this mutates the global `configuration`
    singleton -- harmless in practice (spawn=none tests never read the
    spawner section, and `ephemeral` is normally deselected in CI), but
    worth knowing if test isolation here ever becomes a real problem.
    """
    skip_if_gateway_unreachable()
    configuration.init_from_dict(_CONFIG_EPHEMERAL)
    return configuration


@pytest.fixture(autouse=True)
def reset_spawner_singleton() -> Any:
    """Reset the module-level spawner singleton before and after each test."""
    from workflow.spawner_factory import (
        reset_spawner,
    )  # pylint: disable=import-outside-toplevel

    reset_spawner()
    yield
    reset_spawner()


class TestAgentRunE2E:
    """E2E tests for /v1/agents/run with real LLM calls, spawn=none."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, e2e_config: Any) -> None:
        """Agent returns a text response to a simple prompt."""
        body = AgentRunRequest(
            prompt="What is 2 + 2? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        assert result["output"] is not None
        assert "4" in str(result["output"])
        assert result["token_usage"]["input_tokens"] > 0
        assert result["token_usage"]["output_tokens"] > 0
        assert result["duration_ms"] > 0

    @pytest.mark.asyncio
    async def test_with_instructions(self, e2e_config: Any) -> None:
        """Agent follows system instructions."""
        body = AgentRunRequest(
            prompt="What is the capital of France?",
            provider="openai",
            model="gpt-4o-mini",
            instructions="You are a geography expert. Answer in exactly one word.",
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        summary = str(result["output"]).lower()
        assert "paris" in summary

    @pytest.mark.asyncio
    async def test_structured_output(self, e2e_config: Any) -> None:
        """Agent returns structured JSON matching the output schema."""
        body = AgentRunRequest(
            prompt=(
                "An alert fired: 'High CPU on node worker-1: 98% for 10 minutes.' "
                "Classify the severity and category. Reply with JSON only."
            ),
            provider="openai",
            model="gpt-4o-mini",
            instructions=(
                "You classify infrastructure alerts. "
                "Reply ONLY with a JSON object matching the schema, no markdown."
            ),
            output_schema={
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "category": {
                        "type": "string",
                        "enum": ["resource", "network", "storage", "security"],
                    },
                    "summary": {"type": "string"},
                },
                "required": ["severity", "category", "summary"],
            },
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        output = result["output"]
        assert isinstance(output, dict)
        assert len(output) >= 2
        assert any(k in output for k in ("severity", "category", "summary"))

    @pytest.mark.asyncio
    async def test_provider_model_resolution(self, e2e_config: Any) -> None:
        """Model ID with separate provider field resolves correctly."""
        body = AgentRunRequest(
            prompt="Say 'hello' and nothing else.",
            provider="openai",
            model="gpt-4o-mini",
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        assert "hello" in str(result["output"]).lower()

    @pytest.mark.asyncio
    async def test_multi_step_context_passing(self, e2e_config: Any) -> None:
        """Agent receives prior context and uses it."""
        body = AgentRunRequest(
            prompt=(
                "The previous analysis found severity=high and category=resource. "
                "Based on that, recommend ONE action in a single sentence."
            ),
            provider="openai",
            model="gpt-4o-mini",
            instructions="You are an SRE. Be concise.",
            context={
                "analysis": {
                    "status": "completed",
                    "output": {
                        "severity": "high",
                        "category": "resource",
                        "summary": "Node worker-1 at 98% CPU for 10 min",
                    },
                }
            },
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        assert len(str(result["output"])) > 10

    @pytest.mark.asyncio
    async def test_different_model_produces_response(self, e2e_config: Any) -> None:
        """A different model still produces a valid response."""
        body = AgentRunRequest(
            prompt="What color is the sky? One word.",
            provider="openai",
            model="gpt-4o-mini",
            instructions="Reply with exactly one word.",
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        assert "blue" in str(result["output"]).lower()

    def test_agent_run_missing_prompt_returns_422(self) -> None:
        """Missing required 'prompt' field raises a pydantic ValidationError.

        AgentRunRequest is the request model for POST /v1/agents/run --
        FastAPI turns this into a 422 at the real HTTP layer (not exercised
        here, no handler/HTTP involved -- pure request-model validation).
        """
        with pytest.raises(ValidationError):
            AgentRunRequest(provider="openai", model="gpt-4o-mini")  # type: ignore[call-arg]


class TestAgentRunLocalSpawnE2E:
    """POST /v1/agents/run with spawn=local, through the real handler.

    No output_schema here: the cloud-agents SubprocessExecutor behind
    spawn=local has no native structured-output mode yet
    (jameswnl/lightspeed-cloud-agents#235).
    """

    @pytest.mark.asyncio
    async def test_local_spawn_executes_in_subprocess(self, e2e_config: Any) -> None:
        """spawn=local runs in a child process and returns real LLM output."""
        body = AgentRunRequest(
            prompt="What is 9+9? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
            spawn="local",
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        assert "18" in str(result["output"])
        assert result["transcript"]


@pytest.mark.ephemeral
@pytest.mark.skipif(
    importlib.util.find_spec("cloud_agents.spawner.factory") is None,
    reason="cloud_agents.spawner.factory not installed -- "
    "re-run: uv pip install -e ~/ws/lightspeed-cloud-agents[local,kubernetes,openshell]",
)
class TestAgentRunEphemeralE2E:
    """POST /v1/agents/run with spawn=ephemeral, through the real handler.

    Proves the fix for the spawner=None bug:
    https://github.com/jameswnl/lightspeed-stack/issues/23
    """

    @pytest.mark.asyncio
    async def test_ephemeral_spawn_executes_real_sandbox(
        self, e2e_config_ephemeral: Any
    ) -> None:
        """spawn=ephemeral reaches a real OpenShell sandbox and returns output."""
        body = AgentRunRequest(
            prompt="What is 9+9? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
            spawn="ephemeral",
        )

        result = await run_agent_handler.__wrapped__(make_request(), body, AUTH)

        assert result["status"] == "completed"
        assert "18" in str(result["output"])
        assert result["transcript"]
