"""Handler for /query/direct — query via DirectExecutor (no Llama Stack).

Parallel endpoint to /query that uses cloud-agents' DirectExecutor
instead of build_agent() → Llama Stack. Demonstrates the migration
path for unifying the chatbot with the cloud-agents executor model.

Differences from /query:
- Uses pydantic-ai directly (no Llama Stack in the path)
- MCP servers connected via pydantic-ai's native MCPToolset
- No shield moderation (shields need separate migration)
- No RAG context building (RAG needs separate migration)
- No conversation compaction (compaction needs separate migration)
- No Splunk telemetry (telemetry needs separate migration)
"""

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from authentication import get_auth_dependency
from authentication.interface import AuthTuple
from authorization.middleware import authorize
from configuration import configuration
from log import get_logger
from models.config import Action
from utils.endpoints import check_configuration_loaded
from workflow.query_executor import execute_query_via_direct_executor

logger = get_logger(__name__)
router = APIRouter(tags=["query"])


class QueryDirectRequest(BaseModel):
    """Request body for POST /v1/query/direct.

    Attributes:
        prompt: The user's query text.
        model: Model name (e.g. "gpt-4o-mini").
        provider: Provider name (e.g. "openai").
        instructions: System prompt / instructions.
        mcp_servers: MCP server names to include from config.
        output_schema: Optional JSON Schema for structured output.
        context: Prior conversation context.
    """

    prompt: str = Field(
        ...,
        description="The user's query text.",
    )

    model: Optional[str] = Field(
        None,
        description="Model name (falls back to inference.default_model).",
    )

    provider: Optional[str] = Field(
        None,
        description="Provider name (falls back to inference.default_provider).",
    )

    instructions: Optional[str] = Field(
        None,
        description="System prompt / instructions for the agent.",
    )

    mcp_servers: Optional[list[str]] = Field(
        None,
        description="MCP server names from config (None = all configured).",
    )

    output_schema: Optional[dict[str, Any]] = Field(
        None,
        description="JSON Schema for structured output.",
    )

    context: Optional[dict[str, Any]] = Field(
        None,
        description="Prior conversation context.",
    )


query_direct_responses: dict[int | str, dict[str, Any]] = {
    200: {"description": "Query response via DirectExecutor."},
    400: {"description": "Invalid request (unknown MCP server, missing model)."},
    401: {"description": "Unauthorized."},
    403: {"description": "Forbidden."},
    500: {"description": "Internal server error."},
}


@router.post(
    "/query/direct",
    responses=query_direct_responses,
)
@authorize(Action.QUERY)
async def query_direct_handler(
    request: Request,
    body: QueryDirectRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Execute a query via DirectExecutor (no Llama Stack).

    Migration path endpoint — same as /query but uses pydantic-ai
    directly via cloud-agents' DirectExecutor. MCP servers are
    resolved from lightspeed-stack's configuration.

    Parameters:
        request: FastAPI request (used by auth middleware).
        body: Query parameters.
        auth: Authentication tuple (used by auth decorator).

    Returns:
        Query response with output, transcript, and token usage.
    """
    # request is consumed by the @authorize decorator
    _ = request

    user_id, username, _, _ = auth

    check_configuration_loaded(configuration)

    try:
        result = await execute_query_via_direct_executor(
            prompt=body.prompt,
            model=body.model,
            provider=body.provider,
            instructions=body.instructions,
            mcp_server_names=body.mcp_servers,
            output_schema=body.output_schema,
            context=body.context,
            user_id=user_id,
            username=username,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {
        "status": result.status,
        "response": (result.output.get("response", "") if result.output else ""),
        "output": result.output,
        "error": result.error,
        "transcript": (
            [e if isinstance(e, dict) else {} for e in result.transcript]
            if result.transcript
            else []
        ),
        "token_usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
        "duration_ms": result.duration_ms,
    }
