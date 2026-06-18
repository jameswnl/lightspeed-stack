"""Step definitions for cloud agents E2E tests."""

import os

import requests
from behave import given, then, when

AGENT_HOST = os.getenv("E2E_AGENT_HOSTNAME", "localhost")
AGENT_PORT = os.getenv("E2E_AGENT_PORT", "8081")
AGENT_BASE = f"http://{AGENT_HOST}:{AGENT_PORT}"


@given("The diagnostic agent pod is running")
def step_agent_pod_running(context):
    """Verify the agent pod is reachable."""
    try:
        resp = requests.get(f"{AGENT_BASE}/healthz", timeout=10)
        assert resp.status_code == 200, f"Agent not ready: {resp.status_code}"
    except requests.ConnectionError as exc:
        raise AssertionError(
            f"Cannot connect to diagnostic agent at {AGENT_BASE}: {exc}"
        ) from exc


@given("The diagnostic agent healthcheck returns 200")
def step_agent_healthcheck_ok(context):
    """Verify healthcheck returns 200."""
    resp = requests.get(f"{AGENT_BASE}/healthz", timeout=10)
    assert resp.status_code == 200


@when('I GET the diagnostic agent "{path}"')
def step_get_agent(context, path):
    """Send GET request to the agent."""
    context.response = requests.get(f"{AGENT_BASE}{path}", timeout=30)


@when('I POST to the diagnostic agent "{path}" with prompt "{prompt}"')
def step_post_agent(context, path, prompt):
    """Send POST request to the agent with a prompt."""
    context.response = requests.post(
        f"{AGENT_BASE}{path}",
        json={"prompt": prompt},
        timeout=300,
    )


@when('I POST to the diagnostic agent "{path}" with empty prompt')
def step_post_agent_empty(context, path):
    """Send POST request with empty prompt."""
    context.response = requests.post(
        f"{AGENT_BASE}{path}",
        json={"prompt": ""},
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
    assert text in context.response.text, (
        f"'{text}' not found in response: {context.response.text[:200]}"
    )


@then("The response contains a valid AgentRunResponse")
def step_valid_agent_response(context):
    """Verify response is a valid AgentRunResponse."""
    body = context.response.json()
    assert "output" in body, f"Missing 'output' in response: {body}"
    assert "agent_name" in body, f"Missing 'agent_name' in response: {body}"
    assert "success" in body, f"Missing 'success' in response: {body}"
    assert "output_type" in body, f"Missing 'output_type' in response: {body}"
    assert "schema_version" in body, f"Missing 'schema_version' in response: {body}"
    context.agent_output = body["output"]


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
    assert output.get(field) is True, (
        f"Field '{field}' is not true: {output.get(field)}"
    )
