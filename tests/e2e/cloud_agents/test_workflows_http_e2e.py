"""Real HTTP e2e tests for POST /v1/workflows/*.

Companion automated coverage for docs/cloud-agents-demo-curl.sh's tabs 3-6
-- exercises workflow run/approve/transcripts across all three spawn
modes, with and without a human-approval gate, through the actual FastAPI
app over real HTTP (real routing, real auth dependency resolution, real
request/response validation), unlike test_workflow_definitions_e2e.py
(calls the step-executor dispatch directly per-step, bypassing the
workflow engine, the handler, and HTTP) or test_workflow_tracing_e2e.py
(calls LocalWorkflowRunner directly, bypassing the handler and HTTP, for
tracing-specific assertions).

Requires OPENAI_API_KEY and a reachable PostgreSQL matching
lightspeed-stack-harness.yaml's database.postgres section (used for
workflow run-state/transcript storage). Skips cleanly if either
prerequisite is missing. spawn=ephemeral additionally requires a reachable
OpenShell gateway (OPENSHELL_GATEWAY_URL, default localhost:17670) and is
marked `ephemeral` so CI can deselect it with `-m "not ephemeral"`.

Set LIGHTSPEED_E2E_USE_MOCK_LLM=1 (see conftest.py) to run the
none/local-spawn tests here against an in-process mock LLM instead of
real OpenAI -- this is what CI does, so it doesn't need a real
OPENAI_API_KEY or network egress. spawn=ephemeral still needs a real key
and gateway either way.

Usage:
    cd ~/ws/local-infra && make up   # provides Postgres on localhost:5432
    uv run pytest tests/e2e/cloud_agents/test_workflows_http_e2e.py -v
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from .conftest import postgres_reachable, skip_if_gateway_unreachable, wait_for_status

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
    pytest.mark.skipif(
        not postgres_reachable(),
        reason="PostgreSQL not reachable on localhost:5432 (needed for workflow storage)",
    ),
]


class TestWorkflowHttpE2E:
    """POST /v1/workflows/run + approve + transcripts over real HTTP."""

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

        paused = wait_for_status(
            http_client, workflow_id, lambda body: body["status"] == "paused"
        )
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

        completed = wait_for_status(
            http_client, workflow_id, lambda body: bool(body["is_terminal"])
        )
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
        (SubprocessExecutor) -- untested at the /v1/workflows/* HTTP layer
        before this test existed (test_workflow_definitions_e2e.py covers
        it at the step-executor layer only, bypassing the endpoint
        entirely).
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

        # timeout_s > the step's own timeout_seconds=120 -- otherwise a
        # slow-but-healthy run (real OpenAI latency) hits wait_for_status's
        # own timeout and pytest.fails before the step itself would time out.
        completed = wait_for_status(
            http_client,
            workflow_id,
            lambda body: bool(body["is_terminal"]),
            timeout_s=150,
        )
        assert completed["status"] == "completed"
        assert "investigate_result" in completed["steps"]

    @pytest.mark.ephemeral
    def test_workflow_with_ephemeral_approval(self, http_client: TestClient) -> None:
        """A workflow with spawn:ephemeral steps pauses for approval, then completes.

        Companion automated coverage for docs/cloud-agents-demo-curl.sh's
        tab3 -- previously that combination (ephemeral steps + a
        human-approval gate in between) was only exercised manually via
        the demo script, never asserted by an automated test.
        """
        skip_if_gateway_unreachable()

        start_response = http_client.post(
            "/v1/workflows/run",
            json={
                "definition": {
                    "apiVersion": "v1",
                    "kind": "AgentWorkflow",
                    "metadata": {"name": "triage-remediate-ephemeral-http-e2e"},
                    "spec": {
                        "steps": [
                            {
                                "name": "triage",
                                "type": "agent",
                                "spawn": "ephemeral",
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
                                "spawn": "ephemeral",
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

        # timeout_s > the step's own timeout_seconds=120 -- sandbox boot +
        # LLM on a live OpenShell gateway routinely exceeds 30s.
        paused = wait_for_status(
            http_client,
            workflow_id,
            lambda body: body["status"] == "paused",
            timeout_s=150,
        )
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

        completed = wait_for_status(
            http_client,
            workflow_id,
            lambda body: bool(body["is_terminal"]),
            timeout_s=150,
        )
        assert completed["status"] == "completed"
        assert "remediate_result" in completed["steps"]

    @pytest.mark.ephemeral
    def test_workflow_with_ephemeral_spawn_step(self, http_client: TestClient) -> None:
        """A workflow step with spawn:ephemeral completes over real HTTP.

        Reaches a real OpenShell sandbox -- untested at any layer for
        /v1/workflows/* before this test existed.
        """
        skip_if_gateway_unreachable()

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

        # timeout_s > the step's own timeout_seconds=120 -- sandbox boot +
        # LLM on a live OpenShell gateway routinely exceeds 30s; the
        # gateway-health skip above only covers an unreachable gateway, not
        # a slow-but-healthy one.
        completed = wait_for_status(
            http_client,
            workflow_id,
            lambda body: bool(body["is_terminal"]),
            timeout_s=150,
        )
        assert completed["status"] == "completed"
        assert "investigate_result" in completed["steps"]
