"""E2E tests for OTEL tracing through the agent execution path.

Requires:
- OPENAI_API_KEY environment variable
- OTEL_ANONYMIZATION_SECRET environment variable (any value; see
  docker-compose.yaml for the dev default) — the tracing middleware
  refuses to run without it since it HMACs user_id before adding it
  as a span attribute
- Jaeger running at localhost:4317 (OTLP) and localhost:16686 (query API)
- PostgreSQL reachable at WORKFLOW_PG_* (default: localhost:5432/lightspeed,
  see docker-compose-harness.yaml); ChatWorkflowRunner requires a real
  RunStateStore/TranscriptStore, which the fixture below sets up
  (schema is migrated automatically on connect via Alembic)

Usage:
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
    OTEL_ANONYMIZATION_SECRET=lightspeed-stack-otel-anonymization-dev-default \
    uv run pytest tests/e2e/cloud_agents/test_otel_tracing_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.e2e.cloud_agents.conftest import (
    OTLP_ENDPOINT,
    SERVICE_NAME,
    check_jaeger_available,
    query_jaeger_traces,
)

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
]

_PG_HOST = os.environ.get("WORKFLOW_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("WORKFLOW_PG_PORT", "5432"))
_PG_DB = os.environ.get("WORKFLOW_PG_DB", "lightspeed")
_PG_USER = os.environ.get("WORKFLOW_PG_USER", "lightspeed")
_PG_PASSWORD = os.environ.get("WORKFLOW_PG_PASSWORD", "lightspeed")


@pytest.fixture(name="workflow_storage", autouse=True)
async def workflow_storage_fixture() -> AsyncIterator[None]:
    """Initialize WorkflowStorageFactory so ChatWorkflowRunner has a real store.

    execute_query_via_direct_executor() always routes through
    ChatWorkflowRunner, which requires a connected RunStateStore /
    TranscriptStore. Outside the FastAPI lifespan (main.py) nothing
    initializes WorkflowStorageFactory, so it must be done here.
    """
    from models.config import (
        PostgreSQLDatabaseConfiguration,
        WorkflowEngineConfiguration,
    )
    from workflow.query_executor import reset_runner
    from workflow.storage import WorkflowStorageFactory

    pg_config = PostgreSQLDatabaseConfiguration(
        host=_PG_HOST,
        port=_PG_PORT,
        db=_PG_DB,
        user=_PG_USER,
        password=_PG_PASSWORD,
    )
    wf_config = WorkflowEngineConfiguration(enabled=True)

    reset_runner()
    await WorkflowStorageFactory.initialize(pg_config, wf_config)

    yield

    reset_runner()
    await WorkflowStorageFactory.cleanup()


@pytest.fixture(name="tracer", scope="module")
def tracer_fixture() -> Any:
    """Set up OTEL tracer that exports to Jaeger."""
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    yield trace.get_tracer("e2e-test")

    provider.force_flush()
    provider.shutdown()


class TestOtelTracingE2E:
    """E2E tests verifying OTEL traces reach Jaeger."""

    @pytest.mark.asyncio
    async def test_query_direct_creates_trace(self, tracer: Any) -> None:
        """A /query/direct call creates a trace visible in Jaeger."""
        if not await check_jaeger_available():
            pytest.skip("Jaeger not available")

        from configuration import configuration
        from workflow.query_executor import execute_query_via_direct_executor

        configuration.init_from_dict(
            {
                "name": "otel-e2e-test",
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
                "user_data_collection": {"feedback_enabled": False},
                "authentication": {"module": "noop"},
            }
        )

        with tracer.start_as_current_span("e2e.query_direct") as span:
            span.set_attribute("test.name", "otel_tracing_e2e")
            result = await execute_query_via_direct_executor(
                prompt="What is 1+1? Reply with just the number.",
                provider="openai",
                model="gpt-4o-mini",
                user_id="e2e-test-user",
            )
            span.set_attribute("result.status", result.status)
            span.set_attribute("result.tokens_in", result.input_tokens)
            span.set_attribute("result.tokens_out", result.output_tokens)
            trace_id = format(span.get_span_context().trace_id, "032x")

        assert result.status == "completed"

        await asyncio.sleep(2)

        traces = await query_jaeger_traces(
            service=SERVICE_NAME,
            operation="e2e.query_direct",
        )

        assert len(traces) >= 1, (
            f"Expected trace in Jaeger for service={SERVICE_NAME}, "
            f"operation=e2e.query_direct. Found {len(traces)} traces."
        )

        found_trace = None
        for t in traces:
            if t.get("traceID") == trace_id:
                found_trace = t
                break

        assert found_trace is not None, (
            f"Trace {trace_id} not found in Jaeger. "
            f"Available trace IDs: {[t.get('traceID') for t in traces]}"
        )

        spans = found_trace.get("spans", [])
        assert len(spans) >= 1

        root_span = next(
            (s for s in spans if s.get("operationName") == "e2e.query_direct"),
            None,
        )
        assert root_span is not None

        tags = {t["key"]: t["value"] for t in root_span.get("tags", [])}
        assert tags.get("test.name") == "otel_tracing_e2e"
        assert tags.get("result.status") == "completed"

    @pytest.mark.asyncio
    async def test_trace_includes_token_metrics(self, tracer: Any) -> None:
        """Trace span includes token usage as attributes."""
        if not await check_jaeger_available():
            pytest.skip("Jaeger not available")

        from workflow.query_executor import execute_query_via_direct_executor

        with tracer.start_as_current_span("e2e.token_metrics") as span:
            result = await execute_query_via_direct_executor(
                prompt="Say hello.",
                provider="openai",
                model="gpt-4o-mini",
            )
            span.set_attribute("llm.usage.input_tokens", result.input_tokens)
            span.set_attribute("llm.usage.output_tokens", result.output_tokens)
            span.set_attribute("llm.duration_ms", result.duration_ms)
            trace_id = format(span.get_span_context().trace_id, "032x")

        await asyncio.sleep(2)

        traces = await query_jaeger_traces(
            service=SERVICE_NAME,
            operation="e2e.token_metrics",
        )

        found = next((t for t in traces if t.get("traceID") == trace_id), None)
        assert found is not None

        root_span = found["spans"][0]
        tags = {t["key"]: t["value"] for t in root_span.get("tags", [])}
        assert tags.get("llm.usage.input_tokens", 0) > 0
        assert tags.get("llm.usage.output_tokens", 0) > 0
