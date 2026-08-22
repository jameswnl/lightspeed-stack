"""Unit tests for the /v1/agents/run endpoint."""

# pylint: disable=protected-access,import-outside-toplevel,unused-argument

from __future__ import annotations

from typing import Any

import pytest
from pytest_mock import MockerFixture

from app.endpoints.agents import run_agent_handler
from models.api.requests.agents import AgentRunRequest


@pytest.fixture(name="mock_config")
def mock_config_fixture(mocker: MockerFixture) -> Any:
    """Mock the configuration singleton."""
    mock_cfg = mocker.patch("app.endpoints.agents.configuration")
    mock_cfg.inference = mocker.MagicMock()
    mock_cfg.inference.default_model = "gpt-4o"
    mock_cfg.inference.default_provider = "openai"
    mock_cfg.spawner_configuration = None
    return mock_cfg


@pytest.fixture(name="mock_executor")
def mock_executor_fixture(mocker: MockerFixture) -> Any:
    """Mock the step executor."""
    mock_result = mocker.MagicMock()
    mock_result.status = "completed"
    mock_result.output = {"summary": "Done"}
    mock_result.error = None
    mock_result.transcript = []
    mock_result.input_tokens = 50
    mock_result.output_tokens = 25
    mock_result.duration_ms = 1000

    mock_exec = mocker.AsyncMock()
    mock_exec.run.return_value = mock_result

    mocker.patch(
        "app.endpoints.agents.get_step_executor",
        return_value=mock_exec,
    )
    return mock_exec


class TestRunAgentHandler:
    """Tests for run_agent_handler."""

    @pytest.mark.asyncio
    async def test_successful_run(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Successful agent run returns result."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(
            prompt="Analyze the cluster",
            model="openai/gpt-4o",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await run_agent_handler.__wrapped__(request, body, auth)

        assert result["status"] == "completed"
        assert result["output"] == {"summary": "Done"}
        assert result["token_usage"]["input_tokens"] == 50

    @pytest.mark.asyncio
    async def test_uses_default_model(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Falls back to default model when not specified."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mocker.patch(
            "app.endpoints.agents.get_step_executor",
            return_value=mock_executor,
        )

        body = AgentRunRequest(prompt="Hello")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert "gpt-4o" in call_args.provider.get("model", "")

    @pytest.mark.asyncio
    async def test_ephemeral_without_spawner_raises(
        self,
        mocker: MockerFixture,
        mock_config: Any,
    ) -> None:
        """spawn=ephemeral without spawner config raises 400."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(
            prompt="Fix the issue",
            spawn="ephemeral",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await run_agent_handler.__wrapped__(request, body, auth)

        assert exc_info.value.status_code == 400
