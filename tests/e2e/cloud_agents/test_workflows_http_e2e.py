"""Real HTTP e2e tests for POST /v1/workflows/*.

Companion automated coverage for docs/cloud-agents-demo-curl.sh's
workflow-ephemeral-approval / workflow-none-approval / workflow-local /
workflow-ephemeral scenarios -- exercises workflow run/approve/transcripts
across all three spawn
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

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .conftest import postgres_reachable, skip_if_gateway_unreachable, wait_for_status

_DEMO_MCP_SERVER = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "cloud-agents-demo-mcp.py"
)


def _free_tcp_port() -> int:
    """Return an available localhost TCP port.

    Binds port 0, reads the assigned port, and releases it. There is a
    small race before the MCP subprocess re-binds it, acceptable for a
    single-process test fixture.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(name="demo_mcp_url")
def demo_mcp_url_fixture() -> Generator[str, None, None]:
    """Run docs/cloud-agents-demo-mcp.py and yield its streamable-http URL.

    Provides a real, out-of-process MCP server exposing get_pod_status so a
    spawn:none workflow step has an actual external tool to call over the
    wire -- the same server the demo script's *-none-tools scenarios use.
    """
    assert _DEMO_MCP_SERVER.exists(), f"demo MCP server not found: {_DEMO_MCP_SERVER}"
    port = _free_tcp_port()
    env = {**os.environ, "DEMO_MCP_HOST": "127.0.0.1", "DEMO_MCP_PORT": str(port)}
    # Not a `with` block: the context-manager form only waits on exit, but
    # this long-running server must be actively terminated (see finally).
    proc = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, str(_DEMO_MCP_SERVER)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait until the port accepts connections (server bound + listening).
        deadline = time.time() + 15
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail("demo MCP server exited before becoming ready")
            with (
                contextlib.suppress(OSError),
                socket.create_connection(("127.0.0.1", port), timeout=0.5),
            ):
                break
            time.sleep(0.2)
        else:
            pytest.fail(f"demo MCP server did not open port {port} within 15s")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
            proc.wait()  # reap the killed process so it can't linger as a zombie


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

        # Accept "paused" or any terminal status, not just "paused" -- a
        # workflow that fails immediately (never pausing) would otherwise
        # burn the full timeout budget before the predicate ever matches.
        paused = wait_for_status(
            http_client,
            workflow_id,
            lambda body: body["status"] == "paused" or body["is_terminal"],
        )
        assert paused["status"] == "paused", paused
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

    @pytest.mark.skipif(
        bool(os.environ.get("LIGHTSPEED_E2E_USE_MOCK_LLM")),
        reason="mock LLM returns canned text with no tool calls; "
        "this test needs a real tool-calling model",
    )
    def test_workflow_none_spawn_calls_mcp_tool(
        self, http_client: TestClient, demo_mcp_url: str
    ) -> None:
        """A spawn:none step invokes an MCP tool supplied via the run body.

        Companion automated coverage for docs/cloud-agents-demo-curl.sh's
        workflow-none-tools scenario, and the seam test for this PR's
        plumbing: RunWorkflowRequest.mcp_servers -> workflow_input
        ["mcp_servers"] -> cloud-agents LocalWorkflowRunner -> the
        spawn:none in-process pydantic-ai agent loop (MCPToolset).

        Proof of real invocation: get_pod_status reports the pod's memory
        limit as 347Mi -- an odd, non-default value a model won't emit on
        its own, so seeing it echoed back proves the tool actually ran
        rather than the answer being hallucinated. (The transcript captures
        only the model's paraphrased text, not the raw tool result, so the
        assertion keys on a value the model is asked to repeat verbatim.)
        Requires a real tool-calling LLM -- the mock LLM returns canned
        text with no tool calls, so this is skipped under
        LIGHTSPEED_E2E_USE_MOCK_LLM (see the skipif above).
        """
        start_response = http_client.post(
            "/v1/workflows/run",
            json={
                "definition": {
                    "apiVersion": "v1",
                    "kind": "AgentWorkflow",
                    "metadata": {"name": "none-tools-http-e2e"},
                    "spec": {
                        "steps": [
                            {
                                "name": "check",
                                "type": "agent",
                                "spawn": "none",
                                "output_key": "check_result",
                                "prompt": (
                                    "Use the get_pod_status tool to look up the "
                                    "pod named checkout-7f9. Report its exact "
                                    "memory limit and restart count verbatim."
                                ),
                                "mcp_servers": ["pod-status"],
                                "timeout_seconds": 120,
                            },
                        ]
                    },
                },
                "provider": {"name": "openai", "model": "gpt-4o-mini"},
                "mcp_servers": [{"name": "pod-status", "url": demo_mcp_url}],
            },
        )

        assert start_response.status_code == 202
        workflow_id = start_response.json()["workflow_id"]
        assert workflow_id

        completed = wait_for_status(
            http_client,
            workflow_id,
            lambda body: bool(body["is_terminal"]),
            timeout_s=150,
        )
        assert completed["status"] == "completed", completed
        assert "check_result" in completed["steps"]

        # The tool's odd "347Mi" memory limit is not a value a model emits
        # on its own -- its presence in the step output/transcripts proves
        # the tool actually ran through the full HTTP -> runner -> MCPToolset
        # path (vs. a hallucinated but plausible-looking answer).
        transcripts_response = http_client.get(
            f"/v1/workflows/{workflow_id}/transcripts"
        )
        assert transcripts_response.status_code == 200
        haystack = json.dumps(completed["steps"]) + json.dumps(
            transcripts_response.json()
        )
        assert "347Mi" in haystack, haystack

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
        workflow-ephemeral-approval scenario -- previously that combination
        (ephemeral steps + a human-approval gate in between) was only
        exercised manually via the demo script, never asserted by an
        automated test.
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
        # LLM on a live OpenShell gateway routinely exceeds 30s. Accept
        # "paused" or any terminal status -- a workflow that fails
        # immediately (e.g. a gateway/credential error) would otherwise
        # burn the full 150s budget before the predicate ever matches.
        paused = wait_for_status(
            http_client,
            workflow_id,
            lambda body: body["status"] == "paused" or body["is_terminal"],
            timeout_s=150,
        )
        assert paused["status"] == "paused", paused
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

        transcripts_response = http_client.get(
            f"/v1/workflows/{workflow_id}/transcripts"
        )
        assert transcripts_response.status_code == 200
        transcripts = transcripts_response.json()["transcripts"]
        assert "triage_result" in transcripts
        assert "remediate_result" in transcripts

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
