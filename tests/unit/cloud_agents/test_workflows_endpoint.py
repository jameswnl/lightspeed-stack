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
