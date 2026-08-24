"""Integration tests for /v1/agents/run endpoint.

Tests the full endpoint call chain with mocked step executor,
verifying request parsing, model resolution, and response formatting.
"""

# pylint: disable=import-outside-toplevel,unused-argument,protected-access,too-few-public-methods,unspecified-encoding

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import Request
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


def _mock_executor(mocker: MockerFixture, output: Any = None) -> Any:
    """Mock the step executor returned by get_step_executor."""
    mock_result = mocker.MagicMock()
    mock_result.status = "completed"
    mock_result.output = output or {"summary": "test output"}
    mock_result.error = None
    mock_result.transcript = [
        {"ts": "2024-01-01T00:00:00", "type": "result", "data": {"text": "done"}}
    ]
    mock_result.input_tokens = 50
    mock_result.output_tokens = 25
    mock_result.duration_ms = 500

    mock_exec = mocker.AsyncMock()
    mock_exec.run.return_value = mock_result
    mocker.patch(
        "app.endpoints.agents.get_step_executor",
        return_value=mock_exec,
    )
    return mock_exec


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
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        _mock_executor(mocker)

        body = AgentRunRequest(
            prompt="Classify this alert",
            provider="openai",
            model="gpt-4o-mini",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        assert result["output"]["summary"] == "test output"
        assert result["token_usage"]["input_tokens"] == 50
        assert result["duration_ms"] == 500

    @pytest.mark.asyncio
    async def test_structured_output_passthrough(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Structured output from executor is passed through."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        _mock_executor(
            mocker,
            output={"severity": "high", "category": "resource"},
        )

        body = AgentRunRequest(
            prompt="Classify this alert",
            provider="openai",
            model="gpt-4o-mini",
            output_schema={"type": "object"},
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert result["status"] == "completed"
        assert result["output"]["severity"] == "high"

    @pytest.mark.asyncio
    async def test_transcript_included(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Transcript events are included in the response."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        _mock_executor(mocker)

        body = AgentRunRequest(
            prompt="Check status",
            provider="openai",
            model="gpt-4o-mini",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        result = await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        assert len(result["transcript"]) == 1
        assert result["transcript"][0]["type"] == "result"

    @pytest.mark.asyncio
    async def test_step_input_construction(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """StepInput is constructed with correct provider and prompt."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_exec = _mock_executor(mocker)

        body = AgentRunRequest(
            prompt="Analyze this",
            provider="openai",
            model="gpt-4o",
            instructions="Be concise",
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        step_input = mock_exec.run.call_args[0][0]
        assert step_input.prompt == "Analyze this"
        assert step_input.provider == {"name": "openai", "model": "gpt-4o"}
        assert step_input.system_prompt == "Be concise"

    @pytest.mark.asyncio
    async def test_mcp_servers_passed_to_step_input(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """MCP server configs are passed through to StepInput."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_exec = _mock_executor(mocker)

        mcp_configs = [
            {"name": "kubectl", "url": "http://mcp-kubectl:8080/sse"},
            {
                "name": "github",
                "url": "http://mcp-github:8080/sse",
                "headers": {"Authorization": "Bearer token123"},
            },
        ]

        body = AgentRunRequest(
            prompt="List pods",
            provider="openai",
            model="gpt-4o-mini",
            mcp_servers=mcp_configs,
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        step_input = mock_exec.run.call_args[0][0]
        assert step_input.mcp_servers == mcp_configs
        assert len(step_input.mcp_servers) == 2
        assert (
            step_input.mcp_servers[1]["headers"]["Authorization"] == "Bearer token123"
        )

    @pytest.mark.asyncio
    async def test_tools_passed_to_step_input(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Registered tool names are passed through to StepInput."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_exec = _mock_executor(mocker)

        body = AgentRunRequest(
            prompt="Check the cluster",
            provider="openai",
            model="gpt-4o-mini",
            tools=["kubectl_get", "http_request"],
        )
        auth: AuthTuple = ("user-1", "testuser", False, "")

        await run_agent_handler.__wrapped__(mock_request_with_auth, body, auth)

        step_input = mock_exec.run.call_args[0][0]
        assert step_input.tools == ["kubectl_get", "http_request"]

    @pytest.mark.asyncio
    async def test_ephemeral_without_spawner_returns_400(
        self,
        agent_config: Any,
        mock_request_with_auth: Request,
        mocker: MockerFixture,
    ) -> None:
        """Requesting spawn=ephemeral without spawner config returns 400."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
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
