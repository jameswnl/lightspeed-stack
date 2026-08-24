"""Unit tests for the query executor bridge."""

# pylint: disable=protected-access

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from workflow.query_executor import (
    _resolve_provider,
    _validate_prompt,
    execute_query_via_direct_executor,
    resolve_mcp_servers,
    stream_query_via_direct_executor,
)


class TestResolveMcpServers:
    """Tests for resolve_mcp_servers."""

    def test_resolves_all_servers(self, mocker: MockerFixture) -> None:
        """Resolves all configured MCP servers when no filter."""
        mock_server1 = mocker.MagicMock()
        mock_server1.name = "kubectl"
        mock_server1.url = "http://mcp-kubectl:8080/sse"
        mock_server1.resolved_authorization_headers = {}

        mock_server2 = mocker.MagicMock()
        mock_server2.name = "github"
        mock_server2.url = "http://mcp-github:8080/sse"
        mock_server2.resolved_authorization_headers = {
            "Authorization": "Bearer token123"
        }

        mock_config = mocker.patch("workflow.query_executor.configuration")
        mock_config.mcp_servers = [mock_server1, mock_server2]

        result = resolve_mcp_servers()

        assert len(result) == 2
        assert result[0] == {"name": "kubectl", "url": "http://mcp-kubectl:8080/sse"}
        assert result[1]["headers"]["Authorization"] == "Bearer token123"

    def test_filters_by_name(self, mocker: MockerFixture) -> None:
        """Only resolves named servers when filter is provided."""
        mock_server1 = mocker.MagicMock()
        mock_server1.name = "kubectl"
        mock_server1.url = "http://mcp-kubectl:8080/sse"
        mock_server1.resolved_authorization_headers = {}

        mock_server2 = mocker.MagicMock()
        mock_server2.name = "github"
        mock_server2.url = "http://mcp-github:8080/sse"
        mock_server2.resolved_authorization_headers = {}

        mock_config = mocker.patch("workflow.query_executor.configuration")
        mock_config.mcp_servers = [mock_server1, mock_server2]

        result = resolve_mcp_servers(server_names=["kubectl"])

        assert len(result) == 1
        assert result[0]["name"] == "kubectl"

    def test_empty_when_no_servers(self, mocker: MockerFixture) -> None:
        """Returns empty list when no servers configured."""
        mock_config = mocker.patch("workflow.query_executor.configuration")
        mock_config.mcp_servers = []

        result = resolve_mcp_servers()
        assert result == []

    def test_unknown_server_raises(self, mocker: MockerFixture) -> None:
        """Unknown server name raises ValueError."""
        mock_server = mocker.MagicMock()
        mock_server.name = "kubectl"
        mock_config = mocker.patch("workflow.query_executor.configuration")
        mock_config.mcp_servers = [mock_server]

        with pytest.raises(ValueError, match="Unknown MCP server"):
            resolve_mcp_servers(server_names=["nonexistent"])


class TestValidatePrompt:
    """Tests for _validate_prompt."""

    def test_valid_prompt(self) -> None:
        """Normal prompt passes validation."""
        _validate_prompt("Hello, world!")

    def test_prompt_too_long_raises(self) -> None:
        """Oversized prompt raises ValueError."""
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _validate_prompt("x" * 200_000)

    def test_instructions_too_long_raises(self) -> None:
        """Oversized instructions raises ValueError."""
        with pytest.raises(ValueError, match="Instructions exceed"):
            _validate_prompt("Hello", "x" * 100_000)


class TestResolveProvider:
    """Tests for _resolve_provider."""

    def test_explicit_values(self, mocker: MockerFixture) -> None:
        """Returns explicit provider and model."""
        mocker.patch("workflow.query_executor.configuration")
        result = _resolve_provider("openai", "gpt-4o-mini")
        assert result == {"name": "openai", "model": "gpt-4o-mini"}

    def test_falls_back_to_defaults(self, mocker: MockerFixture) -> None:
        """Falls back to config defaults."""
        mock_config = mocker.patch("workflow.query_executor.configuration")
        mock_config.inference.default_provider = "openai"
        mock_config.inference.default_model = "gpt-4o-mini"

        result = _resolve_provider(None, None)
        assert result == {"name": "openai", "model": "gpt-4o-mini"}

    def test_missing_both_raises(self, mocker: MockerFixture) -> None:
        """Raises ValueError when no provider/model and no defaults."""
        mock_config = mocker.patch("workflow.query_executor.configuration")
        mock_config.inference.default_provider = None
        mock_config.inference.default_model = None

        with pytest.raises(ValueError, match="Provider and model must be"):
            _resolve_provider(None, None)


class TestExecuteQueryViaChatWorkflowRunner:
    """Tests for execute_query_via_direct_executor."""

    @pytest.mark.asyncio
    async def test_executes_query(self, mocker: MockerFixture) -> None:
        """Sends message to ChatWorkflowRunner and returns result."""
        mock_result = mocker.MagicMock()
        mock_result.status = "completed"
        mock_result.output = {"response": "Hello"}
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_result.duration_ms = 500

        mock_runner = mocker.AsyncMock()
        mock_runner.start.return_value = "chat-abc123"
        mock_runner.send_message.return_value = mock_result

        mocker.patch(
            "workflow.query_executor._get_or_create_runner",
            return_value=mock_runner,
        )
        mocker.patch("workflow.query_executor.configuration").mcp_servers = []
        mocker.patch(
            "workflow.query_executor._resolve_provider",
            return_value={"name": "openai", "model": "gpt-4o-mini"},
        )

        result = await execute_query_via_direct_executor(
            prompt="Hello",
            provider="openai",
            model="gpt-4o-mini",
        )

        assert result.status == "completed"
        mock_runner.start.assert_called_once()
        mock_runner.send_message.assert_called_once_with("chat-abc123", "Hello")

    @pytest.mark.asyncio
    async def test_reuses_conversation_id(self, mocker: MockerFixture) -> None:
        """Uses provided conversation_id instead of starting new."""
        mock_result = mocker.MagicMock()
        mock_result.status = "completed"
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_result.duration_ms = 500

        mock_runner = mocker.AsyncMock()
        mock_runner.send_message.return_value = mock_result

        mocker.patch(
            "workflow.query_executor._get_or_create_runner",
            return_value=mock_runner,
        )
        mocker.patch("workflow.query_executor.configuration").mcp_servers = []
        mocker.patch(
            "workflow.query_executor._resolve_provider",
            return_value={"name": "openai", "model": "gpt-4o-mini"},
        )

        await execute_query_via_direct_executor(
            prompt="Hello",
            provider="openai",
            model="gpt-4o-mini",
            conversation_id="existing-conv",
        )

        mock_runner.start.assert_not_called()
        mock_runner.send_message.assert_called_once_with("existing-conv", "Hello")

    @pytest.mark.asyncio
    async def test_output_schema_raises(self, mocker: MockerFixture) -> None:
        """output_schema raises ValueError (not yet supported)."""
        mocker.patch("workflow.query_executor.configuration").mcp_servers = []
        mocker.patch(
            "workflow.query_executor._resolve_provider",
            return_value={"name": "openai", "model": "gpt-4o-mini"},
        )

        with pytest.raises(ValueError, match="not yet supported"):
            await execute_query_via_direct_executor(
                prompt="Hello",
                provider="openai",
                model="gpt-4o-mini",
                output_schema={"type": "object"},
            )


class TestStreamQueryViaChatWorkflowRunner:
    """Tests for stream_query_via_direct_executor."""

    @pytest.mark.asyncio
    async def test_yields_stream_events(self, mocker: MockerFixture) -> None:
        """Yields events from ChatWorkflowRunner.send_message_stream."""
        from cloud_agents.workflow.executor.step.base import StreamEvent

        mock_events = [
            StreamEvent(type="token", data={"delta": "Hello"}),
            StreamEvent(type="complete", data={}, result=mocker.MagicMock()),
        ]

        async def mock_stream(_wf_id: str, _prompt: str):  # type: ignore[no-untyped-def]
            for event in mock_events:
                yield event

        mock_runner = mocker.AsyncMock()
        mock_runner.start.return_value = "chat-stream-1"
        mock_runner.send_message_stream = mock_stream

        mocker.patch(
            "workflow.query_executor._get_or_create_runner",
            return_value=mock_runner,
        )
        mocker.patch("workflow.query_executor.configuration").mcp_servers = []
        mocker.patch(
            "workflow.query_executor._resolve_provider",
            return_value={"name": "openai", "model": "gpt-4o-mini"},
        )

        events = []
        async for event in stream_query_via_direct_executor(
            prompt="Hello",
            provider="openai",
            model="gpt-4o-mini",
        ):
            events.append(event)

        assert len(events) == 2
        assert events[0].type == "token"
        assert events[1].type == "complete"

    @pytest.mark.asyncio
    async def test_reuses_conversation_id(self, mocker: MockerFixture) -> None:
        """Streaming with conversation_id skips start()."""
        from cloud_agents.workflow.executor.step.base import StreamEvent

        async def mock_stream(_wf_id: str, _prompt: str):  # type: ignore[no-untyped-def]
            yield StreamEvent(type="complete", data={}, result=mocker.MagicMock())

        mock_runner = mocker.AsyncMock()
        mock_runner.send_message_stream = mock_stream

        mocker.patch(
            "workflow.query_executor._get_or_create_runner",
            return_value=mock_runner,
        )
        mocker.patch("workflow.query_executor.configuration").mcp_servers = []
        mocker.patch(
            "workflow.query_executor._resolve_provider",
            return_value={"name": "openai", "model": "gpt-4o-mini"},
        )

        async for _ in stream_query_via_direct_executor(
            prompt="Hello",
            provider="openai",
            model="gpt-4o-mini",
            conversation_id="existing-stream-conv",
        ):
            pass

        mock_runner.start.assert_not_called()
