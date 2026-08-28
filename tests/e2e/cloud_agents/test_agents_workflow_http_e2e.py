"""Real HTTP e2e tests for /v1/agents/run and /v1/workflows/*.

Companion automated coverage for docs/cloud-agents-demo-curl.sh -- exercises
the same flows (in-process agent run, workflow run/approve/transcripts,
ephemeral/local spawn steps) through the actual FastAPI app over real HTTP
(real routing, real auth dependency resolution, real request/response
validation), instead of calling endpoint handler functions directly like
the other cloud_agents unit/integration/e2e suites do.

Requires OPENAI_API_KEY and a reachable PostgreSQL matching
lightspeed-stack-harness.yaml's database.postgres section (used for
workflow run-state/transcript storage). Skips cleanly if either
prerequisite is missing. spawn=ephemeral tests additionally require a
reachable OpenShell gateway (OPENSHELL_GATEWAY_URL, default
localhost:17670) and are marked `ephemeral` so CI can deselect them with
`-m "not ephemeral"`.

Set LIGHTSPEED_E2E_USE_MOCK_LLM=1 (see conftest.py) to run the
none/local-spawn tests here against an in-process mock LLM instead of
real OpenAI -- this is what CI does, so it doesn't need a real
OPENAI_API_KEY or network egress. Ephemeral tests still need a real key
and gateway either way.

Usage:
    cd ~/ws/local-infra && make up   # provides Postgres on localhost:5432
    uv run pytest tests/e2e/cloud_agents/test_agents_workflow_http_e2e.py -v
"""

# pylint: disable=too-few-public-methods,import-outside-toplevel

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_HARNESS_CONFIG = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "lightspeed-stack-harness.yaml"
)


def _postgres_reachable(host: str = "localhost", port: int = 5432) -> bool:
    """Check whether a PostgreSQL instance is reachable at host:port."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
    pytest.mark.skipif(
        not _postgres_reachable(),
        reason="PostgreSQL not reachable on localhost:5432 (needed for workflow storage)",
    ),
]

_OPENSHELL_GATEWAY_URL = os.environ.get("OPENSHELL_GATEWAY_URL", "localhost:17670")
_SANDBOX_IMAGE = os.environ.get(
    "LIGHTSPEED_SANDBOX_IMAGE", "quay.io/jameswong/lightspeed-agentic-sandbox:latest"
)


def _skip_if_gateway_unreachable() -> None:
    """Skip the current test if no OpenShell gateway answers a health check."""
    try:
        from openshell import SandboxClient

        SandboxClient(_OPENSHELL_GATEWAY_URL).health()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        pytest.skip(f"OpenShell gateway not available: {exc}")


def _wait_for_status(  # pylint: disable=inconsistent-return-statements
    http_client: TestClient,
    workflow_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Poll GET /v1/workflows/{id} until predicate(body) holds or timeout_s elapses."""
    for _ in range(timeout_s):
        status_response = http_client.get(f"/v1/workflows/{workflow_id}")
        assert status_response.status_code == 200
        body: dict[str, Any] = status_response.json()
        if predicate(body):
            return body
        time.sleep(1)
    pytest.fail(
        f"Workflow {workflow_id} did not reach the expected status in {timeout_s}s"
    )


@pytest.fixture(name="http_client")
def http_client_fixture() -> Generator[TestClient, None, None]:
    """Real TestClient against the full app, with lifespan enabled.

    Unlike tests/integration/conftest.py's integration_http_client, this
    enters the ASGI lifespan context (`with TestClient(app) as client`) so
    WorkflowStorageFactory actually initializes against PostgreSQL --
    required for /v1/workflows/* to do anything but 503.
    """
    assert _HARNESS_CONFIG.exists(), f"Config file not found: {_HARNESS_CONFIG}"

    original = os.environ.get("LIGHTSPEED_STACK_CONFIG_PATH")
    os.environ["LIGHTSPEED_STACK_CONFIG_PATH"] = str(_HARNESS_CONFIG)
    try:
        from configuration import configuration as config_module

        # app.main reads configuration.configuration.name and
        # configuration.service_configuration.root_path at import time to
        # construct the FastAPI app -- LIGHTSPEED_STACK_CONFIG_PATH alone
        # isn't enough, config must actually be loaded before the import.
        config_module.load_configuration(str(_HARNESS_CONFIG))

        import app.endpoints.workflows as wf_mod
        from app.main import app

        wf_mod._executor = None  # pylint: disable=protected-access
        with TestClient(app) as client:
            yield client
        wf_mod._executor = None  # pylint: disable=protected-access
    finally:
        if original is not None:
            os.environ["LIGHTSPEED_STACK_CONFIG_PATH"] = original
        else:
            os.environ.pop("LIGHTSPEED_STACK_CONFIG_PATH", None)


class TestAgentRunHttpE2E:
    """POST /v1/agents/run over real HTTP (mirrors demo-curl tab1)."""

    def test_in_process_agent_run(self, http_client: TestClient) -> None:
        """spawn:none agent run returns a structured, schema-conforming response."""
        response = http_client.post(
            "/v1/agents/run",
            json={
                "prompt": "Is pod checkout-7f9 healthy? Assume yes, everything is fine.",
                "spawn": "none",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "tools": [],
                "mcp_servers": None,
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "healthy": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["healthy", "reason"],
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert isinstance(data["output"], dict)
        assert "healthy" in data["output"]
        assert "reason" in data["output"]
        assert data["token_usage"]["input_tokens"] > 0

    @pytest.mark.ephemeral
    def test_ephemeral_agent_run(self, http_client: TestClient) -> None:
        """spawn:ephemeral agent run reaches a real OpenShell sandbox over HTTP.

        Companion to test_agents_ephemeral_e2e.py's handler-direct coverage
        -- this goes through real routing, auth dependency resolution, and
        request/response validation instead of calling the handler function
        directly.
        """
        _skip_if_gateway_unreachable()

        response = http_client.post(
            "/v1/agents/run",
            json={
                "prompt": "What is 9+9? Reply with just the number.",
                "spawn": "ephemeral",
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["output"] is not None
        assert data["transcript"]


class TestWorkflowHttpE2E:
    """POST /v1/workflows/run + approve + transcripts over real HTTP.

    Mirrors demo-curl tab3, with spawn:none steps instead of spawn:ephemeral
    so this doesn't depend on an OpenShell gateway being up -- ephemeral
    spawn is already covered separately by test_spawn_modes_e2e.py.
    """

    def test_workflow_pause_approve_complete(self, http_client: TestClient) -> None:
        """Multi-step workflow pauses for approval, then completes after approving."""
        start_response = http_client.post(
            "/v1/workflows/run",
            json={
                "definition": {
                    "apiVersion": "v1",
                    "kind": "AgentWorkflow",
                    "metadata": {"name": "triage-remediate-http-e2e"},
                    "spec": {
                        "steps": [
                            {
                                "name": "triage",
                                "type": "agent",
                                "spawn": "none",
                                "output_key": "triage_result",
                                "prompt": (
                                    "Diagnose the checkout-7f9 pod issue. Assume "
                                    "root cause is a memory leak. Report severity "
                                    "and root cause."
                                ),
                                "output_schema": {
                                    "type": "object",
                                    "properties": {
                                        "severity": {"type": "string"},
                                        "root_cause": {"type": "string"},
                                    },
                                    "required": ["severity", "root_cause"],
                                },
                                "timeout_seconds": 120,
                            },
                            {
                                "name": "approve",
                                "type": "human-approval",
                                "output_key": "approval",
                                "message": (
                                    "Root cause: "
                                    "{{ steps.triage_result.output.root_cause }}. "
                                    "Approve remediation?"
                                ),
                                "risk_level": "high",
                            },
                            {
                                "name": "remediate",
                                "type": "agent",
                                "spawn": "none",
                                "output_key": "remediate_result",
                                "prompt": (
                                    "Say one sentence confirming the fix for: "
                                    "{{ steps.triage_result.output.root_cause }}"
                                ),
                                "condition": "steps.approval.output.approved == true",
                                "timeout_seconds": 120,
                            },
                        ]
                    },
                },
                "provider": {"name": "openai", "model": "gpt-4o-mini"},
            },
        )

        assert start_response.status_code == 202
        workflow_id = start_response.json()["workflow_id"]
        assert workflow_id

        paused = None
        for _ in range(30):
            status_response = http_client.get(f"/v1/workflows/{workflow_id}")
            assert status_response.status_code == 200
            body = status_response.json()
            if body["status"] == "paused":
                paused = body
                break
            time.sleep(1)
        assert paused is not None, "Workflow never reached 'paused' status"
        assert "triage_result" in paused["steps"]

        approve_response = http_client.post(
            f"/v1/workflows/{workflow_id}/approve",
            json={
                "step_name": "approve",
                "decision": "approved",
                "approver": "http-e2e-test",
            },
        )
        assert approve_response.status_code == 200

        completed = None
        for _ in range(30):
            status_response = http_client.get(f"/v1/workflows/{workflow_id}")
            assert status_response.status_code == 200
            body = status_response.json()
            if body["is_terminal"]:
                completed = body
                break
            time.sleep(1)
        assert completed is not None, "Workflow never reached a terminal status"
        assert completed["status"] == "completed"
        assert "remediate_result" in completed["steps"]

        transcripts_response = http_client.get(
            f"/v1/workflows/{workflow_id}/transcripts"
        )
        assert transcripts_response.status_code == 200
        transcripts = transcripts_response.json()["transcripts"]
        assert "triage_result" in transcripts
        assert "remediate_result" in transcripts

    def test_workflow_with_local_spawn_step(self, http_client: TestClient) -> None:
        """A workflow step with spawn:local completes over real HTTP.

        spawn=local runs the LLM call in a child subprocess
        (SubprocessExecutor) -- previously untested at the /v1/workflows/*
        HTTP layer (test_spawn_modes_e2e.py covers it at the step-executor
        layer only, bypassing the endpoint entirely).
        """
        start_response = http_client.post(
            "/v1/workflows/run",
            json={
                "definition": {
                    "apiVersion": "v1",
                    "kind": "AgentWorkflow",
                    "metadata": {"name": "local-spawn-http-e2e"},
                    "spec": {
                        "steps": [
                            {
                                "name": "investigate",
                                "type": "agent",
                                "spawn": "local",
                                "output_key": "investigate_result",
                                "prompt": (
                                    "Say one sentence confirming the "
                                    "checkout-7f9 pod is healthy."
                                ),
                                "timeout_seconds": 120,
                            },
                        ]
                    },
                },
                "provider": {"name": "openai", "model": "gpt-4o-mini"},
            },
        )

        assert start_response.status_code == 202
        workflow_id = start_response.json()["workflow_id"]
        assert workflow_id

        completed = _wait_for_status(
            http_client, workflow_id, lambda body: bool(body["is_terminal"])
        )
        assert completed["status"] == "completed"
        assert "investigate_result" in completed["steps"]

    @pytest.mark.ephemeral
    def test_workflow_with_ephemeral_spawn_step(self, http_client: TestClient) -> None:
        """A workflow step with spawn:ephemeral completes over real HTTP.

        Reaches a real OpenShell sandbox -- previously zero e2e coverage of
        /v1/workflows/* with an ephemeral step existed at any layer.
        """
        _skip_if_gateway_unreachable()

        start_response = http_client.post(
            "/v1/workflows/run",
            json={
                "definition": {
                    "apiVersion": "v1",
                    "kind": "AgentWorkflow",
                    "metadata": {"name": "ephemeral-spawn-http-e2e"},
                    "spec": {
                        "steps": [
                            {
                                "name": "investigate",
                                "type": "agent",
                                "spawn": "ephemeral",
                                "output_key": "investigate_result",
                                "prompt": (
                                    "Say one sentence confirming the "
                                    "checkout-7f9 pod is healthy."
                                ),
                                "timeout_seconds": 120,
                            },
                        ]
                    },
                },
                "provider": {"name": "openai", "model": "gpt-4o-mini"},
            },
        )

        assert start_response.status_code == 202
        workflow_id = start_response.json()["workflow_id"]
        assert workflow_id

        completed = _wait_for_status(
            http_client, workflow_id, lambda body: bool(body["is_terminal"])
        )
        assert completed["status"] == "completed"
        assert "investigate_result" in completed["steps"]
