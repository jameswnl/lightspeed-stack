"""Handler for /query/direct — query via DirectExecutor (no Llama Stack).

Parallel endpoint to /query that uses cloud-agents' DirectExecutor
instead of build_agent() → Llama Stack. Uses the SAME request field
names as /query so callers can switch by changing the URL.

Current limitations (tracked in issue #9):
- No shield moderation, RAG, compaction
- conversation_id is NOT wired — multi-turn state is not managed
- Streaming yields token events only when tools/MCP are configured;
  plain prompts produce a single complete event
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from authentication import get_auth_dependency
from authentication.interface import AuthTuple
from authorization.middleware import authorize
from configuration import configuration
from log import get_logger
from models.config import Action
from utils.endpoints import check_configuration_loaded
from workflow.query_executor import (
    execute_query_via_direct_executor,
    stream_query_via_direct_executor,
)

logger = get_logger(__name__)
router = APIRouter(tags=["query"])


class QueryDirectRequest(BaseModel):
    """Request body for POST /v1/query/direct.

    Field names match QueryRequest for API compatibility.
    conversation_id is accepted but NOT used yet (issue #9 gap #1).

    Attributes:
        query: The query string.
        provider: Optional provider name.
        model: Optional model name.
        system_prompt: Optional system prompt.
        mcp_servers: Optional MCP server names from config.
        output_schema: Optional JSON Schema for structured output.
    """

    query: str = Field(
        ...,
        description="The query string",
        examples=["What is Kubernetes?"],
    )

    provider: Optional[str] = Field(
        None,
        description="The optional provider",
    )

    model: Optional[str] = Field(
        None,
        description="The optional model",
    )

    system_prompt: Optional[str] = Field(
        None,
        description="The optional system prompt",
    )

    mcp_servers: Optional[list[str]] = Field(
        None,
        description="MCP server names from config (None = all configured)",
    )

    output_schema: Optional[dict[str, Any]] = Field(
        None,
        description="JSON Schema for structured output",
    )


def _serialize_output(output: Any) -> str:
    """Serialize executor output to a response string.

    Parameters:
        output: StepResult.output — can be dict, str, int, or None.

    Returns:
        String representation of the output.
    """
    if output is None:
        return ""
    if isinstance(output, dict):
        return output.get("response", str(output))
    return str(output)


@router.post("/query/direct")
@authorize(Action.QUERY)
async def query_direct_handler(
    request: Request,
    body: QueryDirectRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Execute a query via DirectExecutor (no Llama Stack).

    Parameters:
        request: FastAPI request (consumed by @authorize decorator).
        body: Query parameters (field names match /query).
        auth: Authentication tuple (consumed by @authorize decorator).

    Returns:
        Response matching QueryResponse field names.
    """
    _ = request
    user_id, username, _, _ = auth

    check_configuration_loaded(configuration)

    try:
        result = await execute_query_via_direct_executor(
            prompt=body.query,
            model=body.model,
            provider=body.provider,
            instructions=body.system_prompt,
            mcp_server_names=body.mcp_servers,
            output_schema=body.output_schema,
            user_id=user_id,
            username=username,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {
        "conversation_id": None,
        "response": _serialize_output(result.output),
        "truncated": False,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "available_quotas": {},
        "tool_calls": [],
        "tool_results": [],
        "rag_chunks": [],
        "referenced_documents": [],
        "request_id": "",
        "interrupted": False,
    }


@router.post("/query/direct/stream")
@authorize(Action.STREAMING_QUERY)
async def query_direct_stream_handler(
    request: Request,
    body: QueryDirectRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> StreamingResponse:
    """Stream a query via DirectExecutor as SSE events.

    Token-by-token streaming is active when tools or MCP servers
    are configured. Without tools, yields a single complete event.

    Parameters:
        request: FastAPI request (consumed by @authorize decorator).
        body: Query parameters (field names match /query).
        auth: Authentication tuple (consumed by @authorize decorator).

    Returns:
        StreamingResponse with SSE events.
    """
    _ = request
    user_id, username, _, _ = auth

    check_configuration_loaded(configuration)

    try:
        step_input_args = dict(
            prompt=body.query,
            model=body.model,
            provider=body.provider,
            instructions=body.system_prompt,
            mcp_server_names=body.mcp_servers,
            output_schema=body.output_schema,
            user_id=user_id,
            username=username,
        )
        from workflow.query_executor import (  # pylint: disable=import-outside-toplevel
            _validate_and_build_step_input,
        )

        _validate_and_build_step_input(
            prompt=body.query,
            model=body.model,
            provider=body.provider,
            instructions=body.system_prompt,
            mcp_server_names=body.mcp_servers,
            output_schema=body.output_schema,
            context=None,
            step_name="validate",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    async def event_generator() -> AsyncIterator[str]:
        """Generate SSE events from DirectExecutor stream."""
        try:
            async for event in stream_query_via_direct_executor(
                **step_input_args,
            ):
                event_data: dict[str, Any] = {
                    "type": event.type,
                    "data": event.data,
                }
                if event.result:
                    event_data["result"] = {
                        "status": event.result.status,
                        "output": _serialize_output(event.result.output),
                        "input_tokens": event.result.input_tokens,
                        "output_tokens": event.result.output_tokens,
                        "duration_ms": event.result.duration_ms,
                    }
                yield f"event: {event.type}\ndata: {json.dumps(event_data, default=str)}\n\n"
        except Exception as exc:
            logger.error("Stream error: %s", exc, exc_info=True)
            error_event = {
                "type": "error",
                "data": {"message": str(exc)},
            }
            yield f"event: error\ndata: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
