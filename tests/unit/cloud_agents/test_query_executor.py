"""Unit tests for the query executor bridge."""

# pylint: disable=protected-access

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from workflow.query_executor import (
    _load_conversation_context,
    _save_conversation_turn,
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


class TestExecuteQueryViaDirectExecutor:
    """Tests for execute_query_via_direct_executor."""

    @pytest.mark.asyncio
    async def test_executes_with_provider(self, mocker: MockerFixture) -> None:
        """Passes provider and model to executor."""
        mock_result = mocker.MagicMock()
        mock_result.status = "completed"
        mock_result.output = {"response": "Done"}

        mock_exec = mocker.AsyncMock()
        mock_exec.run.return_value = mock_result
        mocker.patch(
            "workflow.query_executor.get_step_executor",
            return_value=mock_exec,
        )
        mocker.patch("workflow.query_executor.configuration").mcp_servers = []

        result = await execute_query_via_direct_executor(
            prompt="Hello",
            model="gpt-4o-mini",
            provider="openai",
        )

        assert result.status == "completed"
        step_input = mock_exec.run.call_args[0][0]
        assert step_input.provider == {
            "name": "openai",
            "model": "gpt-4o-mini",
        }

    @pytest.mark.asyncio
    async def test_includes_mcp_servers(self, mocker: MockerFixture) -> None:
        """Resolves and passes MCP servers to executor."""
        mock_result = mocker.MagicMock()
        mock_result.status = "completed"

        mock_exec = mocker.AsyncMock()
        mock_exec.run.return_value = mock_result
        mocker.patch(
            "workflow.query_executor.get_step_executor",
            return_value=mock_exec,
        )

        mock_server = mocker.MagicMock()
        mock_server.name = "kubectl"
        mock_server.url = "http://mcp:8080/sse"
        mock_server.resolved_authorization_headers = {}
        mocker.patch("workflow.query_executor.configuration").mcp_servers = [
            mock_server
        ]

        await execute_query_via_direct_executor(
            prompt="List pods",
            provider="openai",
            model="gpt-4o-mini",
        )

        step_input = mock_exec.run.call_args[0][0]
        assert step_input.mcp_servers is not None
        assert len(step_input.mcp_servers) == 1
        assert step_input.mcp_servers[0]["name"] == "kubectl"

    @pytest.mark.asyncio
    async def test_missing_provider_and_model_raises(
        self, mocker: MockerFixture
    ) -> None:
        """Raises ValueError when no provider/model and no defaults."""
        mock_config = mocker.patch("workflow.query_executor.configuration")
        mock_config.mcp_servers = []
        mock_config.inference = mocker.MagicMock()
        mock_config.inference.default_provider = None
        mock_config.inference.default_model = None

        with pytest.raises(ValueError, match="Provider and model must be"):
            await execute_query_via_direct_executor(prompt="Hello")

    @pytest.mark.asyncio
    async def test_prompt_too_long_raises(self, mocker: MockerFixture) -> None:
        """Raises ValueError when prompt exceeds max length."""
        mocker.patch("workflow.query_executor.configuration")

        with pytest.raises(ValueError, match="exceeds maximum length"):
            await execute_query_via_direct_executor(
                prompt="x" * 200_000,
                provider="openai",
                model="gpt-4o-mini",
            )


class TestLoadConversationContext:
    """Tests for _load_conversation_context."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_store_unavailable(
        self, mocker: MockerFixture
    ) -> None:
        """Returns empty dict when TranscriptStore is not initialized."""
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            side_effect=RuntimeError("not initialized"),
        )
        result = await _load_conversation_context("conv-1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_turns(self, mocker: MockerFixture) -> None:
        """Returns empty dict when no prior turns exist."""
        mock_store = mocker.AsyncMock()
        mock_store.load_recent_turns.return_value = []
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            return_value=mock_store,
        )
        result = await _load_conversation_context("conv-1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_loads_conversation_history(self, mocker: MockerFixture) -> None:
        """Returns conversation history from prior turns."""
        mock_store = mocker.AsyncMock()
        mock_store.load_recent_turns.return_value = [
            {
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there"},
                ]
            }
        ]
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            return_value=mock_store,
        )
        result = await _load_conversation_context("conv-1")
        assert "conversation_history" in result
        assert len(result["conversation_history"]) == 2
        assert result["conversation_history"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_skips_invalid_messages(self, mocker: MockerFixture) -> None:
        """Skips messages missing role or content fields."""
        mock_store = mocker.AsyncMock()
        mock_store.load_recent_turns.return_value = [
            {
                "messages": [
                    {"role": "user", "content": "Valid"},
                    {"bad_field": "no role or content"},
                    {"role": "assistant"},
                ]
            }
        ]
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            return_value=mock_store,
        )
        result = await _load_conversation_context("conv-1")
        assert len(result["conversation_history"]) == 1

    @pytest.mark.asyncio
    async def test_handles_store_errors_gracefully(self, mocker: MockerFixture) -> None:
        """Returns empty dict on store errors."""
        mock_store = mocker.AsyncMock()
        mock_store.load_recent_turns.side_effect = Exception("db error")
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            return_value=mock_store,
        )
        result = await _load_conversation_context("conv-1")
        assert result == {}


class TestSaveConversationTurn:
    """Tests for _save_conversation_turn."""

    @pytest.mark.asyncio
    async def test_saves_turn_to_store(self, mocker: MockerFixture) -> None:
        """Saves user prompt and assistant response as a turn."""
        mock_store = mocker.AsyncMock()
        mock_store.list_steps.return_value = ["turn-0"]
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            return_value=mock_store,
        )

        mock_result = mocker.MagicMock()
        mock_result.output = {"response": "Hello back"}
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_result.duration_ms = 500

        await _save_conversation_turn("conv-1", "Hello", mock_result)

        mock_store.save.assert_called_once()
        call_kwargs = mock_store.save.call_args[1]
        assert call_kwargs["workflow_id"] == "conv-1"
        assert call_kwargs["step_name"].startswith("turn-")
        assert len(call_kwargs["messages"]) == 2

    @pytest.mark.asyncio
    async def test_skips_when_store_unavailable(self, mocker: MockerFixture) -> None:
        """Does nothing when TranscriptStore is not initialized."""
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            side_effect=RuntimeError("not initialized"),
        )
        mock_result = mocker.MagicMock()
        await _save_conversation_turn("conv-1", "Hello", mock_result)

    @pytest.mark.asyncio
    async def test_handles_save_errors_gracefully(self, mocker: MockerFixture) -> None:
        """Logs warning on save failure, doesn't raise."""
        mock_store = mocker.AsyncMock()
        mock_store.list_steps.return_value = []
        mock_store.save.side_effect = Exception("db write failed")
        mocker.patch(
            "workflow.query_executor.WorkflowStorageFactory.get_transcript_store",
            return_value=mock_store,
        )

        mock_result = mocker.MagicMock()
        mock_result.output = {"response": "test"}
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_result.duration_ms = 100

        await _save_conversation_turn("conv-1", "Hello", mock_result)


class TestStreamQueryViaDirectExecutor:
    """Tests for stream_query_via_direct_executor."""

    @pytest.mark.asyncio
    async def test_yields_stream_events(self, mocker: MockerFixture) -> None:
        """Yields events from executor.run_stream()."""
        from cloud_agents.workflow.executor.step.base import StreamEvent

        mock_events = [
            StreamEvent(type="token", data={"delta": "Hello"}),
            StreamEvent(type="token", data={"delta": " world"}),
            StreamEvent(type="complete", data={}, result=mocker.MagicMock()),
        ]

        async def mock_stream(_step_input):  # type: ignore[no-untyped-def]
            for event in mock_events:
                yield event

        mock_exec = mocker.MagicMock()
        mock_exec.run_stream = mock_stream
        mocker.patch(
            "workflow.query_executor.get_step_executor",
            return_value=mock_exec,
        )
        mocker.patch("workflow.query_executor.configuration").mcp_servers = []

        events = []
        async for event in stream_query_via_direct_executor(
            prompt="Hello",
            provider="openai",
            model="gpt-4o-mini",
        ):
            events.append(event)

        assert len(events) == 3
        assert events[0].type == "token"
        assert events[0].data["delta"] == "Hello"
        assert events[2].type == "complete"
