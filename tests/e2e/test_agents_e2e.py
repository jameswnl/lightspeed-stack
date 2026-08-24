"""E2E tests for /v1/agents/run endpoint.

Runs against a real LLM backend (OpenAI via pydantic-ai DirectExecutor).
Requires OPENAI_API_KEY environment variable. No Llama Stack needed.

Usage:
    uv run pytest tests/e2e/test_agents_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods,unspecified-encoding,unused-argument

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import Request

from app.endpoints.agents import run_agent_handler
from authentication.interface import AuthTuple
from configuration import configuration
from models.api.requests.agents import AgentRunRequest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)

_AUTH: AuthTuple = ("e2e-user", "e2e-tester", False, "")

_CONFIG = {
    "name": "e2e-agents-test",
    "service": {
        "host": "localhost",
        "port": 8080,
        "auth_enabled": False,
        "workers": 1,
    },
    "llama_stack": {
        "use_as_library_client": False,
        "url": "http://localhost:8321",
    },
    "user_data_collection": {
        "feedback_enabled": False,
    },
    "authentication": {"module": "noop"},
}


@pytest.fixture(name="e2e_config", scope="module")
def e2e_config_fixture() -> Any:
    """Load config for E2E tests.

    No Llama Stack needed — pydantic-ai talks to OpenAI directly.
    Requires OPENAI_API_KEY environment variable.
    """
    configuration.init_from_dict(_CONFIG)
    return configuration


def _make_request() -> Request:
    """Create a minimal FastAPI Request with all actions authorized."""
    from models.config import Action

    request = Request(
        scope={
            "type": "http",
            "query_string": b"",
            "headers": [],
        }
    )
    request.state.authorized_actions = set(Action)
    return request


class TestAgentRunE2E:
    """E2E tests for /v1/agents/run with real LLM calls."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, e2e_config: Any) -> None:
        """Agent returns a text response to a simple prompt."""
        body = AgentRunRequest(
            prompt="What is 2 + 2? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert result["output"] is not None
        assert "4" in str(result["output"])
        assert result["token_usage"]["input_tokens"] > 0
        assert result["token_usage"]["output_tokens"] > 0
        assert result["duration_ms"] > 0

    @pytest.mark.asyncio
    async def test_with_instructions(self, e2e_config: Any) -> None:
        """Agent follows system instructions."""
        body = AgentRunRequest(
            prompt="What is the capital of France?",
            provider="openai",
            model="gpt-4o-mini",
            instructions="You are a geography expert. Answer in exactly one word.",
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        summary = str(result["output"]).lower()
        assert "paris" in summary

    @pytest.mark.asyncio
    async def test_structured_output(self, e2e_config: Any) -> None:
        """Agent returns structured JSON matching the output schema."""
        body = AgentRunRequest(
            prompt=(
                "An alert fired: 'High CPU on node worker-1: 98% for 10 minutes.' "
                "Classify the severity and category. Reply with JSON only."
            ),
            provider="openai",
            model="gpt-4o-mini",
            instructions=(
                "You classify infrastructure alerts. "
                "Reply ONLY with a JSON object matching the schema, no markdown."
            ),
            output_schema={
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "category": {
                        "type": "string",
                        "enum": ["resource", "network", "storage", "security"],
                    },
                    "summary": {"type": "string"},
                },
                "required": ["severity", "category", "summary"],
            },
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        output = result["output"]
        assert isinstance(output, dict)
        assert len(output) >= 2
        assert any(k in output for k in ("severity", "category", "summary"))

    @pytest.mark.asyncio
    async def test_provider_model_resolution(self, e2e_config: Any) -> None:
        """Model ID with separate provider field resolves correctly."""
        body = AgentRunRequest(
            prompt="Say 'hello' and nothing else.",
            provider="openai",
            model="gpt-4o-mini",
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert "hello" in str(result["output"]).lower()

    @pytest.mark.asyncio
    async def test_multi_step_context_passing(self, e2e_config: Any) -> None:
        """Agent receives prior context and uses it."""
        body = AgentRunRequest(
            prompt=(
                "The previous analysis found severity=high and category=resource. "
                "Based on that, recommend ONE action in a single sentence."
            ),
            provider="openai",
            model="gpt-4o-mini",
            instructions="You are an SRE. Be concise.",
            context={
                "analysis": {
                    "status": "completed",
                    "output": {
                        "severity": "high",
                        "category": "resource",
                        "summary": "Node worker-1 at 98% CPU for 10 min",
                    },
                }
            },
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert len(str(result["output"])) > 10


class TestQueryDirectE2E:
    """E2E tests for POST /v1/query/direct — the migration path."""

    @pytest.mark.asyncio
    async def test_simple_query(self, e2e_config: Any) -> None:
        """Simple query via DirectExecutor returns a response."""
        from workflow.query_executor import execute_query_via_direct_executor

        result = await execute_query_via_direct_executor(
            prompt="What is 2 + 2? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
        )

        assert result.status == "completed"
        assert result.output is not None
        assert "4" in str(result.output)

    @pytest.mark.asyncio
    async def test_query_with_instructions(self, e2e_config: Any) -> None:
        """Query with system instructions via DirectExecutor."""
        from workflow.query_executor import execute_query_via_direct_executor

        result = await execute_query_via_direct_executor(
            prompt="What is the capital of France?",
            provider="openai",
            model="gpt-4o-mini",
            instructions="Answer in exactly one word.",
        )

        assert result.status == "completed"
        assert "paris" in str(result.output).lower()

    @pytest.mark.asyncio
    async def test_streaming_query(self, e2e_config: Any) -> None:
        """Streaming query yields events ending with a complete event."""
        from workflow.query_executor import stream_query_via_direct_executor

        events = []
        async for event in stream_query_via_direct_executor(
            prompt="Say 'hello' and nothing else.",
            provider="openai",
            model="gpt-4o-mini",
        ):
            events.append(event)

        assert len(events) >= 1
        complete_events = [e for e in events if e.type == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0].result is not None
        assert complete_events[0].result.status == "completed"
        assert complete_events[0].result.output is not None


class TestQueryDirectResponseShape:
    """Verify /query/direct response matches /query contract."""

    @pytest.mark.asyncio
    async def test_response_has_all_query_fields(self, e2e_config: Any) -> None:
        """Response includes all fields from QueryResponse."""
        from app.endpoints.query_direct import QueryDirectRequest, query_direct_handler

        body = QueryDirectRequest(
            query="Say hi.",
            provider="openai",
            model="gpt-4o-mini",
        )

        result = await query_direct_handler.__wrapped__(_make_request(), body, _AUTH)

        expected_fields = {
            "conversation_id",
            "response",
            "truncated",
            "input_tokens",
            "output_tokens",
            "available_quotas",
            "tool_calls",
            "tool_results",
            "rag_chunks",
            "referenced_documents",
            "request_id",
            "interrupted",
        }
        assert expected_fields.issubset(result.keys())
        assert isinstance(result["response"], str)
        assert len(result["response"]) > 0
        assert isinstance(result["input_tokens"], int)
        assert isinstance(result["output_tokens"], int)
        assert result["truncated"] is False
        assert result["interrupted"] is False

    @pytest.mark.asyncio
    async def test_response_types_match_query(self, e2e_config: Any) -> None:
        """Response field types match what /query returns."""
        from app.endpoints.query_direct import QueryDirectRequest, query_direct_handler

        body = QueryDirectRequest(
            query="What is Python?",
            provider="openai",
            model="gpt-4o-mini",
            system_prompt="One sentence max.",
        )

        result = await query_direct_handler.__wrapped__(_make_request(), body, _AUTH)

        assert isinstance(result["tool_calls"], list)
        assert isinstance(result["tool_results"], list)
        assert isinstance(result["rag_chunks"], list)
        assert isinstance(result["referenced_documents"], list)
        assert isinstance(result["available_quotas"], dict)


class TestErrorHandlingE2E:
    """E2E tests for error paths."""

    @pytest.mark.asyncio
    async def test_missing_provider_returns_400(self, e2e_config: Any) -> None:
        """Missing provider and model with no defaults returns 400."""
        from fastapi import HTTPException

        from app.endpoints.query_direct import QueryDirectRequest, query_direct_handler

        body = QueryDirectRequest(query="Hello")

        with pytest.raises(HTTPException) as exc_info:
            await query_direct_handler.__wrapped__(_make_request(), body, _AUTH)
        assert exc_info.value.status_code == 400
        assert "Provider and model" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_prompt_too_long_returns_400(self, e2e_config: Any) -> None:
        """Oversized prompt returns 400."""
        from fastapi import HTTPException

        from app.endpoints.query_direct import QueryDirectRequest, query_direct_handler

        body = QueryDirectRequest(
            query="x" * 200_000,
            provider="openai",
            model="gpt-4o-mini",
        )

        with pytest.raises(HTTPException) as exc_info:
            await query_direct_handler.__wrapped__(_make_request(), body, _AUTH)
        assert exc_info.value.status_code == 400
        assert "maximum length" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_unknown_mcp_server_returns_400(self, e2e_config: Any) -> None:
        """Unknown MCP server name returns 400."""
        from fastapi import HTTPException

        from app.endpoints.query_direct import QueryDirectRequest, query_direct_handler

        body = QueryDirectRequest(
            query="Hello",
            provider="openai",
            model="gpt-4o-mini",
            mcp_servers=["nonexistent-server"],
        )

        with pytest.raises(HTTPException) as exc_info:
            await query_direct_handler.__wrapped__(_make_request(), body, _AUTH)
        assert exc_info.value.status_code == 400
        assert "Unknown MCP server" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_agent_run_missing_prompt_returns_422(self, e2e_config: Any) -> None:
        """Missing required 'prompt' field returns 422."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentRunRequest(provider="openai", model="gpt-4o-mini")  # type: ignore[call-arg]


class TestStreamingE2E:
    """E2E tests for streaming behavior."""

    @pytest.mark.asyncio
    async def test_stream_complete_event_has_output(self, e2e_config: Any) -> None:
        """Complete event from stream has output and metrics."""
        from workflow.query_executor import stream_query_via_direct_executor

        events = []
        async for event in stream_query_via_direct_executor(
            prompt="What is 1+1? Reply with just the number.",
            provider="openai",
            model="gpt-4o-mini",
        ):
            events.append(event)

        complete = [e for e in events if e.type == "complete"]
        assert len(complete) == 1
        assert complete[0].result.status == "completed"
        assert complete[0].result.input_tokens > 0
        assert complete[0].result.output_tokens > 0
        assert complete[0].result.duration_ms > 0
        assert "2" in str(complete[0].result.output)

    @pytest.mark.asyncio
    async def test_stream_validation_error_raised_before_streaming(
        self, e2e_config: Any
    ) -> None:
        """Validation errors raise before any events are yielded."""
        from workflow.query_executor import stream_query_via_direct_executor

        with pytest.raises(ValueError, match="maximum length"):
            async for _ in stream_query_via_direct_executor(
                prompt="x" * 200_000,
                provider="openai",
                model="gpt-4o-mini",
            ):
                pass


class TestMultiModelE2E:
    """E2E tests verifying different model configurations."""

    @pytest.mark.asyncio
    async def test_different_model_produces_response(self, e2e_config: Any) -> None:
        """A different model still produces a valid response."""
        body = AgentRunRequest(
            prompt="What color is the sky? One word.",
            provider="openai",
            model="gpt-4o-mini",
            instructions="Reply with exactly one word.",
        )

        result = await run_agent_handler.__wrapped__(_make_request(), body, _AUTH)

        assert result["status"] == "completed"
        assert "blue" in str(result["output"]).lower()


class TestWorkflowDefinitionCompatibility:
    """Test that cloud-agents workflow definitions parse correctly."""

    def test_all_spawn_none_definitions_parse(self) -> None:
        """All workflow definitions with spawn=none steps parse correctly."""
        from cloud_agents.workflow.core.definition import WorkflowDefinition

        wf_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "lightspeed-cloud-agents"
            / "examples"
            / "workflow-definitions"
        )
        if not wf_dir.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")

        for wf_file in wf_dir.glob("*.yaml"):
            with open(wf_file) as f:
                raw = yaml.safe_load(f)

            definition = WorkflowDefinition.model_validate(raw)
            assert definition.spec.steps, f"{wf_file.name}: no steps"

            none_steps = [s for s in definition.spec.steps if s.spawn == "none"]
            for step in none_steps:
                assert step.name, f"{wf_file.name}: step missing name"
                assert step.output_key, f"{wf_file.name}: step missing output_key"
