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
import os
from pathlib import Path
from typing import Any, Optional

import pytest
from cloud_agents.workflow.core.models import (
    StepTranscript,
    TranscriptEvent,
    normalize_transcript_events,
)
from cloud_agents.workflow.executor.local.executor import LocalWorkflowRunner

from tests.e2e.cloud_agents.mock_llm_server import MockResponsesServer
from tests.e2e.cloud_agents.workflow_e2e_helpers import assert_canonical_events

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
        self.turn_messages: dict[str, list[tuple[str, list[dict[str, Any]]]]] = {}

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
        self.turn_messages.setdefault(workflow_id, []).append(
            (step_name, messages or [])
        )

    async def get(self, workflow_id: str, step_name: str) -> Optional[StepTranscript]:
        """Return a saved transcript, or None."""
        return self.saved.get(workflow_id, {}).get(step_name)

    async def list_steps(self, workflow_id: str) -> list[str]:
        """List saved step names in save order."""
        return list(self.saved.get(workflow_id, {}))

    async def load_recent_turns(
        self, workflow_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return the most recent turns as step_name/messages dicts."""
        turns = self.turn_messages.get(workflow_id, [])
        return [
            {"step_name": step_name, "messages": messages}
            for step_name, messages in turns[-limit:]
        ]


class ParityEnv:  # pylint: disable=too-few-public-methods
    """Mock LLM server + env wiring shared by spawn:none and spawn:local."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capture_requests: bool = False,
        q: int = 7,
    ) -> None:
        """Start the scripted server and point the stack's env at it."""
        self.server = MockResponsesServer(
            tool_call={
                "name": PARITY_TOOL,
                "arguments": {"q": q},
                "result_text": "parity final answer",
            },
            capture_requests=capture_requests,
        )
        self.server.start()
        monkeypatch.setenv("OPENAI_BASE_URL", self.server.base_url)
        monkeypatch.setenv("OPENAI_API_KEY", "parity-test-dummy-key")
        monkeypatch.setenv(
            "CLOUD_AGENTS_TOOLS_MODULE", "tests.integration.cloud_agents.parity_tools"
        )
        # The spawn:local subprocess child must import the parity tools
        # module. It currently inherits this process's environment and
        # working directory (repo root) -- explicit PYTHONPATH makes the
        # import independent of cwd. If cloud-agents #269 trims the
        # child env (lightspeed-stack #51 gap G8), these tests will need
        # the trimmed-but-allowlisted equivalents.
        repo_root = str(Path(__file__).resolve().parents[3])
        existing = os.environ.get("PYTHONPATH")
        monkeypatch.setenv(
            "PYTHONPATH",
            f"{repo_root}{os.pathsep}{existing}" if existing else repo_root,
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
        assert_canonical_events(events)
        assert tool_call["data"]["name"] == PARITY_TOOL
        assert tool_call["data"]["input"] == '{"q": 7}'
        assert tool_result["data"] == {"output": "parity:7"}
        assert result["data"]["text"] == "parity final answer"
        # Documented gap vs ephemeral: aggregate usage, unknown cost.
        assert result["data"]["cost_usd"] is None
        assert result["data"]["input_tokens"] > 0
        assert result["data"]["output_tokens"] > 0

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
        assert_canonical_events(events)
        assert tool_call["data"]["name"] == PARITY_TOOL
        assert tool_call["data"]["input"] == '{"q": 7}'
        assert tool_result["data"] == {"output": "parity:7"}
        assert result["data"]["text"] == "parity final answer"
        assert result["data"]["cost_usd"] is None

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

    @pytest.mark.parametrize("spawn", ["none", "local"])
    async def test_failed_run_emits_error_event(
        self, monkeypatch: pytest.MonkeyPatch, spawn: str
    ) -> None:
        """A failing run persists a canonical error event, in both modes.

        spawn:none fails in DirectExecutor; spawn:local fails in the
        subprocess child (dead OPENAI_BASE_URL is inherited) and the
        error event crosses the stdin/stdout boundary.
        """
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
            runner, _workflow_input(_one_step_definition(spawn))
        )
        assert outcome["status"] == "failed", outcome

        events = await _step_events(runner, outcome["workflow_id"])
        assert [e["type"] for e in events] == ["error"]
        assert set(events[0]["data"]) == {"message"}
        assert events[0]["data"]["message"]

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
        events = transcripts["result"]["events"]
        assert [e["type"] for e in events] == ["tool_call", "tool_result", "result"]
        assert_canonical_events(events)


class TestQueryDirectFollowUpTurnAfterToolCall:  # pylint: disable=too-few-public-methods
    """/query/direct conversation continuity across tool-call turns.

    Regression (lightspeed-stack #58 review): canonical transcript
    events carry no tool_call_id, so the conversation-history replay
    must synthesize ids that pair each tool result with its tool call.
    When the pair does NOT match, pydantic-ai silently replaces the tool
    result with an "interrupted" marker in every follow-up request --
    the real tool output never reaches the provider again. The test
    asserts the follow-up request carries the ACTUAL tool output with
    ids that pair.
    """

    @pytest.mark.parametrize("q", [7, int("1" * 2100)], ids=["small", "truncated"])
    async def test_second_turn_after_tool_call_turn_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, q: int
    ) -> None:
        """A follow-up turn replays the real tool result, not a repair marker."""
        from cloud_agents.workflow.executor.chat.runner import (  # pylint: disable=import-outside-toplevel
            ChatWorkflowConfig,
            ChatWorkflowRunner,
        )

        parity_env = ParityEnv(monkeypatch, capture_requests=True, q=q)
        try:
            transcript_store = InMemoryTranscriptStore()
            runner = ChatWorkflowRunner(
                run_store=InMemoryRunStateStore(),
                transcript_store=transcript_store,
                config=ChatWorkflowConfig(
                    provider={"name": "openai", "model": "gpt-4o-mock"},
                    tools=[PARITY_TOOL],
                    tools_module="tests.integration.cloud_agents.parity_tools",
                    spawn="none",
                    timeout_seconds=60,
                ),
            )
            conversation_id = await runner.start({"user_id": "parity-test"})

            first = await runner.send_message(conversation_id, "Run the parity probe")
            assert first.status == "completed", first.error

            # The tool turn saved canonical events and tool conversation
            # messages (no ids) that the next turn replays as history.
            turn0_events = [
                e.model_dump()
                for e in transcript_store.saved[conversation_id]["turn-0"].events
            ]
            assert [e["type"] for e in turn0_events] == [
                "tool_call",
                "tool_result",
                "result",
            ]
            assert_canonical_events(turn0_events)

            request_count = len(parity_env.server.requests)
            second = await runner.send_message(conversation_id, "Summarize that")
            assert second.status == "completed", second.error
            assert second.output is not None

            if q != 7:
                # The bounded audit arguments cannot be replayed safely.
                # The next request retains user/assistant context while
                # omitting both halves of the incomplete tool exchange.
                replay_input = parity_env.server.requests[request_count]["input"]
                assert not any(
                    item.get("type") in {"function_call", "function_call_output"}
                    for item in replay_input
                )
                assert len(turn0_events[0]["data"]["input"]) == 2000
                return

            # The regression: with mismatched synthesized call/result ids,
            # pydantic-ai replaces the tool output with an "interrupted"
            # marker when serializing history. The follow-up request must
            # carry the REAL tool output, with ids that pair.
            follow_ups = [
                req
                for req in parity_env.server.requests
                if isinstance(req.get("input"), list)
                and any(
                    isinstance(item, dict)
                    and item.get("type") == "function_call_output"
                    for item in req["input"]
                )
            ]
            assert follow_ups, "no follow-up request replayed the tool turn"
            input_items = follow_ups[-1]["input"]
            call_ids = {
                item.get("call_id")
                for item in input_items
                if isinstance(item, dict) and item.get("type") == "function_call"
            }
            outputs = [
                item
                for item in input_items
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            ]
            assert len(outputs) == 1
            assert outputs[0]["call_id"] in call_ids
            assert outputs[0]["output"] == "parity:7"
        finally:
            parity_env.stop()


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


@pytest.mark.parametrize("spawn", ["none", "local"])
@pytest.mark.parametrize("with_tools", [False, True], ids=["model-request", "agent"])
async def test_output_schema_parsing_failure_persists_error(
    monkeypatch: pytest.MonkeyPatch, spawn: str, with_tools: bool
) -> None:
    """Non-JSON schema output exposes an error through the transcript accessor."""
    # Deliberately violate native structured output instead of using the
    # mock's default schema-conforming placeholder.
    monkeypatch.setattr(
        "tests.e2e.cloud_agents.mock_llm_server._response_text_for_request",
        lambda _request, text: text,
    )
    parity_env = ParityEnv(monkeypatch)
    try:
        runner = LocalWorkflowRunner(
            run_state_store=InMemoryRunStateStore(),
            transcript_store=InMemoryTranscriptStore(),
        )
        definition = _one_step_definition(spawn)
        step = definition["spec"]["steps"][0]
        step["output_schema"] = {"type": "object"}
        if not with_tools:
            step.pop("tools")
        outcome = await _run_to_terminal(runner, _workflow_input(definition))
        assert outcome["status"] == "failed", outcome
        events = await _step_events(runner, outcome["workflow_id"])
        assert_canonical_events(events)
        assert events[-1]["type"] == "error"
        assert "non-JSON" in events[-1]["data"]["message"]
    finally:
        parity_env.stop()
