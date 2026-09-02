"""E2E test that OTEL traces flow when the runner is built by the factory.

This is the companion to test_workflow_tracing_e2e.py that closes the gap
called out in review of jameswnl/lightspeed-stack#45: that file installs its
*own* process-global TracerProvider in a fixture and constructs
LocalWorkflowRunner directly, so it proves cloud-agents emits spans but
*bypasses* lightspeed-stack's real wiring. The running service never does
that -- it builds the runner through
`workflow.executor_factory.create_workflow_runner()`, which is the only place
lightspeed-stack calls `init_tracing("workflow-runner")` to install a
provider. Before #45 that call was missing, so a factory-built runner emitted
NoOp spans (dropped) even with OTEL_EXPORTER_OTLP_ENDPOINT set.

This test therefore installs NO provider of its own. It drives
create_workflow_runner() exactly as the endpoint handler does and asserts:

1. After the factory runs, the process-global provider is the real SDK
   provider named "workflow-runner" (not a NoOp/Proxy) -- i.e. the factory,
   not the test, wired tracing. This alone regresses if #45 is reverted.
2. A real 2-step run through that factory-built runner delivers step.execute
   spans to Jaeger under service "workflow-runner", tagged with workflow.id.

Requires:
- OPENAI_API_KEY environment variable
- Jaeger running at localhost:4317 (OTLP) and localhost:16686 (query API)
- PostgreSQL reachable at WORKFLOW_PG_* (default: localhost:5432/lightspeed,
  see docker-compose-harness.yaml)

Usage:
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
    uv run pytest tests/e2e/cloud_agents/test_workflow_factory_tracing_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,protected-access,too-few-public-methods

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest

from tests.e2e.cloud_agents.jaeger_helpers import (
    OTLP_ENDPOINT,
    check_jaeger_available,
    query_jaeger_traces,
    spans_with_operation,
)
from tests.e2e.cloud_agents.workflow_e2e_helpers import (
    DB_URL,
    start_and_await_completion,
    two_step_definition,
)

# The service name create_workflow_runner() installs via init_tracing().
FACTORY_SERVICE_NAME = "workflow-runner"

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
]


@pytest.fixture(name="reset_tracing")
def reset_tracing_fixture() -> Iterator[None]:
    """Clear any previously-installed TracerProvider before the factory runs.

    OpenTelemetry allows exactly one TracerProvider install per process and
    cloud-agents' init_tracing() has a module-level `_initialized` guard, so
    if another test (or a prior run) already installed a provider, the
    factory's init_tracing("workflow-runner") would be a silent no-op and
    this test would assert against the wrong provider. Resetting both here
    makes the test deterministic and order-independent: the factory becomes
    the thing that installs the provider, which is exactly what we want to
    verify. We install nothing ourselves -- that's the whole point.

    Also ensures OTEL_EXPORTER_OTLP_ENDPOINT is set so init_tracing wires a
    real exporter rather than taking its NoOp branch.
    """
    import cloud_agents.runtime.tracing as ca_tracing
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
    from opentelemetry.util._once import Once

    prev_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = OTLP_ENDPOINT

    # Reset the OTEL global-provider "set once" latch and cloud-agents' guard.
    trace._TRACER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    ca_tracing._initialized = False

    yield

    # Flush and tear down whatever the factory installed so spans aren't lost
    # and a later test starts clean. Only a real SDK provider has these; a
    # NoOp/Proxy (factory took its no-op branch) does not.
    provider = trace.get_tracer_provider()
    if isinstance(provider, SdkTracerProvider):
        try:
            provider.force_flush()
            provider.shutdown()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    trace._TRACER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    ca_tracing._initialized = False
    if prev_endpoint is None:
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    else:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = prev_endpoint


@pytest.fixture(name="initialized_storage")
async def initialized_storage_fixture() -> AsyncIterator[None]:
    """Populate WorkflowStorageFactory's stores against the harness PostgreSQL.

    create_workflow_runner() pulls its stores from WorkflowStorageFactory,
    which raises unless its singleton stores are set. We build and connect
    them directly from DB_URL -- the same RunStateStore(db_url=...) pattern
    the companion test's run_state_store fixture uses -- rather than going
    through WorkflowStorageFactory.initialize(), which would require
    constructing the full PostgreSQL/WorkflowEngine config models just to
    hand back the same connection string. Skips if PostgreSQL is unreachable.
    """
    from cloud_agents.storage.run_state_store import RunStateStore
    from cloud_agents.storage.transcript_store import TranscriptStore

    from workflow.storage import WorkflowStorageFactory

    run_state_store = RunStateStore(db_url=DB_URL)
    try:
        await run_state_store.connect()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    transcript_store = TranscriptStore(db_url=DB_URL, retention_days=30)
    await transcript_store.connect()

    WorkflowStorageFactory.reset()
    WorkflowStorageFactory._run_state_store = run_state_store
    WorkflowStorageFactory._transcript_store = transcript_store

    yield

    await WorkflowStorageFactory.cleanup()


class TestFactoryPathTracing:
    """Tracing wired by create_workflow_runner(), not by the test."""

    @pytest.mark.asyncio
    async def test_factory_installs_provider_and_delivers_spans(
        self, reset_tracing: None, initialized_storage: None
    ) -> None:
        """The factory installs a real 'workflow-runner' provider and spans ship.

        Drives create_workflow_runner() exactly as the endpoint does, then
        checks both that the factory (not this test) installed a real
        exporting provider and that a real run's step spans reach Jaeger.
        """
        _ = (reset_tracing, initialized_storage)
        if not await check_jaeger_available():
            pytest.skip("Jaeger not available")

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider

        from workflow.executor_factory import create_workflow_runner

        # Sanity: nothing has installed a real provider yet (fixture reset it).
        assert not isinstance(
            trace.get_tracer_provider(), SdkTracerProvider
        ), "A real TracerProvider was already installed before the factory ran"

        runner = create_workflow_runner()

        # (1) The factory -- not this test -- installed a real SDK provider
        # named "workflow-runner". This is the exact wiring #45 added; it
        # regresses to a NoOp/Proxy provider if that call is removed.
        provider = trace.get_tracer_provider()
        assert isinstance(provider, SdkTracerProvider), (
            "create_workflow_runner() did not install a real TracerProvider "
            f"(got {type(provider).__name__}); init_tracing wiring is missing."
        )
        service_name = provider.resource.attributes.get("service.name")
        assert service_name == FACTORY_SERVICE_NAME, (
            f"Factory-installed provider has service.name={service_name!r}, "
            f"expected {FACTORY_SERVICE_NAME!r}."
        )

        # (2) A real run through the factory-built runner delivers spans to
        # Jaeger under that service name.
        definition = two_step_definition("e2e-factory-tracing")
        workflow_id = await start_and_await_completion(runner, definition)

        # Force spans out of the BatchSpanProcessor before we query.
        provider.force_flush()
        await asyncio.sleep(2)

        traces = await query_jaeger_traces(
            service=FACTORY_SERVICE_NAME,
            operation="step.execute",
            tags={"workflow.id": workflow_id},
        )
        step_spans = spans_with_operation(traces, "step.execute")
        assert len(step_spans) >= 2, (
            f"Expected >=2 step.execute spans tagged workflow.id={workflow_id} "
            f"under service {FACTORY_SERVICE_NAME!r} in Jaeger, found "
            f"{len(step_spans)}. The factory-built runner's spans are not "
            "reaching Jaeger -- the init_tracing wiring in "
            "create_workflow_runner() is not taking effect."
        )
        for span in step_spans:
            tags = {t["key"]: t["value"] for t in span.get("tags", [])}
            assert tags.get("workflow.id") == workflow_id, (
                f"Span {span.get('spanID')} has "
                f"workflow.id={tags.get('workflow.id')!r}, expected "
                f"{workflow_id!r}"
            )
