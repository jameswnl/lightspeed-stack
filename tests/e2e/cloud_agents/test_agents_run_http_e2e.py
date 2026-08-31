"""Real HTTP e2e tests for POST /v1/agents/run.

Companion automated coverage for docs/cloud-agents-demo-curl.sh's
agent-none/agent-local/agent-ephemeral scenarios -- exercises in-process,
subprocess, and ephemeral agent runs through the actual FastAPI app over
real HTTP (real routing, real auth dependency resolution, real
request/response validation), unlike test_agents_run_handler_e2e.py
(calls the handler function directly) or test_step_executor_e2e.py (calls
the step-executor dispatch directly, bypassing the handler and HTTP both).

Requires OPENAI_API_KEY and a reachable PostgreSQL matching
lightspeed-stack-harness.yaml's database.postgres section -- the shared
`http_client` fixture (conftest.py) enters the app's ASGI lifespan, which
initializes WorkflowStorageFactory against Postgres unconditionally, even
though /v1/agents/run itself doesn't touch workflow storage. Skips cleanly
if either prerequisite is missing. spawn=ephemeral additionally requires a
reachable OpenShell gateway (OPENSHELL_GATEWAY_URL, default
localhost:17670) and is marked `ephemeral` so CI can deselect it with
`-m "not ephemeral"`.

Set LIGHTSPEED_E2E_USE_MOCK_LLM=1 (see conftest.py) to run the spawn=none
and spawn=local tests here against an in-process mock LLM instead of real
OpenAI -- this is what CI does. spawn=ephemeral still needs a real key and
gateway either way.

Usage:
    cd ~/ws/local-infra && make up   # provides Postgres on localhost:5432
    uv run pytest tests/e2e/cloud_agents/test_agents_run_http_e2e.py -v
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from .conftest import postgres_reachable, skip_if_gateway_unreachable

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
    pytest.mark.skipif(
        not postgres_reachable(),
        reason=(
            "PostgreSQL not reachable on localhost:5432 "
            "(needed by the shared http_client fixture)"
        ),
    ),
]


class TestAgentRunHttpE2E:
    """POST /v1/agents/run over real HTTP.

    Mirrors demo-curl agent-none/agent-local/agent-ephemeral.
    """

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

    def test_local_spawn_agent_run(self, http_client: TestClient) -> None:
        """spawn:local agent run executes in a subprocess and returns a completed result.

        No output_schema here (unlike test_in_process_agent_run): the
        cloud-agents SubprocessExecutor has no native structured-output
        mode yet (jameswnl/lightspeed-cloud-agents#235) -- it only embeds
        the schema as prompt text, which the deterministic mock LLM used
        here doesn't recognize. Matches the existing spawn:local workflow
        step test's approach (test_workflow_with_local_spawn_step), which
        also avoids output_schema for the same reason.
        """
        response = http_client.post(
            "/v1/agents/run",
            json={
                "prompt": "Say one sentence confirming pod checkout-7f9 is healthy.",
                "spawn": "local",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "tools": [],
                "mcp_servers": None,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["output"] is not None
        assert data["token_usage"]["input_tokens"] > 0

    @pytest.mark.ephemeral
    def test_ephemeral_agent_run(self, http_client: TestClient) -> None:
        """spawn:ephemeral agent run reaches a real OpenShell sandbox over HTTP.

        Companion to test_agents_run_handler_e2e.py's handler-direct
        coverage -- this goes through real routing, auth dependency
        resolution, and request/response validation instead of calling the
        handler function directly.
        """
        skip_if_gateway_unreachable()

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
