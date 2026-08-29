"""Self-test for the mock OpenAI Responses API server.

Validates the mock's /v1/responses handler against the real openai SDK's
response schema, independent of pydantic-ai/cloud_agents -- this is the
thing spawn=none/local e2e tests point OPENAI_BASE_URL at in CI so they
don't need a real OPENAI_API_KEY or network egress.
"""

from __future__ import annotations

import json

import httpx
from openai.types.responses import Response, ResponseOutputMessage, ResponseOutputText

from .mock_llm_server import DEFAULT_RESPONSE_TEXT, MockResponsesServer


def _first_output_text(parsed: Response) -> str:
    """Extract the first output_text from a parsed Responses API result."""
    item = parsed.output[0]
    assert isinstance(item, ResponseOutputMessage)
    content = item.content[0]
    assert isinstance(content, ResponseOutputText)
    return content.text


def test_mock_responses_server_returns_valid_response_shape() -> None:
    """POSTing to /v1/responses returns a body the openai SDK can parse."""
    server = MockResponsesServer(response_text="hello from the mock")
    server.start()
    try:
        result = httpx.post(
            f"{server.base_url}/responses",
            json={"model": "gpt-4o", "input": "hi"},
            timeout=5.0,
        )
        assert result.status_code == 200
        parsed = Response.model_validate(result.json())
        assert _first_output_text(parsed) == "hello from the mock"
        assert parsed.status == "completed"
        assert parsed.usage is not None
        assert parsed.usage.input_tokens > 0
        assert parsed.usage.output_tokens > 0
    finally:
        server.stop()


def test_mock_responses_server_default_text() -> None:
    """Without an explicit response_text, the server uses the module default."""
    server = MockResponsesServer()
    server.start()
    try:
        result = httpx.post(
            f"{server.base_url}/responses",
            json={"model": "gpt-4o", "input": "hi"},
            timeout=5.0,
        )
        parsed = Response.model_validate(result.json())
        assert _first_output_text(parsed) == DEFAULT_RESPONSE_TEXT
    finally:
        server.stop()


def test_mock_responses_server_rejects_unknown_path() -> None:
    """Any path other than /v1/responses 404s."""
    server = MockResponsesServer()
    server.start()
    try:
        result = httpx.post(f"{server.base_url}/chat/completions", json={}, timeout=5.0)
        assert result.status_code == 404
    finally:
        server.stop()


def test_native_structured_output_returns_schema_conforming_json() -> None:
    """A json_schema-format request gets back JSON satisfying the schema.

    cloud-agents' native structured-output mode (ModelRequestParameters
    output_mode="native") sends `text.format.schema`, then json.loads()s
    the returned text directly -- plain canned prose fails that with
    "LLM returned non-JSON response but output_schema was requested".
    """
    server = MockResponsesServer()
    server.start()
    try:
        result = httpx.post(
            f"{server.base_url}/responses",
            json={
                "model": "gpt-4o",
                "input": "hi",
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "output",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "healthy": {"type": "boolean"},
                                "reason": {"type": "string"},
                            },
                            "required": ["healthy", "reason"],
                        },
                    }
                },
            },
            timeout=5.0,
        )
        parsed = Response.model_validate(result.json())
        decoded = json.loads(_first_output_text(parsed))
        assert decoded.keys() == {"healthy", "reason"}
        assert isinstance(decoded["healthy"], bool)
        assert isinstance(decoded["reason"], str)
    finally:
        server.stop()
