"""Unit tests for the /v1/workflows/* endpoints."""

# pylint: disable=protected-access,too-few-public-methods,unused-argument,import-outside-toplevel

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from pytest_mock import MockerFixture

from app.endpoints.workflows import (
    _get_executor,
    approve_workflow_handler,
    cancel_workflow_handler,
    get_transcripts_handler,
    get_workflow_handler,
    start_workflow_handler,
)
from models.api.requests.agents import ApproveWorkflowRequest, RunWorkflowRequest


@pytest.fixture(autouse=True)
def reset_executor() -> Generator[None, None, None]:
    """Reset the module-level executor singleton."""
    import app.endpoints.workflows as wf_mod

    wf_mod._executor = None
    yield
    wf_mod._executor = None


@pytest.fixture(name="mock_config")
def mock_config_fixture(mocker: MockerFixture) -> Any:
    """Mock the configuration singleton."""
    mock_cfg = mocker.patch("app.endpoints.workflows.configuration")
    mock_cfg.workflow_engine_configuration = mocker.MagicMock()
    mock_cfg.workflow_engine_configuration.enabled = True
    mock_cfg.inference = mocker.MagicMock()
    mock_cfg.inference.default_model = "gpt-4o"
    mock_cfg.inference.default_provider = "openai"
    return mock_cfg


@pytest.fixture(name="mock_executor")
def mock_executor_fixture(mocker: MockerFixture) -> Any:
    """Mock the workflow executor by injecting directly into module state."""
    import app.endpoints.workflows as wf_mod

    mock_exec = mocker.AsyncMock()
    wf_mod._executor = mock_exec
    return mock_exec


class TestGetExecutor:
    """Tests for _get_executor."""

    def test_disabled_engine_raises_503(
        self, mocker: MockerFixture, mock_config: Any
    ) -> None:
        """Disabled workflow engine raises HTTP 503."""
        mock_config.workflow_engine_configuration.enabled = False
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _get_executor()
        assert exc_info.value.status_code == 503

    def test_passes_real_spawner_when_configured(
        self, mocker: MockerFixture, mock_config: Any
    ) -> None:
        """A configured spawner is built and forwarded to create_workflow_runner.

        Regression test: _get_executor() used to call create_workflow_runner()
        with no spawner argument at all, silently no-opping ephemeral spawn
        for workflow steps regardless of spawner_configuration.
        """
        spawner_config = mocker.MagicMock()
        mock_config.spawner_configuration = spawner_config

        fake_spawner = mocker.MagicMock()
        mock_build_spawner = mocker.patch(
            "app.endpoints.workflows.build_spawner", return_value=fake_spawner
        )
        mock_create_runner = mocker.patch(
            "workflow.executor_factory.create_workflow_runner"
        )

        _get_executor()

        mock_build_spawner.assert_called_once_with(spawner_config)
        mock_create_runner.assert_called_once_with(spawner=fake_spawner)

    def test_no_spawner_config_passes_none(
        self, mocker: MockerFixture, mock_config: Any
    ) -> None:
        """Without a spawner config, create_workflow_runner gets no spawner."""
        mock_config.spawner_configuration = None
        mock_build_spawner = mocker.patch("app.endpoints.workflows.build_spawner")
        mock_create_runner = mocker.patch(
            "workflow.executor_factory.create_workflow_runner"
        )

        _get_executor()

        mock_build_spawner.assert_not_called()
        mock_create_runner.assert_called_once_with(spawner=None)


class TestStartWorkflow:
    """Tests for start_workflow_handler."""

    @pytest.mark.asyncio
    async def test_starts_workflow(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Starts a workflow and returns ID."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {
                    "steps": [
                        {
                            "name": "analyze",
                            "type": "agent",
                            "output_key": "analysis",
                            "spawn": "none",
                            "prompt": "Analyze this",
                        }
                    ]
                },
            }
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await start_workflow_handler.__wrapped__(request, body, auth)

        assert result["workflow_id"] == "wf-abc123"
        assert result["status"] == "running"
        mock_executor.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_credentials_secret_added_for_known_provider(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """A known provider gets its credentials_secret env var injected.

        Regression test: start_workflow_handler forwarded body.provider
        as-is, so an ephemeral step's sandbox never received an LLM API
        key at all.
        """
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_config.spawner_configuration = None
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {"steps": []},
            },
            provider={"name": "anthropic", "model": "claude-sonnet-5"},
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["provider"]["credentials_secret"] == "ANTHROPIC_API_KEY"

    @pytest.mark.asyncio
    async def test_credentials_secret_omitted_for_unknown_provider(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """An unrecognized provider gets no guessed credentials_secret."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_config.spawner_configuration = None
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {"steps": []},
            },
            provider={"name": "bedrock", "model": "some-model"},
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert "credentials_secret" not in workflow_input["provider"]

    @pytest.mark.asyncio
    async def test_caller_supplied_credentials_secret_not_overridden(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """A caller-supplied credentials_secret is preserved as-is."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_config.spawner_configuration = None
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {"steps": []},
            },
            provider={
                "name": "openai",
                "model": "gpt-4o",
                "credentials_secret": "MY_CUSTOM_KEY",
            },
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["provider"]["credentials_secret"] == "MY_CUSTOM_KEY"

    @pytest.mark.asyncio
    async def test_sandbox_image_falls_back_to_spawner_config(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """sandbox_image falls back to spawner_configuration when unset.

        Regression test: start_workflow_handler hardcoded
        "lightspeed-agentic-sandbox:latest" regardless of
        spawner_configuration.sandbox_image.
        """
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "configured-sandbox:v3"
        mock_config.spawner_configuration = spawner_config
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {"steps": []},
            },
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["sandbox_image"] == "configured-sandbox:v3"

    @pytest.mark.asyncio
    async def test_sandbox_image_request_override_wins(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """A request-level sandbox_image wins over spawner_configuration."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "configured-sandbox:v3"
        mock_config.spawner_configuration = spawner_config
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {"steps": []},
            },
            sandbox_image="custom-sandbox:v9",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["sandbox_image"] == "custom-sandbox:v9"

    @pytest.mark.asyncio
    async def test_no_spawner_config_falls_back_to_default_image(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Without spawner_configuration, falls back to the hardcoded default."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_config.spawner_configuration = None
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {"steps": []},
            },
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["sandbox_image"] == "lightspeed-agentic-sandbox:latest"

    @pytest.mark.asyncio
    async def test_forwards_session_id(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Forwards a caller-supplied session_id into the executor's input."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {
                    "steps": [
                        {
                            "name": "analyze",
                            "type": "agent",
                            "output_key": "analysis",
                            "spawn": "none",
                            "prompt": "Analyze this",
                        }
                    ]
                },
            },
            session_id="ses-abc123",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        mock_executor.start.assert_called_once()
        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["session_id"] == "ses-abc123"

    @pytest.mark.asyncio
    async def test_session_id_omitted_forwards_none(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Omitting session_id forwards None rather than a missing key."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {
                    "steps": [
                        {
                            "name": "analyze",
                            "type": "agent",
                            "output_key": "analysis",
                            "spawn": "none",
                            "prompt": "Analyze this",
                        }
                    ]
                },
            },
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["session_id"] is None

    @pytest.mark.asyncio
    async def test_forwards_user_id(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Forwards the authenticated user_id as a top-level input key.

        StepMetadata.user_id is populated from input.get("user_id") in
        cloud-agents (graph_translator.py) -- previously only nested under
        authz_context, which cloud-agents doesn't read for this purpose.
        """
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {
                    "steps": [
                        {
                            "name": "analyze",
                            "type": "agent",
                            "output_key": "analysis",
                            "spawn": "none",
                            "prompt": "Analyze this",
                        }
                    ]
                },
            },
        )
        auth = ("user-42", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["user_id"] == "user-42"
        assert workflow_input["authz_context"]["user_id"] == "user-42"

    @pytest.mark.asyncio
    async def test_forwards_mcp_servers(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Forwards a caller-supplied mcp_servers list into the executor input.

        cloud-agents' LocalWorkflowRunner reads input["mcp_servers"] and
        threads it to spawn:none steps (graph_translator -> direct.py's
        pydantic-ai MCPToolset). Without this key the run-scoped MCP
        catalog never reaches the in-process agent loop, so the model
        can't call external tools.
        """
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.start.return_value = "wf-abc123"

        servers = [{"name": "pod-status", "url": "http://localhost:9111/mcp"}]
        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {
                    "steps": [
                        {
                            "name": "analyze",
                            "type": "agent",
                            "output_key": "analysis",
                            "spawn": "none",
                            "prompt": "Analyze this",
                        }
                    ]
                },
            },
            mcp_servers=servers,
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["mcp_servers"] == servers

    @pytest.mark.asyncio
    async def test_mcp_servers_omitted_forwards_none(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Omitting mcp_servers forwards None rather than a missing key."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.start.return_value = "wf-abc123"

        body = RunWorkflowRequest(
            definition={
                "apiVersion": "v1",
                "kind": "AgentWorkflow",
                "metadata": {"name": "test-wf"},
                "spec": {"steps": []},
            },
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await start_workflow_handler.__wrapped__(request, body, auth)

        workflow_input = mock_executor.start.call_args[0][0]
        assert workflow_input["mcp_servers"] is None


class TestGetWorkflow:
    """Tests for get_workflow_handler."""

    @pytest.mark.asyncio
    async def test_returns_status(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Returns workflow status."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        from cloud_agents.workflow.executor.base import WorkflowStatus

        mock_executor.get_status.return_value = WorkflowStatus(
            workflow_id="wf-1",
            status="completed",
            steps={"analyze": {"status": "completed"}},
            events=[],
            is_terminal=True,
        )

        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await get_workflow_handler.__wrapped__(request, "wf-1", auth)

        assert result["workflow_id"] == "wf-1"
        assert result["status"] == "completed"
        assert result["is_terminal"] is True

    @pytest.mark.asyncio
    async def test_not_found_raises_404(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Missing workflow raises HTTP 404."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.get_status.side_effect = KeyError("not found")

        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_handler.__wrapped__(request, "wf-missing", auth)
        assert exc_info.value.status_code == 404


class TestApproveWorkflow:
    """Tests for approve_workflow_handler."""

    @pytest.mark.asyncio
    async def test_approves_step(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Approves a paused workflow step."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")

        body = ApproveWorkflowRequest(
            step_name="review",
            decision="approved",
            approver="admin",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await approve_workflow_handler.__wrapped__(request, "wf-1", body, auth)

        assert "approved" in result["message"]
        mock_executor.approve.assert_called_once()


class TestCancelWorkflow:
    """Tests for cancel_workflow_handler."""

    @pytest.mark.asyncio
    async def test_cancels_workflow(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Cancels a running workflow."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")

        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await cancel_workflow_handler.__wrapped__(request, "wf-1", auth)

        assert "cancelled" in result["message"]
        mock_executor.cancel.assert_called_once_with("wf-1")


class TestGetTranscripts:
    """Tests for get_transcripts_handler."""

    @pytest.mark.asyncio
    async def test_returns_transcripts(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Returns step transcripts."""
        mocker.patch("app.endpoints.workflows.check_configuration_loaded")
        mock_executor.get_step_transcripts.return_value = {
            "analyze": {"events": [], "step_name": "analyze"}
        }

        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await get_transcripts_handler.__wrapped__(request, "wf-1", auth)

        assert result["workflow_id"] == "wf-1"
        assert "analyze" in result["transcripts"]
