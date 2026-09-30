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

import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

DEFAULT_RESPONSE_TEXT = "Mock LLM response: acknowledged."

# Monotonic call ids so a retried first turn never reuses a call_id
# (itertools.count.__next__ is atomic under the GIL; the ThreadingHTTPServer
# handler threads only ever call it under here).
_CALL_IDS = itertools.count(1)


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


def _canned_function_call_body(
    call_id: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Build a minimal body whose output is a single scripted function_call."""
    body = _canned_response_body("")
    body["output"] = [
        {
            "id": call_id,
            "type": "function_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments),
        }
    ]
    return body


def _input_has_function_call_output(request_body: dict[str, Any]) -> bool:
    """Detect a tool-result round-trip in the request input.

    pydantic-ai's second turn sends the tool's return value as
    {"type": "function_call_output", ...} input items -- that is the
    signal to stop scripting a tool call and answer with final text.
    Non-list input shapes (str, None, or unexpected truthy values) are
    treated as "no tool results yet".
    """
    input_value = request_body.get("input")
    if not isinstance(input_value, list):
        return False
    for item in input_value:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            return True
    return False


class _ResponsesHandler(BaseHTTPRequestHandler):
    """Handles POST /v1/responses with a canned response body."""

    response_text: str
    tool_call: Optional[dict[str, Any]] = None

    def log_message(  # pylint: disable=arguments-differ
        self, format_: str, *args: Any
    ) -> None:
        """Silence default request logging."""

    def _select_body(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Choose the scripted or canned response body for a request.

        With a tool_call script configured: a request that declares tools
        and carries no tool results yet gets a scripted function_call;
        anything else (including the tool-result turn) gets text.
        """
        script = self.tool_call
        if (
            script
            and request_body.get("tools")
            and not _input_has_function_call_output(request_body)
        ):
            return _canned_function_call_body(
                call_id=f"call_mock_{next(_CALL_IDS)}",
                name=script.get("name", ""),
                arguments=script.get("arguments") or {},
            )
        text = _response_text_for_request(request_body, self.response_text)
        if script and _input_has_function_call_output(request_body):
            text = script.get("result_text", text)
        return _canned_response_body(text)

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

        payload = json.dumps(self._select_body(request_body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class MockResponsesServer:
    """A loopback-only mock of OpenAI's /v1/responses endpoint.

    Optionally scripts one deterministic tool call: the agent's first
    request (declares tools, no tool results yet) gets a function_call
    for ``tool_call["name"]`` with ``tool_call["arguments"]``; once the
    tool result round-trips, the mock answers with
    ``tool_call["result_text"]``. Used by transcript-parity tests so
    spawn=none/local runs make identical, reproducible tool calls.
    """

    def __init__(
        self,
        response_text: str = DEFAULT_RESPONSE_TEXT,
        tool_call: Optional[dict[str, Any]] = None,
    ) -> None:
        """Build the server (not yet listening -- call start()).

        Parameters:
            response_text: Canned final text for plain requests.
            tool_call: Optional script dict with "name", "arguments",
                and "result_text" keys enabling tool-call scripting.
        """
        handler = type(
            "_BoundResponsesHandler",
            (_ResponsesHandler,),
            {"response_text": response_text, "tool_call": tool_call},
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
