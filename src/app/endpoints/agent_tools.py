"""Handler for REST API call to list available agent tools."""

from typing import Annotated, Any

from cloud_agents.workflow.executor.step.tools import get_tools, list_tools
from fastapi import APIRouter, Depends, Request

from authentication import get_auth_dependency
from authentication.interface import AuthTuple
from authorization.middleware import authorize
from log import get_logger
from models.config import Action

logger = get_logger(__name__)
router = APIRouter(tags=["tools"])


@router.get("/agent-tools")
@authorize(Action.GET_TOOLS)
async def list_agent_tools_handler(
    request: Request,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """List registered agent tools available for spawn:none/local steps.

    These are Python functions registered via cloud-agents' @step_tool
    decorator. Different from MCP tools (which are remote servers).

    Parameters:
        request: FastAPI request (consumed by @authorize decorator).
        auth: Authentication tuple (consumed by @authorize decorator).

    Returns:
        List of tool names and descriptions.
    """
    _ = request
    _ = auth

    tool_names = list_tools()
    tool_defs = get_tools(tool_names) if tool_names else []

    tools = []
    for tool_def in tool_defs:
        tools.append(
            {
                "name": tool_def.name,
                "description": tool_def.description or "",
            }
        )

    return {
        "tools": tools,
        "count": len(tools),
    }
