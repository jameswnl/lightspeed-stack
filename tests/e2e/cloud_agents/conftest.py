"""Shared fixtures/helpers for cloud-agents e2e tests.

See mock_llm_env.py for the LIGHTSPEED_E2E_USE_MOCK_LLM gating logic the
autouse fixture below wraps.

LIGHTSPEED_E2E_USE_MOCK_LLM is only safe for test_agents_run_http_e2e.py /
test_workflows_http_e2e.py (plus this module's own self-tests) -- those
files' assertions are structural only (status/key-presence), by design, so
a canned response satisfies them. Every other file in this directory
asserts real-world semantic content (e.g. "paris" in output) that only a
real LLM can produce -- running those with the mock active fails
confusingly, not because of a real bug. Since this fixture is autouse at
session scope, point pytest at the specific compatible file(s) rather than
the whole tests/e2e/cloud_agents/ directory when the mock is enabled (see
the CI job in .github/workflows/cloud_agents_tests.yaml and the
test-e2e-agents-workflows-mock Makefile target for the supported scope).
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from authentication.interface import AuthTuple
from models.config import Action

from .mock_llm_env import mock_llm_env_context

_HARNESS_CONFIG = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "lightspeed-stack-harness.yaml"
)

_OPENSHELL_GATEWAY_URL = os.environ.get("OPENSHELL_GATEWAY_URL", "localhost:17670")
_SANDBOX_IMAGE = os.environ.get(
    "LIGHTSPEED_SANDBOX_IMAGE", "quay.io/jameswong/lightspeed-agentic-sandbox:latest"
)

AUTH: AuthTuple = ("e2e-user", "e2e-tester", False, "")


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server_env() -> Generator[None, None, None]:
    """Redirect OPENAI_BASE_URL to a local mock server when opted in."""
    with mock_llm_env_context():
        yield


def make_request() -> Request:
    """Create a minimal FastAPI Request with all actions authorized.

    Used by handler-direct tests (`handler.__wrapped__(make_request(), ...)`)
    to bypass the `@authorize` decorator and HTTP layer while still giving
    the handler a `request.state.authorized_actions` it can read.
    """
    request = Request(
        scope={
            "type": "http",
            "query_string": b"",
            "headers": [],
        }
    )
    request.state.authorized_actions = set(Action)
    return request


def postgres_reachable(host: str = "localhost", port: int = 5432) -> bool:
    """Check whether a PostgreSQL instance is reachable at host:port."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def skip_if_gateway_unreachable() -> None:
    """Skip the current test if no OpenShell gateway answers a health check."""
    try:
        from openshell import SandboxClient  # pylint: disable=import-outside-toplevel

        SandboxClient(_OPENSHELL_GATEWAY_URL).health()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        pytest.skip(f"OpenShell gateway not available: {exc}")


def wait_for_status(  # pylint: disable=inconsistent-return-statements
    http_client: TestClient,
    workflow_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Poll GET /v1/workflows/{id} until predicate(body) holds or timeout_s elapses."""
    import time  # pylint: disable=import-outside-toplevel

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
        from configuration import (  # pylint: disable=import-outside-toplevel
            configuration as config_module,
        )

        # app.main reads configuration.configuration.name and
        # configuration.service_configuration.root_path at import time to
        # construct the FastAPI app -- LIGHTSPEED_STACK_CONFIG_PATH alone
        # isn't enough, config must actually be loaded before the import.
        config_module.load_configuration(str(_HARNESS_CONFIG))

        import app.endpoints.workflows as wf_mod  # pylint: disable=import-outside-toplevel
        from app.main import app  # pylint: disable=import-outside-toplevel

        wf_mod._executor = None  # pylint: disable=protected-access
        with TestClient(app) as client:
            yield client
        wf_mod._executor = None  # pylint: disable=protected-access
    finally:
        if original is not None:
            os.environ["LIGHTSPEED_STACK_CONFIG_PATH"] = original
        else:
            os.environ.pop("LIGHTSPEED_STACK_CONFIG_PATH", None)
