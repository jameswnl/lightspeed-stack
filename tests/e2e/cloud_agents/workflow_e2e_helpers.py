"""Shared workflow-run helpers for cloud_agents E2E OTEL tracing tests.

Both test_workflow_tracing_e2e.py (which drives LocalWorkflowRunner directly
with a self-installed TracerProvider) and test_workflow_factory_tracing_e2e.py
(which drives create_workflow_runner() so tracing is wired by the factory,
exactly as the running service does) need the same PostgreSQL connection
config, the same minimal workflow definition, and the same status-polling /
run-to-completion helpers. These are framework-agnostic: none of them install
or assume any particular TracerProvider, so they are safe to share across
files that make different choices about how tracing gets set up.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

# PostgreSQL connection config, matching docker-compose-harness.yaml defaults.
PG_HOST = os.environ.get("WORKFLOW_PG_HOST", "localhost")
PG_PORT = os.environ.get("WORKFLOW_PG_PORT", "5432")
PG_DB = os.environ.get("WORKFLOW_PG_DB", "lightspeed")
PG_USER = os.environ.get("WORKFLOW_PG_USER", "lightspeed")
PG_PASSWORD = os.environ.get("WORKFLOW_PG_PASSWORD", "lightspeed")
DB_URL = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

PROVIDER = {"name": "openai", "model": "gpt-4o-mini"}


def two_step_definition(workflow_name: str) -> dict[str, Any]:
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


async def poll_until_status(
    runner: Any,
    workflow_id: str,
    target_statuses: set[str],
    timeout: float = 60.0,
) -> Any:
    """Poll get_status() until the run reaches one of the target statuses.

    Deliberately avoids reaching into LocalWorkflowRunner's private
    `_running` task registry -- a real caller (e.g. the
    GET /v1/workflows/{id} HTTP endpoint) only ever observes progress via
    get_status(), never via an in-process task handle, so polling here keeps
    the test representative of real usage.

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


async def start_and_await_completion(
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
    start_input: dict[str, Any] = {"definition": definition, "provider": PROVIDER}
    if session_id is not None:
        start_input["session_id"] = session_id
    workflow_id = await runner.start(start_input)
    status = await poll_until_status(
        runner, workflow_id, {"completed", "failed", "cancelled"}
    )
    assert status.status == "completed", (
        f"Workflow did not complete cleanly: status={status.status}, "
        f"steps={status.steps}"
    )
    return workflow_id
