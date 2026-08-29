"""In-process mock of OpenAI's Responses API for cloud-agents e2e tests.

cloud-agents' step executors always resolve a bare "openai:<model>" model
string, which pydantic-ai routes to OpenAIResponsesModel -> POST
/v1/responses, non-streaming, with no tools wired for a plain agent-run
(see DirectExecutor/SubprocessExecutor). This server implements just that
one endpoint so spawn=none/local e2e tests can run against
OPENAI_BASE_URL without a real OPENAI_API_KEY or network egress -- e.g.
in CI. spawn=ephemeral tests still require a real key and gateway.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_RESPONSE_TEXT = "Mock LLM response: acknowledged."


def _placeholder_for_schema(schema: dict[str, Any]) -> Any:
    """Build a minimal JSON-serializable placeholder value for a JSON Schema type.

    Only handles the shapes cloud-agents' workflow step output_schemas
    actually use (object/string/boolean/integer/number/array) -- not a
    general-purpose JSON Schema example generator.
    """
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required") or list(properties)
        return {
            key: _placeholder_for_schema(properties.get(key, {})) for key in required
        }
    if schema_type == "boolean":
        return True
    if schema_type in ("integer", "number"):
        return 0
    if schema_type == "array":
        return []
    return "mock"


def _response_text_for_request(request_body: dict[str, Any], default_text: str) -> str:
    """Return the canned text, or schema-conforming JSON for native structured output.

    cloud-agents' native structured-output mode sends `text.format.schema`
    (a JSON Schema) and then json.loads()s the returned text directly --
    plain prose fails that, so a json_schema-format request gets a JSON
    string satisfying the schema's required properties instead.
    """
    output_format = request_body.get("text", {}).get("format", {})
    schema = output_format.get("schema")
    if output_format.get("type") == "json_schema" and schema:
        return json.dumps(_placeholder_for_schema(schema))
    return default_text


def _canned_response_body(text: str) -> dict[str, Any]:
    """Build a minimal body satisfying openai.types.responses.Response."""
    return {
        "id": "resp_mock",
        "object": "response",
        "created_at": 0,
        "model": "gpt-4o-mock",
        "status": "completed",
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 8,
            "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
            "output_tokens": 9,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 17,
        },
    }


class _ResponsesHandler(BaseHTTPRequestHandler):
    """Handles POST /v1/responses with a canned response body."""

    response_text: str = DEFAULT_RESPONSE_TEXT

    def log_message(  # pylint: disable=arguments-differ
        self, format_: str, *args: Any
    ) -> None:
        """Silence default request logging."""

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Return a canned Responses-API body for /v1/responses, 404 otherwise."""
        if self.path != "/v1/responses":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            request_body = json.loads(raw_body)
        except json.JSONDecodeError:
            request_body = {}

        text = _response_text_for_request(request_body, self.response_text)
        payload = json.dumps(_canned_response_body(text)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class MockResponsesServer:
    """A loopback-only mock of OpenAI's /v1/responses endpoint."""

    def __init__(self, response_text: str = DEFAULT_RESPONSE_TEXT) -> None:
        """Build the server (not yet listening -- call start())."""
        handler = type(
            "_BoundResponsesHandler",
            (_ResponsesHandler,),
            {"response_text": response_text},
        )
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        """The ephemeral port the server is bound to."""
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        """The server's base URL, suitable for OPENAI_BASE_URL."""
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> None:
        """Start serving in a background daemon thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop serving and release the socket."""
        self._httpd.shutdown()
        self._httpd.server_close()
