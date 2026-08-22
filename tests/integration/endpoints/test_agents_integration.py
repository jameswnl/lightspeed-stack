"""Integration tests for /v1/agents/run endpoint.

Tests the full endpoint call chain with mocked pydantic-ai Agent,
verifying request parsing, model resolution, agent execution,
and response formatting.
"""

# pylint: disable=import-outside-toplevel,unused-argument,protected-access,too-few-public-methods,unspecified-encoding,too-many-locals

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import Request
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pytest_mock import MockerFixture

from app.endpoints.agents import run_agent_handler
from authentication.interface import AuthTuple
from configuration import configuration
from models.api.requests.agents import AgentRunRequest


@pytest.fixture(name="agent_config")
def agent_config_fixture() -> Any:
    """Load minimal config for agent tests."""
    config_dict = {
        "name": "test-agents",
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
    configuration.init_from_dict(config_dict)
    return configuration


def _mock_agent_run(mocker: MockerFixture, output: str = "test output") -> Any:
    """Create a mocked pydantic-ai Agent that returns the given output."""
    mock_run_result = mocker.MagicMock()
    mock_run_result.output = output
    mock_run_result.new_messages.return_value = []
    mock_run_result.usage = mocker.MagicMock()
    mock_run_result.usage.input_tokens = 50
    mock_run_result.usage.output_tokens = 25

    mock_agent = mocker.AsyncMock()
    mock_agent.run.return_value = mock_run_result

    mocker.patch(
        "workflow.step.in_process.Agent",
        return_value=mock_agent,
    )
    return mock_agent


class TestAgentRunIntegration:
    """Integration tests for POST /v1/agents/run."""

    @pytest.mark.asyncio
    async def test_simple_agent_run(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Simple agent run returns completed status with output."""
        _mock_agent_run(mocker, '{"severity": "high", "summary": "OOM"}')

        body = AgentRunRequest(
            prompt="Classify this alert",
            provider="openai",
            model="gpt-4o-mini",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        assert result["output"] is not None
        assert result["duration_ms"] >= 0
        assert result["token_usage"]["input_tokens"] == 50

    @pytest.mark.asyncio
    async def test_agent_run_with_instructions(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Agent run passes instructions to the agent."""
        mock_agent = _mock_agent_run(mocker)

        body = AgentRunRequest(
            prompt="Classify this alert",
            provider="openai",
            model="gpt-4o-mini",
            instructions="You are a senior SRE. Be concise.",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        mock_agent.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_run_with_structured_output(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Agent run with output_schema parses JSON output."""
        _mock_agent_run(
            mocker,
            '{"severity": "high", "category": "resource", "summary": "OOM"}',
        )

        body = AgentRunRequest(
            prompt="Classify this alert",
            provider="openai",
            model="gpt-4o-mini",
            output_schema={
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "category": {"type": "string"},
                },
            },
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        assert result["output"]["severity"] == "high"
        assert result["output"]["category"] == "resource"

    @pytest.mark.asyncio
    async def test_agent_run_with_tool_calls(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Agent run captures tool calls in transcript."""
        tool_call_response = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="kubectl",
                    args={"command": "get pods -n production"},
                    tool_call_id="tc-1",
                ),
            ],
        )
        tool_return = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="kubectl",
                    content="NAME  STATUS\npod-1  Running",
                    tool_call_id="tc-1",
                )
            ]
        )
        final_response = ModelResponse(
            parts=[TextPart(content="The pod is running normally.")],
        )

        mock_run_result = mocker.MagicMock()
        mock_run_result.output = "The pod is running normally."
        mock_run_result.new_messages.return_value = [
            tool_call_response,
            tool_return,
            final_response,
        ]
        mock_run_result.usage = mocker.MagicMock()
        mock_run_result.usage.input_tokens = 100
        mock_run_result.usage.output_tokens = 50

        mock_agent = mocker.AsyncMock()
        mock_agent.run.return_value = mock_run_result
        mocker.patch(
            "workflow.step.in_process.Agent",
            return_value=mock_agent,
        )

        body = AgentRunRequest(
            prompt="Check pod status",
            provider="openai",
            model="gpt-4o-mini",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        transcript = result["transcript"]
        tool_calls = [
            e
            for e in transcript
            if (e.get("type") if isinstance(e, dict) else e.type) == "tool_call"
        ]
        assert len(tool_calls) >= 1

    @pytest.mark.asyncio
    async def test_ephemeral_without_spawner_returns_400(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
    ) -> None:
        """Requesting spawn=ephemeral without spawner config returns 400."""
        from fastapi import HTTPException

        body = AgentRunRequest(
            prompt="Fix the issue",
            provider="openai",
            model="gpt-4o-mini",
            spawn="ephemeral",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        with pytest.raises(HTTPException) as exc_info:
            await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)
        assert exc_info.value.status_code == 400


class TestWorkflowDefinitionExecution:
    """Test that cloud-agents workflow definitions are compatible."""

    def test_triage_classify_definition_parses(self) -> None:
        """The triage-classify workflow YAML parses into a valid definition."""
        from cloud_agents.workflow.core.definition import WorkflowDefinition

        wf_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "lightspeed-cloud-agents"
            / "examples"
            / "workflow-definitions"
            / "triage-classify-workflow.yaml"
        )
        if not wf_path.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")
        with open(wf_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        definition = WorkflowDefinition.model_validate(raw)
        assert definition.metadata["name"] == "triage-classify-alerts"
        assert len(definition.spec.steps) == 3

        step_types = [s.type for s in definition.spec.steps]
        assert step_types == ["agent", "human-approval", "agent"]
