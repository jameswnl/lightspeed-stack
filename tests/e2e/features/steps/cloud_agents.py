"""Step definitions for cloud agents E2E tests."""

import os

import requests
from behave import given, then, when

DIAG_HOST = os.getenv("E2E_AGENT_HOSTNAME", "localhost")
DIAG_PORT = os.getenv("E2E_AGENT_PORT", "8081")
DIAG_BASE = f"http://{DIAG_HOST}:{DIAG_PORT}"

MONITOR_HOST = os.getenv("E2E_MONITOR_HOSTNAME", "localhost")
MONITOR_PORT = os.getenv("E2E_MONITOR_PORT", "8082")
MONITOR_BASE = f"http://{MONITOR_HOST}:{MONITOR_PORT}"


@given("The diagnostic agent pod is running")
def step_diag_pod_running(context):
    """Verify the diagnostic agent pod is reachable."""
    try:
        resp = requests.get(f"{DIAG_BASE}/healthz", timeout=10)
        assert (
            resp.status_code == 200
        ), f"Diagnostic agent not ready: {resp.status_code}"
    except requests.ConnectionError as exc:
        raise AssertionError(
            f"Cannot connect to diagnostic agent at {DIAG_BASE}: {exc}"
        ) from exc


@given("The diagnostic agent healthcheck returns 200")
def step_diag_healthcheck_ok(context):
    """Verify diagnostic healthcheck returns 200."""
    resp = requests.get(f"{DIAG_BASE}/healthz", timeout=10)
    assert resp.status_code == 200


@given("The monitoring agent pod is running")
def step_monitor_pod_running(context):
    """Verify the monitoring agent pod is reachable."""
    try:
        resp = requests.get(f"{MONITOR_BASE}/healthz", timeout=10)
        assert (
            resp.status_code == 200
        ), f"Monitoring agent not ready: {resp.status_code}"
    except requests.ConnectionError as exc:
        raise AssertionError(
            f"Cannot connect to monitoring agent at {MONITOR_BASE}: {exc}"
        ) from exc


@when('I GET the diagnostic agent "{path}"')
def step_get_diag(context, path):
    """Send GET request to the diagnostic agent."""
    context.response = requests.get(f"{DIAG_BASE}{path}", timeout=30)


@when('I GET the monitoring agent "{path}"')
def step_get_monitor(context, path):
    """Send GET request to the monitoring agent."""
    context.response = requests.get(f"{MONITOR_BASE}{path}", timeout=30)


@when('I POST to the diagnostic agent "{path}" with prompt "{prompt}"')
def step_post_diag(context, path, prompt):
    """Send POST request to the diagnostic agent with a prompt."""
    context.response = requests.post(
        f"{DIAG_BASE}{path}",
        json={"prompt": prompt},
        timeout=300,
    )


@when('I POST to the monitoring agent "{path}" with prompt "{prompt}"')
def step_post_monitor(context, path, prompt):
    """Send POST request to the monitoring agent with a prompt."""
    context.response = requests.post(
        f"{MONITOR_BASE}{path}",
        json={"prompt": prompt},
        timeout=300,
    )


@when('I POST to the diagnostic agent "{path}" with empty prompt')
def step_post_diag_empty(context, path):
    """Send POST request with empty prompt."""
    context.response = requests.post(
        f"{DIAG_BASE}{path}",
        json={"prompt": ""},
        timeout=30,
    )


@when('I submit async to the diagnostic agent "{path}" with prompt "{prompt}"')
def step_async_submit_diag(context, path, prompt):
    """Send async POST to the diagnostic agent."""
    context.response = requests.post(
        f"{DIAG_BASE}{path}",
        json={"prompt": prompt},
        headers={"Prefer": "respond-async"},
        timeout=30,
    )


@when("I poll the diagnostic agent for the run_id")
def step_poll_diag(context):
    """Poll the diagnostic agent for the previously submitted run."""
    run_id = context.async_run_id
    context.response = requests.get(
        f"{DIAG_BASE}/v1/runs/{run_id}",
        timeout=30,
    )


@then("The response status is {status_code:d}")
def step_check_status(context, status_code):
    """Verify HTTP status code."""
    assert context.response.status_code == status_code, (
        f"Expected {status_code}, got {context.response.status_code}: "
        f"{context.response.text[:200]}"
    )


@then('The response body contains "{text}"')
def step_body_contains(context, text):
    """Verify response body contains text."""
    assert (
        text in context.response.text
    ), f"'{text}' not found in response: {context.response.text[:200]}"


@then("The response contains a valid AgentRunResponse")
def step_valid_agent_response(context):
    """Verify response is a valid AgentRunResponse."""
    body = context.response.json()
    assert "output" in body, f"Missing 'output' in response: {body}"
    assert "agent_name" in body, f"Missing 'agent_name' in response: {body}"
    assert "success" in body, f"Missing 'success' in response: {body}"
    assert "output_type" in body, f"Missing 'output_type' in response: {body}"
    assert "schema_version" in body, f"Missing 'schema_version' in response: {body}"


@then('The output field "{field}" is present')
def step_output_field_present(context, field):
    """Verify a field exists in the agent output."""
    output = context.response.json().get("output", {})
    assert field in output, f"Field '{field}' not in output: {output}"


@then('The output field "{field}" is not empty')
def step_output_field_not_empty(context, field):
    """Verify a field in the output is not empty."""
    output = context.response.json().get("output", {})
    value = output.get(field)
    assert value, f"Field '{field}' is empty or missing: {output}"


@then('The output field "{field}" is true')
def step_output_field_true(context, field):
    """Verify a boolean field in the output is true."""
    output = context.response.json().get("output", {})
    assert (
        output.get(field) is True
    ), f"Field '{field}' is not true: {output.get(field)}"


@then("The async submit response status is {status_code:d}")
def step_async_status(context, status_code):
    """Verify async submit status code."""
    assert (
        context.response.status_code == status_code
    ), f"Expected {status_code}, got {context.response.status_code}"


@then("The async submit response contains a run_id")
def step_async_has_run_id(context):
    """Verify async submit contains run_id and store it."""
    body = context.response.json()
    assert "run_id" in body, f"Missing run_id in async submit response: {body}"
    context.async_run_id = body["run_id"]


@then('The poll response has status "{status1}" or "{status2}"')
def step_poll_status(context, status1, status2):
    """Verify poll response status is one of the expected values."""
    body = context.response.json()
    assert body.get("status") in (
        status1,
        status2,
    ), f"Expected status '{status1}' or '{status2}', got: {body.get('status')}"
