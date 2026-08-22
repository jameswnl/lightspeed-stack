"""Unit tests for the InProcessStepExecutor."""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pytest_mock import MockerFixture

from workflow.step.in_process import InProcessStepExecutor, _extract_transcript


@pytest.fixture(name="executor")
def executor_fixture() -> InProcessStepExecutor:
    """Create an InProcessStepExecutor."""
    return InProcessStepExecutor()


def _make_step_input(mocker: MockerFixture, **overrides: Any) -> Any:
    """Create a mock StepInput."""
    step_input = mocker.MagicMock()
    step_input.step_name = overrides.get("step_name", "test-step")
    step_input.prompt = overrides.get("prompt", "Analyze the cluster")
    step_input.provider = overrides.get(
        "provider", {"name": "openai", "model": "gpt-4o-mini"}
    )
    step_input.system_prompt = overrides.get("system_prompt", None)
    step_input.output_schema = overrides.get("output_schema", None)
    step_input.output_key = overrides.get("output_key", "test-step")
    return step_input


class TestExtractTranscript:
    """Tests for _extract_transcript."""

    def test_empty_messages(self, mocker: MockerFixture) -> None:
        """No messages produces empty transcript."""
        mock_result = mocker.MagicMock()
        mock_result.new_messages.return_value = []
        events = _extract_transcript(mock_result)
        assert not events

    def test_text_response(self, mocker: MockerFixture) -> None:
        """ModelResponse with text produces a result event."""
        response = ModelResponse(
            parts=[TextPart(content="Analysis complete")],
        )

        mock_result = mocker.MagicMock()
        mock_result.new_messages.return_value = [response]

        events = _extract_transcript(mock_result)
        assert len(events) == 1
        assert events[0].type == "result"
        assert events[0].data["text"] == "Analysis complete"

    def test_tool_call_and_result(self, mocker: MockerFixture) -> None:
        """Tool call and return produce tool_call and tool_result events."""
        tool_call = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="kubectl",
                    args={"command": "get pods"},
                    tool_call_id="tc-1",
                )
            ],
        )
        tool_return = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="kubectl",
                    content="pod-1 Running",
                    tool_call_id="tc-1",
                )
            ],
        )

        mock_result = mocker.MagicMock()
        mock_result.new_messages.return_value = [tool_call, tool_return]

        events = _extract_transcript(mock_result)
        assert len(events) == 2
        assert events[0].type == "tool_call"
        assert events[0].data["name"] == "kubectl"
        assert events[1].type == "tool_result"
        assert events[1].data["name"] == "kubectl"


class TestInProcessStepExecutor:
    """Tests for InProcessStepExecutor.run."""

    @pytest.mark.asyncio
    async def test_successful_run(
        self,
        mocker: MockerFixture,
        executor: InProcessStepExecutor,
    ) -> None:
        """Successful agent run returns completed StepResult."""
        mock_run_result = mocker.MagicMock()
        mock_run_result.output = "Analysis: cluster is healthy"
        mock_run_result.new_messages.return_value = []
        mock_run_result.usage = mocker.MagicMock()
        mock_run_result.usage.input_tokens = 100
        mock_run_result.usage.output_tokens = 50

        mock_agent = mocker.AsyncMock()
        mock_agent.run.return_value = mock_run_result

        mocker.patch(
            "workflow.step.in_process.Agent",
            return_value=mock_agent,
        )

        step_input = _make_step_input(mocker)
        result = await executor.run(step_input)

        assert result.status == "completed"
        assert result.output is not None
        assert result.output["summary"] == "Analysis: cluster is healthy"

    @pytest.mark.asyncio
    async def test_failed_run(
        self,
        mocker: MockerFixture,
        executor: InProcessStepExecutor,
    ) -> None:
        """Agent exception returns failed StepResult."""
        mocker.patch(
            "workflow.step.in_process.Agent",
            side_effect=RuntimeError("LLM connection failed"),
        )

        step_input = _make_step_input(mocker)
        result = await executor.run(step_input)

        assert result.status == "failed"
        assert "LLM connection failed" in result.error

    @pytest.mark.asyncio
    async def test_structured_output(
        self,
        mocker: MockerFixture,
        executor: InProcessStepExecutor,
    ) -> None:
        """When output_schema is set, output is parsed as JSON."""
        mock_run_result = mocker.MagicMock()
        mock_run_result.output = '{"severity": "high", "count": 3}'
        mock_run_result.new_messages.return_value = []
        mock_run_result.usage = mocker.MagicMock()
        mock_run_result.usage.input_tokens = 80
        mock_run_result.usage.output_tokens = 30

        mock_agent = mocker.AsyncMock()
        mock_agent.run.return_value = mock_run_result

        mocker.patch(
            "workflow.step.in_process.Agent",
            return_value=mock_agent,
        )

        step_input = _make_step_input(
            mocker,
            output_schema={"type": "object"},
        )
        result = await executor.run(step_input)

        assert result.status == "completed"
        assert result.output == {"severity": "high", "count": 3}

    @pytest.mark.asyncio
    async def test_model_resolution(
        self,
        mocker: MockerFixture,
        executor: InProcessStepExecutor,
    ) -> None:
        """Model string is built as provider:model for pydantic-ai."""
        mock_run_result = mocker.MagicMock()
        mock_run_result.output = "done"
        mock_run_result.new_messages.return_value = []
        mock_run_result.usage = mocker.MagicMock()
        mock_run_result.usage.input_tokens = 10
        mock_run_result.usage.output_tokens = 5

        mock_agent = mocker.AsyncMock()
        mock_agent.run.return_value = mock_run_result

        mock_agent_cls = mocker.patch(
            "workflow.step.in_process.Agent",
            return_value=mock_agent,
        )

        step_input = _make_step_input(
            mocker,
            provider={"name": "openai", "model": "gpt-4o"},
        )
        await executor.run(step_input)

        mock_agent_cls.assert_called_once_with("openai:gpt-4o", instructions="")
