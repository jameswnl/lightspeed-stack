"""Handler for /query/direct — query via DirectExecutor (no Llama Stack).

Parallel endpoint to /query that uses cloud-agents' DirectExecutor
instead of build_agent() → Llama Stack. Uses the SAME request/response
shape as /query so existing callers can switch without code changes.

Differences from /query (internal only):
- Uses pydantic-ai directly (no Llama Stack in the path)
- MCP servers connected via pydantic-ai's native MCPToolset
- No shield moderation, RAG, compaction yet (see issue #9)
"""

import json
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

    Matches QueryRequest field names for API compatibility.

    Attributes:
        query: The query string (same as QueryRequest.query).
        conversation_id: Optional conversation ID for multi-turn.
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

    conversation_id: Optional[str] = Field(
        None,
        description="The optional conversation ID (UUID)",
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


@router.post(
    "/query/direct",
)
@authorize(Action.QUERY)
async def query_direct_handler(
    request: Request,
    body: QueryDirectRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Execute a query via DirectExecutor (no Llama Stack).

    Uses the same request/response shape as /v1/query so existing
    callers can switch by changing the URL only.

    Parameters:
        request: FastAPI request (used by auth middleware).
        body: Query parameters (same shape as QueryRequest).
        auth: Authentication tuple (used by auth decorator).

    Returns:
        Response matching QueryResponse shape.
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

    output = result.output
    response_text = ""
    if isinstance(output, dict):
        response_text = output.get("response", "")
    elif output is not None:
        response_text = str(output)

    return {
        "conversation_id": body.conversation_id or "",
        "response": response_text,
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


@router.post(
    "/query/direct/stream",
)
@authorize(Action.QUERY)
async def query_direct_stream_handler(
    request: Request,
    body: QueryDirectRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> StreamingResponse:
    """Stream a query via DirectExecutor as SSE events.

    Parameters:
        request: FastAPI request (used by auth middleware).
        body: Query parameters (same shape as QueryRequest).
        auth: Authentication tuple (used by auth decorator).

    Returns:
        StreamingResponse with SSE events.
    """
    _ = request
    user_id, username, _, _ = auth

    check_configuration_loaded(configuration)

    async def event_generator():  # type: ignore[return]
        """Generate SSE events from DirectExecutor stream."""
        try:
            async for event in stream_query_via_direct_executor(
                prompt=body.query,
                model=body.model,
                provider=body.provider,
                instructions=body.system_prompt,
                mcp_server_names=body.mcp_servers,
                output_schema=body.output_schema,
                user_id=user_id,
                username=username,
            ):
                event_data = {
                    "type": event.type,
                    "data": event.data,
                }
                if event.result:
                    event_data["result"] = {
                        "status": event.result.status,
                        "output": event.result.output,
                        "token_usage": {
                            "input_tokens": event.result.input_tokens,
                            "output_tokens": event.result.output_tokens,
                        },
                        "duration_ms": event.result.duration_ms,
                    }
                yield f"data: {json.dumps(event_data)}\n\n"
        except ValueError as exc:
            error_event = {"type": "error", "data": {"message": str(exc)}}
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
