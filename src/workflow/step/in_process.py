"""In-process step executor using lightspeed-stack's pydantic-ai agent.

Implements cloud-agents' StepExecutor ABC for spawn=none mode.
Wraps build_agent() and agent.run() to execute workflow steps
in-process without spawning a container.
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any, Optional

from cloud_agents.workflow.core.models import (
    TranscriptEvent,
)
from cloud_agents.workflow.executor.step.base import (
    StepExecutor,
    StepInput,
    StepResult,
)
from ogx_client import AsyncOgxClient

from client import AsyncOgxClientHolder
from configuration import configuration
from log import get_logger
from utils.pydantic_ai_helpers import build_agent
from workflow.utils import prepare_workflow_step_params

logger = get_logger(__name__)


def _parse_tool_args(args: Any) -> dict[str, Any]:
    """Parse tool call arguments to a dict.

    Parameters:
        args: Raw args from a ToolCallPart (dict or JSON string).

    Returns:
        Parsed arguments as a dict.
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except (json.JSONDecodeError, TypeError):
            return {"raw": args}
    return {}


def _extract_transcript(run_result: Any) -> list[TranscriptEvent]:
    """Extract transcript events from a pydantic-ai agent run result.

    Maps pydantic-ai's ModelResponse/ModelRequest messages to
    cloud-agents' TranscriptEvent format.

    Parameters:
        run_result: Completed AgentRunResult from agent.run().

    Returns:
        Ordered list of TranscriptEvent objects.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

    events: list[TranscriptEvent] = []
    now = datetime.now(UTC).isoformat()

    for message in run_result.new_messages():
        if isinstance(message, ModelResponse):
            events.extend(_extract_response_events(message, now))
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    content = part.content
                    if isinstance(content, str) and len(content) > 1000:
                        content = content[:1000] + "...(truncated)"
                    events.append(
                        TranscriptEvent(
                            ts=now,
                            type="tool_result",
                            data={"name": part.tool_name, "output": content},
                        )
                    )

    return events


def _extract_response_events(message: Any, ts: str) -> list[TranscriptEvent]:
    """Extract events from a ModelResponse message.

    Parameters:
        message: A pydantic-ai ModelResponse.
        ts: ISO timestamp string.

    Returns:
        List of TranscriptEvent for tool calls and text output.
    """
    events: list[TranscriptEvent] = []
    for part in message.parts:
        if hasattr(part, "tool_name"):
            args = part.args if hasattr(part, "args") else {}
            events.append(
                TranscriptEvent(
                    ts=ts,
                    type="tool_call",
                    data={
                        "name": part.tool_name,
                        "input": _parse_tool_args(args),
                    },
                )
            )
    if message.text:
        events.append(
            TranscriptEvent(ts=ts, type="result", data={"text": message.text})
        )
    return events


class InProcessStepExecutor(StepExecutor):
    """Execute workflow steps in-process using pydantic-ai.

    Uses lightspeed-stack's build_agent() to construct an agent and
    runs it synchronously (agent.run). Results are mapped to
    cloud-agents' StepResult format with transcript capture.

    Attributes:
        _client: Llama Stack client for agent execution.
    """

    def __init__(
        self,
        client: Optional[AsyncOgxClient] = None,
    ) -> None:
        """Initialize the in-process executor.

        Parameters:
            client: Llama Stack client. If None, uses the global singleton.
        """
        self._client = client

    def _get_client(self) -> AsyncOgxClient:
        """Get the Llama Stack client.

        Returns:
            The configured AsyncOgxClient.
        """
        if self._client is not None:
            return self._client
        return AsyncOgxClientHolder().get_client()

    def _resolve_model(self, step_input: StepInput) -> str:
        """Resolve the full model ID from step input provider config.

        Parameters:
            step_input: Step input with provider configuration.

        Returns:
            Model ID in "provider/model" format.
        """
        model = step_input.provider.get("model", "")
        provider_name = step_input.provider.get("name", "")
        if provider_name and "/" not in model:
            return f"{provider_name}/{model}"
        return model

    @staticmethod
    def _parse_output(output_text: str, has_schema: bool) -> dict[str, Any]:
        """Parse agent output text into a result dict.

        Parameters:
            output_text: Raw text output from the agent.
            has_schema: Whether an output schema was requested.

        Returns:
            Parsed output dict.
        """
        if not has_schema:
            return {"summary": output_text}
        try:
            parsed = json.loads(output_text)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except (json.JSONDecodeError, TypeError):
            return {"raw": output_text}

    async def run(self, step_input: StepInput) -> StepResult:
        """Execute a workflow step in-process via pydantic-ai.

        Parameters:
            step_input: Step execution input with prompt, provider config,
                system prompt, tools, and context.

        Returns:
            StepResult with status, output, transcript, and metrics.
        """
        step_name = step_input.step_name
        logger.info("Running in-process step '%s'", step_name)
        start_ms = int(time.monotonic() * 1000)

        try:
            params = prepare_workflow_step_params(
                model=self._resolve_model(step_input),
                prompt=step_input.prompt,
                instructions=step_input.system_prompt,
            )

            agent = build_agent(self._get_client(), params, configuration)
            run_result = await agent.run(step_input.prompt)

            transcript_events = _extract_transcript(run_result)
            duration_ms = int(time.monotonic() * 1000) - start_ms

            input_tokens = run_result.usage.total_tokens or 0 if run_result.usage else 0
            output_tokens = (
                run_result.usage.output_tokens or 0 if run_result.usage else 0
            )

            output_text = (
                run_result.output
                if isinstance(run_result.output, str)
                else str(run_result.output)
            )
            output = self._parse_output(output_text, bool(step_input.output_schema))

            logger.info(
                "Step '%s' completed in %dms (%d events)",
                step_name,
                duration_ms,
                len(transcript_events),
            )

            return StepResult(
                status="completed",
                output=output,
                transcript=[e.model_dump() for e in transcript_events],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
            )

        except (RuntimeError, ValueError, TypeError, OSError, ConnectionError) as exc:
            duration_ms = int(time.monotonic() * 1000) - start_ms
            logger.error(
                "Step '%s' failed after %dms: %s",
                step_name,
                duration_ms,
                exc,
                exc_info=True,
            )
            return StepResult(
                status="failed",
                error=str(exc),
                duration_ms=duration_ms,
            )
