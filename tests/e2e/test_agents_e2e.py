"""E2E tests for /v1/agents/run endpoint.

Runs against a real LLM backend (OpenAI via Llama Stack library mode).
Requires OPENAI_API_KEY environment variable.

Usage:
    uv run pytest tests/e2e/test_agents_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods,unspecified-encoding,unused-argument

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import Request

from app.endpoints.agents import run_agent_handler
from authentication.interface import AuthTuple
from configuration import configuration
from models.api.requests.agents import AgentRunRequest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)

_AUTH: AuthTuple = ("e2e-user", "e2e-tester", False, "")

_CONFIG = {
    "name": "e2e-agents-test",
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
    "user_data_collection": {
        "feedback_enabled": False,
    },
    "authentication": {"module": "noop"},
}


@pytest.fixture(name="e2e_config", scope="module")
def e2e_config_fixture() -> Any:
    """Load config for E2E tests.

    No Llama Stack needed — pydantic-ai talks to OpenAI directly.
    Requires OPENAI_API_KEY environment variable.
    """
    configuration.init_from_dict(_CONFIG)
    return configuration


def _make_request() -> Request:
    """Create a minimal FastAPI Request with all actions authorized."""
    from models.config import Action

    request = Request(
        scope={
            "type": "http",
            "query_string": b"",
            "headers": [],
        }
    )
    request.state.authorized_actions = set(Action)
    return request


class TestAgentRunE2E:
    """E2E tests for /v1/agents/run with real LLM calls."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, e2e_config: Any) -> None:
        """Agent returns a text response to a simple prompt."""
        body = AgentRunRequest(
            prompt="What is 2 + 2? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert result["output"] is not None
        assert "4" in result["output"].get("summary", "")
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

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        summary = result["output"].get("summary", "").lower()
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

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

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

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert "hello" in result["output"].get("summary", "").lower()

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

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert len(result["output"].get("summary", "")) > 10


class TestWorkflowDefinitionCompatibility:
    """Test that cloud-agents workflow definitions parse correctly."""

    def test_all_spawn_none_definitions_parse(self) -> None:
        """All workflow definitions with spawn=none steps parse correctly."""
        from cloud_agents.workflow.core.definition import WorkflowDefinition

        wf_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "lightspeed-cloud-agents"
            / "examples"
            / "workflow-definitions"
        )
        if not wf_dir.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")

        for wf_file in wf_dir.glob("*.yaml"):
            with open(wf_file) as f:
                raw = yaml.safe_load(f)

            definition = WorkflowDefinition.model_validate(raw)
            assert definition.spec.steps, f"{wf_file.name}: no steps"

            none_steps = [s for s in definition.spec.steps if s.spawn == "none"]
            for step in none_steps:
                assert step.name, f"{wf_file.name}: step missing name"
                assert step.output_key, f"{wf_file.name}: step missing output_key"
