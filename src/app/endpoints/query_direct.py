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

from fastapi import APIRouter, Depends, Request

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


query_direct_responses: dict[int | str, dict[str, Any]] = {
    200: {"description": "Query response via DirectExecutor."},
    401: {"description": "Unauthorized."},
    403: {"description": "Forbidden."},
    500: {"description": "Internal server error."},
}


class QueryDirectRequest:
    """Query parameters extracted from the request body."""

    def __init__(
        self,
        prompt: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        instructions: Optional[str] = None,
        mcp_servers: Optional[list[str]] = None,
        output_schema: Optional[dict[str, Any]] = None,
    ):
        """Initialize query request parameters."""
        self.prompt = prompt
        self.model = model
        self.provider = provider
        self.instructions = instructions
        self.mcp_servers = mcp_servers
        self.output_schema = output_schema


@router.post(
    "/query/direct",
    responses=query_direct_responses,
)
@authorize(Action.QUERY)
async def query_direct_handler(
    request: Request,
    body: dict[str, Any],
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Execute a query via DirectExecutor (no Llama Stack).

    Migration path endpoint — same as /query but uses pydantic-ai
    directly via cloud-agents' DirectExecutor. MCP servers are
    resolved from lightspeed-stack's configuration.

    Parameters:
        request: FastAPI request (used by middleware).
        body: Query parameters (prompt, model, provider, etc.).
        auth: Authentication tuple (used by middleware).

    Returns:
        Query response with output, transcript, and token usage.
    """
    _ = request
    _ = auth

    check_configuration_loaded(configuration)

    result = await execute_query_via_direct_executor(
        prompt=body.get("prompt", ""),
        model=body.get("model"),
        provider=body.get("provider"),
        instructions=body.get("instructions"),
        mcp_server_names=body.get("mcp_servers"),
        output_schema=body.get("output_schema"),
        context=body.get("context"),
    )

    return {
        "status": result.status,
        "response": result.output.get("response", "") if result.output else "",
        "output": result.output,
        "error": result.error,
        "transcript": (
            [e if isinstance(e, dict) else e.model_dump() for e in result.transcript]
            if result.transcript
            else []
        ),
        "token_usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
        "duration_ms": result.duration_ms,
    }
