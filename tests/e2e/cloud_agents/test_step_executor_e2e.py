"""Step-executor-dispatch e2e tests for a single agent step.

Calls `get_step_executor(...).run(...)`/`.run_stream(...)` (or, for
ephemeral, `cloud_agents.workflow.core.step_runner.run_step(...)`)
directly -- bypassing the `/v1/agents/run` handler and FastAPI routing
entirely, one layer below test_agents_run_handler_e2e.py. See
test_workflow_definitions_e2e.py for the same layer applied to a full
multi-step workflow YAML instead of a single step.

Covers all three spawn tiers against real LLMs:
- spawn:none -- in-process (always available)
- spawn:local -- subprocess (always available)
- spawn:ephemeral -- OpenShell gateway (requires gateway + sandbox image,
  marked `ephemeral` so CI can deselect with `-m "not ephemeral"`)

Ephemeral tests need an OpenShell gateway configured with:
- [openshell.drivers.podman] grpc_endpoint/supervisor_image/default_image
  so sandboxes can reach the gateway on boot
- [openshell.gateway.gateway_jwt] (signing_key_path/public_key_path/kid_path)
  so the gateway can mint each sandbox's OPENSHELL_SANDBOX_TOKEN_FILE --
  without it sandboxes fail with "no sandbox token source available"
- [openshell.drivers.kubernetes] namespace, if the gateway itself runs as a
  K8s pod (it still bootstraps a K8s client for its own identity even when
  compute_drivers = ["podman"])

See ~/ws/local-infra/kind/openshell-gateway.yaml for a working example
(gateway running as a Kind pod, spawning sandboxes via Podman DooD).
Default OPENSHELL_GATEWAY_URL is localhost:17670, matching
cloud_agents.spawner.factory's own default. Override with
OPENSHELL_GATEWAY_URL=localhost:9080 for `make kind-openshell-up` in
~/ws/local-infra (its Service uses a non-default port).

Requires OPENAI_API_KEY.

Usage:
    uv run pytest tests/e2e/cloud_agents/test_step_executor_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)

_PROVIDER = {"name": "openai", "model": "gpt-4o-mini"}
_SANDBOX_IMAGE = os.environ.get(
    "LIGHTSPEED_SANDBOX_IMAGE", "quay.io/jameswong/lightspeed-agentic-sandbox:latest"
)
_OPENSHELL_ENDPOINT = os.environ.get("OPENSHELL_GATEWAY_URL", "localhost:17670")


@pytest.fixture(name="openshell_spawner")
def openshell_spawner_fixture():
    """Create OpenShellSpawner, skip if gateway unavailable."""
    try:
        from cloud_agents.spawner.openshell_spawner import OpenShellSpawner
        from openshell import SandboxClient

        client = SandboxClient(_OPENSHELL_ENDPOINT)
        client.health()
        return OpenShellSpawner(openshell_client=client)
    except Exception as exc:
        pytest.skip(f"OpenShell gateway not available: {exc}")


class TestSpawnNone:
    """E2E tests for spawn:none (in-process via DirectExecutor)."""

    @pytest.mark.asyncio
    async def test_simple_prompt(self) -> None:
        """In-process agent returns correct answer."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "none"}, spawner=None)
        result = await executor.run(
            StepInput(
                prompt="What is 5+5? Reply with just the number.",
                provider=_PROVIDER,
                step_name="none-test",
                output_key="result",
            )
        )

        assert result.status == "completed"
        assert "10" in str(result.output)
        assert result.input_tokens > 0
        assert result.output_tokens > 0

    @pytest.mark.asyncio
    async def test_with_instructions(self) -> None:
        """In-process agent follows system instructions."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "none"}, spawner=None)
        result = await executor.run(
            StepInput(
                prompt="Name a color.",
                provider=_PROVIDER,
                system_prompt="Always answer with exactly one word.",
                step_name="none-instructions",
                output_key="result",
            )
        )

        assert result.status == "completed"
        assert len(str(result.output).split()) <= 3


class TestSpawnLocal:
    """E2E tests for spawn:local (subprocess via SubprocessExecutor)."""

    @pytest.mark.asyncio
    async def test_simple_prompt(self) -> None:
        """Subprocess agent returns correct answer."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "local"}, spawner=None)
        result = await executor.run(
            StepInput(
                prompt="What is 7+7? Reply with just the number.",
                provider=_PROVIDER,
                step_name="local-test",
                output_key="result",
            )
        )

        assert result.status == "completed"
        assert "14" in str(result.output)
        assert result.input_tokens > 0

    @pytest.mark.asyncio
    async def test_with_instructions(self) -> None:
        """Subprocess agent follows system instructions."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "local"}, spawner=None)
        result = await executor.run(
            StepInput(
                prompt="What is the capital of Japan?",
                provider=_PROVIDER,
                system_prompt="Answer in one word.",
                step_name="local-instructions",
                output_key="result",
            )
        )

        assert result.status == "completed"
        assert "tokyo" in str(result.output).lower()

    @pytest.mark.asyncio
    async def test_subprocess_executes(self) -> None:
        """Subprocess executor completes and reports metrics."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "local"}, spawner=None)
        result = await executor.run(
            StepInput(
                prompt="Reply with only the word 'isolated'.",
                provider=_PROVIDER,
                step_name="local-isolation",
                output_key="result",
            )
        )

        assert result.status == "completed"
        assert result.duration_ms > 0


@pytest.mark.ephemeral
class TestSpawnEphemeral:
    """E2E tests for spawn:ephemeral via OpenShell gateway."""

    @pytest.mark.asyncio
    async def test_simple_prompt(self, openshell_spawner: Any) -> None:
        """OpenShell sandbox agent returns correct answer."""
        from cloud_agents.workflow.core.step_runner import run_step

        result = await run_step(
            input={
                "step": {
                    "name": "ephemeral-test",
                    "prompt": "What is 9+9? Reply with just the number.",
                },
                "workflow_id": "e2e-ephemeral",
                "provider": {
                    "name": "openai",
                    "model": "gpt-4o-mini",
                    "credentials_secret": "OPENAI_API_KEY",
                },
                "sandbox_image": _SANDBOX_IMAGE,
            },
            spawner=openshell_spawner,
        )

        assert result["status"] == "completed"
        assert "18" in str(result.get("output", {}))

    @pytest.mark.asyncio
    async def test_with_instructions(self, openshell_spawner: Any) -> None:
        """OpenShell sandbox agent follows system instructions."""
        from cloud_agents.workflow.core.step_runner import run_step

        result = await run_step(
            input={
                "step": {
                    "name": "ephemeral-instructions",
                    "prompt": "What is the smallest continent?",
                    "instructions": "Answer in one word.",
                },
                "workflow_id": "e2e-ephemeral-instructions",
                "provider": {
                    "name": "openai",
                    "model": "gpt-4o-mini",
                    "credentials_secret": "OPENAI_API_KEY",
                },
                "sandbox_image": _SANDBOX_IMAGE,
            },
            spawner=openshell_spawner,
        )

        assert result["status"] == "completed"
        output_str = str(result.get("output", {})).lower()
        assert "australia" in output_str or "oceania" in output_str

    @pytest.mark.asyncio
    async def test_transcript_collected(self, openshell_spawner: Any) -> None:
        """OpenShell sandbox produces a non-empty transcript."""
        from cloud_agents.workflow.core.step_runner import run_step

        result = await run_step(
            input={
                "step": {
                    "name": "ephemeral-transcript",
                    "prompt": "Say hello.",
                },
                "workflow_id": "e2e-ephemeral-transcript",
                "provider": {
                    "name": "openai",
                    "model": "gpt-4o-mini",
                    "credentials_secret": "OPENAI_API_KEY",
                },
                "sandbox_image": _SANDBOX_IMAGE,
            },
            spawner=openshell_spawner,
        )

        assert result["status"] == "completed"
        transcript = result.get("transcript", {})
        events = transcript.get("events", [])
        assert events is not None
        assert len(events) >= 1


class TestStreamingExecution:
    """E2E test for spawn:none streaming via run_stream."""

    @pytest.mark.asyncio
    async def test_stream_complete_event_has_output(self) -> None:
        """Complete event from stream has output and metrics."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "none"}, spawner=None)
        events = []
        async for event in executor.run_stream(
            StepInput(
                prompt="What is 1+1? Reply with just the number.",
                provider=_PROVIDER,
                step_name="test",
                output_key="result",
            )
        ):
            events.append(event)

        assert len(events) >= 1
        complete = [e for e in events if e.type == "complete"]
        assert len(complete) == 1
        assert complete[0].result.status == "completed"
        assert complete[0].result.input_tokens > 0
        assert complete[0].result.output_tokens > 0
        assert complete[0].result.duration_ms > 0
        assert "2" in str(complete[0].result.output)


class TestStepResultShape:
    """Verify StepResult (spawn:none) has the expected fields."""

    @pytest.mark.asyncio
    async def test_result_has_expected_fields(self) -> None:
        """StepResult from DirectExecutor has status, output, tokens."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "none"}, spawner=None)
        result = await executor.run(
            StepInput(
                prompt="Say hi.",
                provider=_PROVIDER,
                step_name="test",
                output_key="result",
            )
        )

        assert result.status == "completed"
        assert result.output is not None
        assert isinstance(result.input_tokens, int)
        assert isinstance(result.output_tokens, int)
        assert result.input_tokens > 0
        assert result.output_tokens > 0
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_result_has_transcript(self) -> None:
        """StepResult includes transcript entries."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        executor = get_step_executor({"name": "test", "spawn": "none"}, spawner=None)
        result = await executor.run(
            StepInput(
                prompt="What is Python?",
                provider=_PROVIDER,
                system_prompt="One sentence max.",
                step_name="test",
                output_key="result",
            )
        )

        assert result.status == "completed"
        assert isinstance(result.transcript, list)
        assert len(result.transcript) >= 1


class TestPromptValidation:
    """Validation helpers used by the step-executor path.

    Note: this doesn't exercise the step-executor itself, just the
    private `_validate_prompt()` function it (and query_direct_handler)
    call before dispatching -- arguably a tests/unit candidate rather
    than e2e, kept here as-is pending a separate decision on moving it.
    """

    def test_stream_validation_error(self) -> None:
        """Validation helpers raise on invalid input."""
        from workflow.query_executor import _validate_prompt

        with pytest.raises(ValueError, match="maximum length"):
            _validate_prompt("x" * 200_000)
