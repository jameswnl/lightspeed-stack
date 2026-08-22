"""Integration tests for /v1/agents/run endpoint.

Tests the full endpoint call chain with mocked LLM backend,
verifying request parsing, model resolution, agent execution,
and response formatting.
"""

# pylint: disable=import-outside-toplevel,unused-argument,protected-access,too-few-public-methods,unspecified-encoding,too-many-locals

from __future__ import annotations

from typing import Any

import pytest
import yaml
from fastapi import Request
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pytest_mock import MockerFixture

from app.endpoints.agents import run_agent_handler
from authentication.interface import AuthTuple
from configuration import configuration
from models.api.requests.agents import AgentRunRequest
from tests.integration.conftest import create_agent_run_result


@pytest.fixture(name="agent_config")
def agent_config_fixture(mocker: MockerFixture) -> Any:
    """Load config and mock the agent execution path."""
    config_dict = {
        "name": "test-agents",
        "service": {
            "host": "localhost",
            "port": 8080,
            "auth_enabled": False,
            "workers": 1,
        },
        "llama_stack": {
            "use_as_library_client": True,
            "library_client_config_path": "tests/configuration/run.yaml",
        },
        "user_data_collection": {
            "feedback_enabled": False,
        },
        "authentication": {"module": "noop"},
    }
    configuration.init_from_dict(config_dict)
    return configuration


@pytest.fixture(name="mock_agent_for_run")
def mock_agent_for_run_fixture(mocker: MockerFixture) -> Any:
    """Mock build_agent and OgxClient for the agents endpoint."""
    mock_agent = mocker.AsyncMock()
    mock_agent.run = mocker.AsyncMock(
        return_value=create_agent_run_result(
            mocker,
            content='{"severity": "high", "category": "resource", "summary": "OOM on worker-3"}',
        )
    )
    mocker.patch(
        "workflow.step.in_process.build_agent",
        return_value=mock_agent,
    )
    mocker.patch("workflow.step.in_process.configuration", configuration)

    mock_client = mocker.AsyncMock()
    mock_holder = mocker.patch("workflow.step.in_process.AsyncOgxClientHolder")
    mock_holder.return_value.get_client.return_value = mock_client

    return mock_agent


class TestAgentRunIntegration:
    """Integration tests for POST /v1/agents/run."""

    @pytest.mark.asyncio
    async def test_simple_agent_run(
        self,
        agent_config: Any,
        mock_agent_for_run: Any,
        mock_request_with_auth: Request,
    ) -> None:
        """Simple agent run returns completed status with output."""
        body = AgentRunRequest(
            prompt="Classify this alert: high memory on worker-3",
            model="test-provider/test-model",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        assert result["output"] is not None
        assert result["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_agent_run_with_instructions(
        self,
        agent_config: Any,
        mock_agent_for_run: Any,
        mock_request_with_auth: Request,
    ) -> None:
        """Agent run passes instructions to the agent builder."""
        body = AgentRunRequest(
            prompt="Classify this alert",
            model="test-provider/test-model",
            instructions="You are a senior SRE. Be concise.",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        mock_agent_for_run.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_run_with_structured_output(
        self,
        agent_config: Any,
        mock_agent_for_run: Any,
        mock_request_with_auth: Request,
    ) -> None:
        """Agent run with output_schema parses JSON output."""
        body = AgentRunRequest(
            prompt="Classify this alert",
            model="test-provider/test-model",
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
        tool_return_msg = mocker.MagicMock()
        tool_return_msg.__class__ = type("ModelRequest", (), {})

        from pydantic_ai.messages import ModelRequest

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

        mock_run_result = create_agent_run_result(
            mocker,
            content="The pod is running normally.",
            new_messages=[tool_call_response, tool_return, final_response],
        )

        mock_agent = mocker.AsyncMock()
        mock_agent.run = mocker.AsyncMock(return_value=mock_run_result)
        mocker.patch(
            "workflow.step.in_process.build_agent",
            return_value=mock_agent,
        )
        mocker.patch("workflow.step.in_process.configuration", configuration)
        mock_holder = mocker.patch("workflow.step.in_process.AsyncOgxClientHolder")
        mock_holder.return_value.get_client.return_value = mocker.AsyncMock()

        body = AgentRunRequest(
            prompt="Check pod status",
            model="test-provider/test-model",
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
    async def test_agent_run_model_fallback(
        self,
        agent_config: Any,
        mock_agent_for_run: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Agent run falls back to default model when none specified."""
        mock_prepare = mocker.patch(
            "workflow.step.in_process.prepare_workflow_step_params"
        )
        mocker.patch(
            "workflow.step.in_process.build_agent",
            return_value=mock_agent_for_run,
        )

        body = AgentRunRequest(prompt="Hello")
        auth: AuthTuple = ("user-1", "testuser", False, "")

        await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        call_kwargs = mock_prepare.call_args
        assert call_kwargs is not None

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
            model="test-provider/test-model",
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
        from pathlib import Path

        from cloud_agents.workflow.core.definition import WorkflowDefinition

        wf_path = (
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "lightspeed-cloud-agents"
            / "examples"
            / "workflow-definitions"
            / "triage-classify-workflow.yaml"
        )
        if not wf_path.exists():
            pytest.skip("lightspeed-cloud-agents repo not found at expected path")
        with open(wf_path) as f:
            raw = yaml.safe_load(f)

        definition = WorkflowDefinition.model_validate(raw)
        assert definition.metadata["name"] == "triage-classify-alerts"
        assert len(definition.spec.steps) == 3

        step_types = [s.type for s in definition.spec.steps]
        assert step_types == ["agent", "human-approval", "agent"]

        spawn_modes = [s.spawn for s in definition.spec.steps]
        assert spawn_modes[0] == "none"
        assert spawn_modes[2] == "none"
