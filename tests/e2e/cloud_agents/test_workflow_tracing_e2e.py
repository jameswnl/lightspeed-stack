"""E2E tests verifying OTEL trace chaining for LocalWorkflowRunner runs.

Companion to test_otel_tracing_e2e.py (which covers ChatWorkflowRunner) —
this file covers the workflow-runner path (LocalWorkflowRunner, i.e.
/v1/workflows/*). See jameswnl/lightspeed-stack#20 for full context and
jameswnl/lightspeed-cloud-agents#179 for the companion cloud-agents issue.

LocalWorkflowRunner already wraps every agent step's executor with
MiddlewareExecutor(..., tracer=_tracer) (graph_translator.py), which opens a
real `step.execute` span per step tagged with `workflow.id`/`step.name`, and
(since jameswnl/lightspeed-cloud-agents#181) `session.id` when provided. What
this test actually verifies is open, not assumed:

1. Every step span for a run carries the correct `workflow.id` and
   `session.id` attributes (expected to already work).
2. Whether all step spans of one live (non-paused) run share a single
   trace_id (genuinely unverified — see cloud-agents#179 item 3, still
   open; unrelated to and not addressed by #181).
3. Whether workflow.id still correlates spans across an approval
   pause/resume boundary, and whether a post-resume span carries an OTEL
   span Link back to the pre-pause trace (cloud-agents#179 item 2 --
   implemented in #181, asserted for real below).

Requires:
- OPENAI_API_KEY environment variable
- Jaeger running at localhost:4317 (OTLP) and localhost:16686 (query API)
- PostgreSQL reachable at WORKFLOW_PG_* (default: localhost:5432/lightspeed,
  see docker-compose-harness.yaml) — LocalWorkflowRunner.approve() requires a
  real RunStateStore, so the pause/resume test needs one (schema is migrated
  automatically on connect via Alembic)

Usage:
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
    uv run pytest tests/e2e/cloud_agents/test_workflow_tracing_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.e2e.cloud_agents.jaeger_helpers import (
    OTLP_ENDPOINT,
    SERVICE_NAME,
    check_jaeger_available,
    query_jaeger_traces,
    spans_with_operation,
)

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
]

_PG_HOST = os.environ.get("WORKFLOW_PG_HOST", "localhost")
_PG_PORT = os.environ.get("WORKFLOW_PG_PORT", "5432")
_PG_DB = os.environ.get("WORKFLOW_PG_DB", "lightspeed")
_PG_USER = os.environ.get("WORKFLOW_PG_USER", "lightspeed")
_PG_PASSWORD = os.environ.get("WORKFLOW_PG_PASSWORD", "lightspeed")
_DB_URL = f"postgresql://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_PG_DB}"

_PROVIDER = {"name": "openai", "model": "gpt-4o-mini"}


def _find_workflow_definitions_dir() -> Path:
    """Locate the lightspeed-cloud-agents example workflow definitions dir.

    Walks upward from this file rather than assuming a fixed number of
    parent levels, since that count differs between a plain checkout and a
    git-worktree checkout (e.g. .claude/worktrees/<name>/tests/...).

    Returns:
        The workflow-definitions directory, or a non-existent sentinel path
        if no lightspeed-cloud-agents checkout is found nearby -- callers
        should check `.exists()` and skip.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = (
            parent / "lightspeed-cloud-agents" / "examples" / "workflow-definitions"
        )
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / "lightspeed-cloud-agents-not-found"


_WF_DIR = _find_workflow_definitions_dir()


def _two_step_definition(workflow_name: str) -> dict[str, Any]:
    """A minimal 2-step, spawn:none, no-approval workflow definition.

    Parameters:
        workflow_name: Name to embed in the definition's metadata.

    Returns:
        A workflow definition dict matching the cloud-agents YAML schema.
    """
    return {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": workflow_name},
        "spec": {
            "steps": [
                {
                    "name": "step-a",
                    "type": "agent",
                    "spawn": "none",
                    "output_key": "result_a",
                    "prompt": "Reply with exactly one word: apple",
                    "timeout_seconds": 30,
                },
                {
                    "name": "step-b",
                    "type": "agent",
                    "spawn": "none",
                    "output_key": "result_b",
                    "prompt": "Reply with exactly one word: banana",
                    "timeout_seconds": 30,
                },
            ]
        },
    }


@pytest.fixture(name="tracer", scope="module")
def tracer_fixture() -> Any:
    """Set up OTEL tracer that exports to Jaeger.

    Installs a process-global TracerProvider. LocalWorkflowRunner's step
    spans (opened via cloud_agents.runtime.tracing.get_tracer(), a thin
    wrapper around opentelemetry.trace.get_tracer()) resolve against this
    same global provider since they run in the same process.
    """
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


@pytest.fixture(name="run_state_store")
async def run_state_store_fixture() -> AsyncIterator[Any]:
    """Real PostgreSQL-backed RunStateStore, matching harness creds.

    LocalWorkflowRunner.approve() raises RuntimeError without a store, so
    the pause/resume test needs a real one. The no-pause test also accepts
    one for consistency (it exercises get_status()).
    """
    from cloud_agents.storage.run_state_store import RunStateStore

    store = RunStateStore(db_url=_DB_URL)
    try:
        await store.connect()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    yield store

    await store.close()


async def _poll_until_status(
    runner: Any,
    workflow_id: str,
    target_statuses: set[str],
    timeout: float = 60.0,
) -> Any:
    """Poll get_status() until the run reaches one of the target statuses.

    Deliberately avoids reaching into LocalWorkflowRunner's private
    `_running` task registry -- a real caller (e.g. the
    GET /v1/workflows/{id} HTTP endpoint) only ever observes progress via
    get_status(), never via an in-process task handle, so polling here
    keeps the test representative of real usage.

    Parameters:
        runner: LocalWorkflowRunner instance.
        workflow_id: Target run.
        target_statuses: Statuses that end the poll loop.
        timeout: Max seconds to wait before giving up.

    Returns:
        The WorkflowStatus once a target status is reached.

    Raises:
        AssertionError: If the timeout elapses before a target status.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        status = await runner.get_status(workflow_id)
        if status.status in target_statuses:
            return status
        if loop.time() >= deadline:
            raise AssertionError(
                f"Timed out after {timeout}s waiting for workflow "
                f"{workflow_id} to reach one of {sorted(target_statuses)}; "
                f"last status={status.status}"
            )
        await asyncio.sleep(0.5)


async def _start_and_await_completion(
    runner: Any, definition: dict[str, Any], session_id: str | None = None
) -> str:
    """Start a workflow run and poll until it reaches a terminal status.

    Parameters:
        runner: LocalWorkflowRunner instance.
        definition: Workflow definition dict.
        session_id: Optional session_id to thread into the run's input.

    Returns:
        The new run's workflow_id, once it has completed.
    """
    start_input = {"definition": definition, "provider": _PROVIDER}
    if session_id is not None:
        start_input["session_id"] = session_id
    workflow_id = await runner.start(start_input)
    status = await _poll_until_status(
        runner, workflow_id, {"completed", "failed", "cancelled"}
    )
    assert status.status == "completed", (
        f"Workflow did not complete cleanly: status={status.status}, "
        f"steps={status.steps}"
    )
    return workflow_id


def _assert_workflow_id_tag(spans: list[dict[str, Any]], workflow_id: str) -> None:
    """Assert every span in the list carries the expected workflow.id tag.

    Parameters:
        spans: Span dicts (e.g. from spans_with_operation()).
        workflow_id: Expected value of the workflow.id tag.
    """
    for span in spans:
        tags = {t["key"]: t["value"] for t in span.get("tags", [])}
        assert tags.get("workflow.id") == workflow_id, (
            f"Span {span.get('spanID')} ({span.get('operationName')}) has "
            f"workflow.id={tags.get('workflow.id')!r}, expected {workflow_id!r}"
        )


def _assert_session_id_tag(spans: list[dict[str, Any]], session_id: str) -> None:
    """Assert every span in the list carries the expected session.id tag.

    Set by cloud-agents' TracingMiddleware (workflow/executor/middleware.py)
    when StepMetadata.session_id is non-None -- see cloud-agents#179 item 1 /
    jameswnl/lightspeed-cloud-agents#181.

    Parameters:
        spans: Span dicts (e.g. from spans_with_operation()).
        session_id: Expected value of the session.id tag.
    """
    for span in spans:
        tags = {t["key"]: t["value"] for t in span.get("tags", [])}
        assert tags.get("session.id") == session_id, (
            f"Span {span.get('spanID')} ({span.get('operationName')}) has "
            f"session.id={tags.get('session.id')!r}, expected {session_id!r}"
        )


class TestNoPauseWorkflowTracing:
    """Trace chaining for a workflow run with no approval pause."""

    @pytest.mark.asyncio
    async def test_steps_share_workflow_id_and_trace_id(
        self, tracer: Any, run_state_store: Any
    ) -> None:
        """Every step span carries workflow.id/session.id; steps ideally share trace_id.

        The workflow.id and session.id assertions are expected to already
        hold (see graph_translator.py's MiddlewareExecutor wiring and
        jameswnl/lightspeed-cloud-agents#181 for session.id specifically).
        The shared trace_id assertion is a real, currently-unverified
        question -- LocalWorkflowRunner.start() fires the run via
        asyncio.create_task(), and whether that preserves a single trace
        across all step spans with no caller-provided span is what this
        test exists to answer.
        """
        _ = tracer
        if not await check_jaeger_available():
            pytest.skip("Jaeger not available")

        from cloud_agents.workflow.executor.local.executor import (
            LocalWorkflowRunner,
        )

        runner = LocalWorkflowRunner(run_state_store=run_state_store)
        definition = _two_step_definition("e2e-tracing-no-pause")
        session_id = "ses-e2e-tracing-no-pause"
        workflow_id = await _start_and_await_completion(
            runner, definition, session_id=session_id
        )

        await asyncio.sleep(2)

        traces = await query_jaeger_traces(
            operation="step.execute",
            tags={"workflow.id": workflow_id},
        )

        # `operation` only selects which *traces* match; a matched trace's
        # `spans` list can still include other non-step.execute spans (e.g.
        # from auto-instrumented libraries) that never carry workflow.id.
        # Filter explicitly rather than assuming every returned span is ours.
        step_spans = spans_with_operation(traces, "step.execute")
        assert len(step_spans) >= 2, (
            f"Expected >=2 step.execute spans tagged workflow.id={workflow_id} "
            f"in Jaeger, found {len(step_spans)}."
        )
        _assert_workflow_id_tag(step_spans, workflow_id)
        _assert_session_id_tag(step_spans, session_id)

        trace_ids = {t["traceID"] for t in traces}
        assert len(trace_ids) == 1, (
            f"Expected all step spans of workflow {workflow_id} to share one "
            f"trace_id, found {len(trace_ids)}: {sorted(trace_ids)}. If this "
            "fails, LocalWorkflowRunner's asyncio.create_task()-based "
            "execution is not preserving a single trace across steps for a "
            "live run -- see jameswnl/lightspeed-cloud-agents#179 item 3."
        )


async def _run_paused_then_resumed_workflow(
    run_state_store: Any,
) -> tuple[str, set[str], set[str]]:
    """Run the triage-classify workflow through a real pause and resume.

    Parameters:
        run_state_store: Real RunStateStore backing the run.

    Returns:
        Tuple of (workflow_id, pre_pause_trace_ids, post_resume_trace_ids).
    """
    wf_path = _WF_DIR / "triage-classify-workflow.yaml"
    if not wf_path.exists():
        pytest.skip("lightspeed-cloud-agents examples not found")
    definition = yaml.safe_load(wf_path.read_text(encoding="utf-8"))

    from cloud_agents.workflow.executor.base import ApprovalDecision
    from cloud_agents.workflow.executor.local.executor import LocalWorkflowRunner

    runner = LocalWorkflowRunner(run_state_store=run_state_store)

    workflow_id = await runner.start({"definition": definition, "provider": _PROVIDER})
    status = await _poll_until_status(
        runner, workflow_id, {"paused", "completed", "failed", "cancelled"}
    )
    assert status.status == "paused", (
        f"Expected workflow to pause at the approval step, "
        f"got status={status.status}"
    )

    await asyncio.sleep(2)
    pre_pause_traces = await query_jaeger_traces(
        operation="step.execute",
        tags={"workflow.id": workflow_id},
    )
    pre_pause_step_spans = spans_with_operation(pre_pause_traces, "step.execute")
    assert pre_pause_step_spans, (
        f"Expected at least one step.execute span tagged "
        f"workflow.id={workflow_id} before pause"
    )
    _assert_workflow_id_tag(pre_pause_step_spans, workflow_id)
    pre_pause_trace_ids = {t["traceID"] for t in pre_pause_traces}

    await runner.approve(
        workflow_id,
        ApprovalDecision(
            step_name="approve-escalation",
            decision="approved",
            approver="e2e-test",
        ),
    )
    status = await _poll_until_status(
        runner, workflow_id, {"completed", "failed", "cancelled"}
    )
    assert status.status == "completed", (
        f"Workflow did not complete after resume: "
        f"status={status.status}, steps={status.steps}"
    )

    await asyncio.sleep(2)
    all_traces = await query_jaeger_traces(
        operation="step.execute",
        tags={"workflow.id": workflow_id},
    )
    _assert_workflow_id_tag(
        spans_with_operation(all_traces, "step.execute"), workflow_id
    )
    all_trace_ids = {t["traceID"] for t in all_traces}
    post_resume_trace_ids = all_trace_ids - pre_pause_trace_ids

    return workflow_id, pre_pause_trace_ids, post_resume_trace_ids


class TestPauseResumeWorkflowTracing:
    """Trace correlation across an approval pause/resume boundary.

    A single test method covers both questions (workflow.id correlation,
    and whether a resumed run's span links back to the pre-pause trace)
    against one real, LLM-backed run of the triage-classify workflow --
    that run isn't free, and there's no reason to pay for it twice to
    check two independent things about the same execution's traces.
    """

    @pytest.mark.asyncio
    async def test_pause_resume_tracing(
        self, tracer: Any, run_state_store: Any
    ) -> None:
        """workflow.id correlation and the resume span-link must both hold.

        Pre-pause and post-resume spans land in separate traces (nothing
        keeps live OTEL context across a real pause) -- the correlation
        mechanism that ties them together is the shared workflow.id tag,
        asserted for real below (including at the individual-span level,
        not just via the Jaeger tag-query filter), plus an OTEL span Link
        from the first post-resume span back to the pre-pause trace, per
        jameswnl/lightspeed-cloud-agents#181 (cloud-agents#179 item 2).
        """
        _ = tracer
        if not await check_jaeger_available():
            pytest.skip("Jaeger not available")

        workflow_id, pre_pause_trace_ids, post_resume_trace_ids = (
            await _run_paused_then_resumed_workflow(run_state_store)
        )

        assert post_resume_trace_ids, (
            "Expected new trace(s) to appear after resume, found none -- "
            "either the resumed steps produced no spans, or they landed in "
            "a pre-pause trace unexpectedly."
        )
        assert pre_pause_trace_ids.isdisjoint(post_resume_trace_ids)

        post_resume_traces = await query_jaeger_traces(
            operation="step.execute",
            tags={"workflow.id": workflow_id},
        )
        post_resume_spans = spans_with_operation(
            [t for t in post_resume_traces if t["traceID"] in post_resume_trace_ids],
            "step.execute",
        )
        linked_trace_ids = {
            ref.get("traceID")
            for span in post_resume_spans
            for ref in span.get("references", [])
        }

        assert linked_trace_ids & pre_pause_trace_ids, (
            "Expected a post-resume span to carry an OTEL span Link back to "
            "the pre-pause trace (jameswnl/lightspeed-cloud-agents#181, "
            "cloud-agents#179 item 2). Observed reference trace_ids: "
            f"{sorted(filter(None, linked_trace_ids))}, pre-pause trace_ids: "
            f"{sorted(pre_pause_trace_ids)}."
        )
