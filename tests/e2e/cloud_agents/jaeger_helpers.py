"""Shared Jaeger query helpers for cloud_agents E2E OTEL tracing tests.

Split out because both test_otel_tracing_e2e.py (ChatWorkflowRunner) and
test_workflow_tracing_e2e.py (LocalWorkflowRunner) need identical Jaeger
availability/query helpers. The `tracer` fixture itself (which installs a
process-global TracerProvider) is intentionally NOT shared here — each file
keeps its own copy and assumes standalone execution (see each file's
docstring), since OpenTelemetry only allows one TracerProvider install per
process and sharing the fixture across modules would make provider/exporter
config depend on which test file runs first.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

JAEGER_QUERY_URL = os.environ.get("JAEGER_QUERY_URL", "http://localhost:16686")
OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE_NAME = "lightspeed-stack-e2e-test"


async def check_jaeger_available() -> bool:
    """Check if the Jaeger query API is reachable.

    Returns:
        True if Jaeger responds to a services list request.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{JAEGER_QUERY_URL}/api/services", timeout=5)
            return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


async def query_jaeger_traces(
    service: str = SERVICE_NAME,
    operation: str = "",
    limit: int = 20,
    tags: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Query Jaeger for recent traces.

    Note: the `operation` filter selects which *traces* match (a trace
    matches if any of its spans has that operation name) -- it does not
    filter which spans are returned within a matched trace. A matched
    trace's `spans` list can still include other spans (e.g. from
    auto-instrumented libraries) with different operation names and no
    `workflow.id` tag. Use `spans_with_operation()` below to filter down
    to the spans you actually want to assert on.

    Parameters:
        service: Service name to query.
        operation: Optional operation (span) name filter.
        limit: Max traces to return.
        tags: Optional exact-match tag filter, e.g. {"workflow.id": "wf-123"}.

    Returns:
        List of trace dicts from Jaeger.
    """
    params: dict[str, Any] = {
        "service": service,
        "limit": limit,
        "lookback": "1h",
    }
    if operation:
        params["operation"] = operation
    if tags:
        params["tags"] = json.dumps(tags)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{JAEGER_QUERY_URL}/api/traces",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])


def spans_with_operation(
    traces: list[dict[str, Any]], operation_name: str
) -> list[dict[str, Any]]:
    """Filter a list of Jaeger traces down to spans with a specific operation name.

    Parameters:
        traces: Traces as returned by query_jaeger_traces().
        operation_name: Exact operation (span) name to keep, e.g. "step.execute".

    Returns:
        Flat list of matching span dicts, each still carrying its own tags.
    """
    return [
        span
        for trace in traces
        for span in trace.get("spans", [])
        if span.get("operationName") == operation_name
    ]
