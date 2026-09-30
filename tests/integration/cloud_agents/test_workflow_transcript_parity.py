"""Transcript parity across workflow spawn modes (issue #52).

Verifies on the unified workflow path that the same logical agent run
(one step, same prompt, same tool) yields the same canonical transcript
event types and data keys across spawn modes -- ``none`` and ``local``
executed end-to-end here through the real ``LocalWorkflowRunner`` with
in-memory stores and the scripted mock LLM server; ``ephemeral``'s
contract is pinned at the shared ``TranscriptEvent`` normalization level
(its executor needs a real sandbox, see the e2e suite).

Run from the repository root (the spawn:local subprocess child resolves
``CLOUD_AGENTS_TOOLS_MODULE`` via its inherited working directory).
"""

# pylint: disable=too-many-locals

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest
from cloud_agents.workflow.core.models import (
    StepTranscript,
    TranscriptEvent,
    normalize_transcript_events,
)
from cloud_agents.workflow.executor.local.executor import LocalWorkflowRunner

from tests.e2e.cloud_agents.mock_llm_server import MockResponsesServer

CANONICAL_EVENT_TYPES = {"tool_call", "tool_result", "thinking", "result", "error"}

PARITY_TOOL = "parity_probe"
PARITY_STEP = {
    "name": "agent",
    "type": "agent",
    "prompt": "Run the parity probe",
    "output_key": "result",
    "tools": [PARITY_TOOL],
    "timeout_seconds": 60,
}

# Reference EventLogger output (lightspeed-agentic-sandbox
# src/lightspeed_agentic/logging.py) -- the canonical producer whose
# contract spawn:none/local transcripts must match.
REFERENCE_EPHEMERAL_JSONL = [
    {"ts": "2026-09-30T00:00:01+00:00", "type": "thinking", "data": {"text": "hmm"}},
    {
        "ts": "2026-09-30T00:00:02+00:00",
        "type": "tool_call",
        "data": {"name": PARITY_TOOL, "input": '{"q": 7}'},
    },
    {
        "ts": "2026-09-30T00:00:03+00:00",
        "type": "tool_result",
        "data": {"output": "parity:7"},
    },
    {
        "ts": "2026-09-30T00:00:04+00:00",
        "type": "result",
        "data": {
            "text": "parity final answer",
            "cost_usd": 0.0001,
            "input_tokens": 8,
            "output_tokens": 9,
        },
    },
]


class InMemoryRunStateStore:
    """Duck-typed RunStateStore sufficient for LocalWorkflowRunner."""

    def __init__(self) -> None:
        """Start with no workflow state."""
        self.workflows: dict[str, dict[str, Any]] = {}

    async def create(  # pylint: disable=too-many-arguments,too-many-positional-arguments,unused-argument
        self,
        workflow_id: str,
        workflow_name: str,
        definition: dict[str, Any],
        provider: dict[str, Any],
        authz_context: dict[str, Any],
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        parent_workflow_id: Optional[str] = None,
    ) -> None:
        """Persist initial workflow state (RunStateStore.create signature)."""
        self.workflows[workflow_id] = {
            "workflow_name": workflow_name,
            "definition": definition,
            "provider": provider,
            "status": "running",
            "steps": {},
            "events": [],
            "workflow_context": {},
        }

    async def get(self, workflow_id: str) -> Optional[dict[str, Any]]:
        """Return the workflow state dict, or None if unknown."""
        return self.workflows.get(workflow_id)

    async def update_step(
        self,
        workflow_id: str,
        step_name: str,
        status: str,
        output: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record a step result."""
        self.workflows[workflow_id]["steps"][step_name] = {
            "status": status,
            "output": output,
            "error": error,
        }

    async def append_event(self, workflow_id: str, event: dict[str, Any]) -> None:
        """Append a lifecycle event."""
        self.workflows[workflow_id]["events"].append(event)

    async def update_workflow_context(
        self, workflow_id: str, context: dict[str, Any]
    ) -> None:
        """Replace the workflow context."""
        self.workflows[workflow_id]["workflow_context"] = context

    async def set_paused(self, workflow_id: str, step_name: str) -> None:
        """Mark the workflow paused at a step."""
        self.workflows[workflow_id]["status"] = "paused"
        self.workflows[workflow_id]["paused_at_step"] = step_name

    async def resume(self, workflow_id: str) -> None:
        """Clear the paused marker."""
        self.workflows[workflow_id]["status"] = "running"
        self.workflows[workflow_id].pop("paused_at_step", None)

    async def mark_terminal(self, workflow_id: str, status: str) -> None:
        """Mark the workflow terminal."""
        self.workflows[workflow_id]["status"] = status

    async def list_paused(self) -> list[str]:
        """List workflow IDs currently paused."""
        return [
            wid
            for wid, state in self.workflows.items()
            if state.get("status") == "paused"
        ]


class InMemoryTranscriptStore:  # pylint: disable=too-few-public-methods
    """Duck-typed TranscriptStore capturing saved StepTranscripts."""

    def __init__(self) -> None:
        """Start with no saved transcripts."""
        self.saved: dict[str, dict[str, StepTranscript]] = {}

    async def save(  # pylint: disable=unused-argument
        self,
        workflow_id: str,
        step_name: str,
        transcript: StepTranscript,
        trace_id: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """Save a step transcript in insertion order."""
        self.saved.setdefault(workflow_id, {})[step_name] = transcript

    async def get(self, workflow_id: str, step_name: str) -> Optional[StepTranscript]:
        """Return a saved transcript, or None."""
        return self.saved.get(workflow_id, {}).get(step_name)

    async def list_steps(self, workflow_id: str) -> list[str]:
        """List saved step names in save order."""
        return list(self.saved.get(workflow_id, {}))


class ParityEnv:  # pylint: disable=too-few-public-methods
    """Mock LLM server + env wiring shared by spawn:none and spawn:local."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Start the scripted server and point the stack's env at it."""
        self.server = MockResponsesServer(
            tool_call={
                "name": PARITY_TOOL,
                "arguments": {"q": 7},
                "result_text": "parity final answer",
            }
        )
        self.server.start()
        monkeypatch.setenv("OPENAI_BASE_URL", self.server.base_url)
        monkeypatch.setenv("OPENAI_API_KEY", "parity-test-dummy-key")
        monkeypatch.setenv(
            "CLOUD_AGENTS_TOOLS_MODULE", "tests.integration.cloud_agents.parity_tools"
        )

    def stop(self) -> None:
        """Stop the mock server."""
        self.server.stop()


def _one_step_definition(spawn: str) -> dict[str, Any]:
    """The documented one-step workflow shape with the parity tool."""
    return {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": f"parity-one-step-{spawn}"},
        "spec": {"steps": [{**PARITY_STEP, "spawn": spawn}]},
    }


def _multi_step_definition(spawn: str) -> dict[str, Any]:
    """A two-step workflow whose second step is the parity step."""
    return {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": f"parity-multi-step-{spawn}"},
        "spec": {
            "steps": [
                {
                    "name": "warmup",
                    "type": "agent",
                    "prompt": "Say ok",
                    "output_key": "warmup_result",
                    "spawn": spawn,
                    "timeout_seconds": 60,
                },
                {**PARITY_STEP, "spawn": spawn},
            ]
        },
    }


def _workflow_input(definition: dict[str, Any]) -> dict[str, Any]:
    """Runner input for a definition against the mock provider."""
    return {
        "definition": definition,
        "provider": {"name": "openai", "model": "gpt-4o-mock"},
    }


async def _run_to_terminal(
    runner: LocalWorkflowRunner, workflow_input: dict[str, Any]
) -> dict[str, Any]:
    """Start a workflow and poll until it reaches a terminal status."""
    workflow_id = await runner.start(workflow_input)
    for _ in range(60):
        status = await runner.get_status(workflow_id)
        if status.is_terminal:
            return {
                "workflow_id": workflow_id,
                "status": status.status,
                "steps": status.steps,
            }
        await asyncio.sleep(0.5)
    pytest.fail("workflow did not reach a terminal status in 30s")


async def _step_events(
    runner: LocalWorkflowRunner, workflow_id: str
) -> list[dict[str, Any]]:
    """Canonical transcript event dicts for step 'result', via the public API.

    Uses ``get_step_transcripts`` -- the same accessor the
    ``/v1/workflows/{id}/transcripts`` endpoint serves from -- so the
    parity assertions check exactly what callers receive.
    """
    transcripts = await runner.get_step_transcripts(workflow_id)
    return transcripts["result"]["events"]


def _event_skeletons(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project events down to the parity-comparable skeleton.

    ``ts`` is excluded: reconstructed (none/local) events carry the run
    completion timestamp, which differs between runs by design -- the
    cross-mode contract is event order + type + data.
    """
    return [{"type": e["type"], "data": e["data"]} for e in events]


class TestTranscriptParityAcrossSpawnModes:
    """Same logical run, same canonical events, across spawn modes."""

    @pytest.mark.asyncio
    async def test_none_and_local_yield_identical_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same one-step run yields identical events under none and local."""
        parity_env = ParityEnv(monkeypatch)
        try:
            transcripts: dict[str, list[dict[str, Any]]] = {}
            for spawn in ("none", "local"):
                runner = LocalWorkflowRunner(
                    run_state_store=InMemoryRunStateStore(),
                    transcript_store=InMemoryTranscriptStore(),
                )
                outcome = await _run_to_terminal(
                    runner, _workflow_input(_one_step_definition(spawn))
                )
                assert outcome["status"] == "completed", outcome
                transcripts[spawn] = await _step_events(runner, outcome["workflow_id"])
        finally:
            parity_env.stop()

        assert _event_skeletons(transcripts["none"]) == _event_skeletons(
            transcripts["local"]
        )
        assert [e["type"] for e in transcripts["none"]] == [
            "tool_call",
            "tool_result",
            "result",
        ]

    @pytest.mark.asyncio
    async def test_none_emits_canonical_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn:none events match the sandbox data-key contract."""
        parity_env = ParityEnv(monkeypatch)
        try:
            runner = LocalWorkflowRunner(
                run_state_store=InMemoryRunStateStore(),
                transcript_store=InMemoryTranscriptStore(),
            )
            outcome = await _run_to_terminal(
                runner, _workflow_input(_one_step_definition("none"))
            )
            assert outcome["status"] == "completed", outcome
            events = await _step_events(runner, outcome["workflow_id"])
        finally:
            parity_env.stop()

        tool_call, tool_result, result = events
        assert set(tool_call) == {"ts", "type", "data"}
        assert tool_call["data"]["name"] == PARITY_TOOL
        assert tool_call["data"]["input"] == '{"q": 7}'
        assert tool_result["data"] == {"output": "parity:7"}
        assert result["data"]["text"] == "parity final answer"
        # Documented gap vs ephemeral: aggregate usage, unknown cost.
        assert result["data"]["cost_usd"] is None
        assert result["data"]["input_tokens"] > 0
        assert result["data"]["output_tokens"] > 0
        assert {e["type"] for e in events} <= CANONICAL_EVENT_TYPES

    @pytest.mark.asyncio
    async def test_local_emits_canonical_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn:local events match the sandbox data-key contract."""
        parity_env = ParityEnv(monkeypatch)
        try:
            runner = LocalWorkflowRunner(
                run_state_store=InMemoryRunStateStore(),
                transcript_store=InMemoryTranscriptStore(),
            )
            outcome = await _run_to_terminal(
                runner, _workflow_input(_one_step_definition("local"))
            )
            assert outcome["status"] == "completed", outcome
            events = await _step_events(runner, outcome["workflow_id"])
        finally:
            parity_env.stop()

        tool_call, tool_result, result = events
        assert tool_call["data"]["name"] == PARITY_TOOL
        assert tool_call["data"]["input"] == '{"q": 7}'
        assert tool_result["data"] == {"output": "parity:7"}
        assert result["data"]["text"] == "parity final answer"
        assert result["data"]["cost_usd"] is None
        assert {e["type"] for e in events} <= CANONICAL_EVENT_TYPES

    @pytest.mark.asyncio
    @pytest.mark.parametrize("spawn", ["none", "local"])
    async def test_multi_step_step_matches_one_step(
        self, monkeypatch: pytest.MonkeyPatch, spawn: str
    ) -> None:
        """An equivalent step in a multi-step workflow yields the same events."""
        parity_env = ParityEnv(monkeypatch)
        try:
            runner = LocalWorkflowRunner(
                run_state_store=InMemoryRunStateStore(),
                transcript_store=InMemoryTranscriptStore(),
            )
            one = await _run_to_terminal(
                runner, _workflow_input(_one_step_definition(spawn))
            )
            multi_runner = LocalWorkflowRunner(
                run_state_store=InMemoryRunStateStore(),
                transcript_store=InMemoryTranscriptStore(),
            )
            multi = await _run_to_terminal(
                multi_runner, _workflow_input(_multi_step_definition(spawn))
            )
            assert one["status"] == "completed", one
            assert multi["status"] == "completed", multi

            one_step_events = await _step_events(runner, one["workflow_id"])
            multi_events = await _step_events(multi_runner, multi["workflow_id"])
        finally:
            parity_env.stop()

        assert _event_skeletons(multi_events) == _event_skeletons(one_step_events)

    @pytest.mark.asyncio
    async def test_failed_run_emits_error_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing spawn:none run persists a canonical error event."""
        monkeypatch.setenv("OPENAI_API_KEY", "parity-test-dummy-key")
        monkeypatch.setenv(
            "CLOUD_AGENTS_TOOLS_MODULE", "tests.integration.cloud_agents.parity_tools"
        )
        # A dead loopback port: connection refused, no retry storm.
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")

        runner = LocalWorkflowRunner(
            run_state_store=InMemoryRunStateStore(),
            transcript_store=InMemoryTranscriptStore(),
        )
        outcome = await _run_to_terminal(
            runner, _workflow_input(_one_step_definition("none"))
        )
        assert outcome["status"] == "failed", outcome

        events = await _step_events(runner, outcome["workflow_id"])
        assert [e["type"] for e in events] == ["error"]
        assert set(events[0]["data"]) == {"message"}
        assert events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_get_step_transcripts_exposes_canonical_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The transcripts API data source returns the canonical events."""
        parity_env = ParityEnv(monkeypatch)
        try:
            runner = LocalWorkflowRunner(
                run_state_store=InMemoryRunStateStore(),
                transcript_store=InMemoryTranscriptStore(),
            )
            outcome = await _run_to_terminal(
                runner, _workflow_input(_one_step_definition("none"))
            )
            transcripts = await runner.get_step_transcripts(outcome["workflow_id"])
        finally:
            parity_env.stop()

        assert "result" in transcripts
        body = transcripts["result"]
        events = body["events"]
        assert [e["type"] for e in events] == ["tool_call", "tool_result", "result"]
        for event in events:
            assert set(event) == {"ts", "type", "data"}


class TestEphemeralContract:
    """All spawn modes normalize to the same TranscriptEvent contract."""

    def test_reference_sandbox_events_pass_through_unchanged(self) -> None:
        """EventLogger-produced JSONL normalizes to the shared contract.

        Ephemeral transcripts are canonical by construction (collected
        from the sandbox event log); this pins that the same
        TranscriptEvent model carries none/local/ephemeral events.
        """
        normalized = normalize_transcript_events(REFERENCE_EPHEMERAL_JSONL)
        assert [event.model_dump() for event in normalized] == REFERENCE_EPHEMERAL_JSONL
        assert {event.type for event in normalized} <= CANONICAL_EVENT_TYPES

    def test_transcript_event_accepts_all_canonical_types(self) -> None:
        """The persisted model validates every canonical event type."""
        for event_type in sorted(CANONICAL_EVENT_TYPES):
            event = TranscriptEvent(
                ts="2026-09-30T00:00:00+00:00", type=event_type, data={}
            )
            assert event.type == event_type
