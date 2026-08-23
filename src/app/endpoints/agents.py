"""Handler for REST API call to execute an agent."""

# pylint: disable=import-outside-toplevel

from typing import Annotated, Any

from cloud_agents.workflow.executor.step.base import StepInput
from cloud_agents.workflow.executor.step.dispatch import get_step_executor
from fastapi import APIRouter, Depends, HTTPException, Request, status

from authentication import get_auth_dependency
from authentication.interface import AuthTuple
from authorization.middleware import authorize
from configuration import configuration
from log import get_logger
from models.api.requests.agents import AgentRunRequest
from models.config import Action
from utils.endpoints import check_configuration_loaded

logger = get_logger(__name__)
router = APIRouter(tags=["agents"])


agent_run_responses: dict[int | str, dict[str, Any]] = {
    200: {"description": "Agent execution result."},
    401: {"description": "Unauthorized."},
    403: {"description": "Forbidden."},
    500: {"description": "Internal server error."},
}


@router.post(
    "/agents/run",
    responses=agent_run_responses,
)
@authorize(Action.AGENT_RUN)
async def run_agent_handler(
    request: Request,
    body: AgentRunRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Execute a single agent with inline parameters.

    Parameters:
        request: FastAPI request (used by middleware).
        body: Agent execution parameters.
        auth: Authentication tuple (used by middleware).

    Returns:
        Agent execution result with status, output, and transcript.
    """
    _ = auth
    _ = request

    check_configuration_loaded(configuration)

    step_def = {
        "name": "agent-run",
        "spawn": body.spawn,
        "prompt": body.prompt,
    }

    spawner_config = configuration.spawner_configuration
    if body.spawn == "ephemeral" and not spawner_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="spawn=ephemeral requires a spawner configuration.",
        )

    executor = get_step_executor(step_def, spawner=None)

    step_input = StepInput(
        prompt=body.prompt,
        provider={"name": body.provider or "", "model": body.model or ""},
        system_prompt=body.instructions,
        output_schema=body.output_schema,
        context=body.context or {},
        step_name="agent-run",
        output_key="result",
    )

    result = await executor.run(step_input)

    return {
        "status": result.status,
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
