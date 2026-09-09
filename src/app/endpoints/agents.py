"""Handler for REST API call to execute an agent."""

# pylint: disable=import-outside-toplevel

from typing import Annotated, Any

from cloud_agents.workflow.executor.step.base import StepInput, StepMetadata
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
from workflow.provider_credentials import credentials_secret_for
from workflow.spawner_factory import build_spawner

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
    _ = request

    check_configuration_loaded(configuration)

    inference = configuration.inference
    spawner = None
    sandbox_image = None
    provider: dict[str, Any] = {
        "name": body.provider or inference.default_provider or "",
        "model": body.model or inference.default_model or "",
    }
    if body.spawn == "ephemeral":
        spawner_config = configuration.spawner_configuration
        if not spawner_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="spawn=ephemeral requires a spawner configuration.",
            )
        spawner = build_spawner(spawner_config)
        sandbox_image = body.sandbox_image or spawner_config.sandbox_image
        cred_secret = credentials_secret_for(provider["name"])
        if cred_secret:
            provider["credentials_secret"] = cred_secret

    executor = get_step_executor(
        {"name": "agent-run", "spawn": body.spawn, "prompt": body.prompt},
        spawner=spawner,
    )

    user_id, _, _, _ = auth

    step_input_kwargs: dict[str, Any] = {
        "prompt": body.prompt,
        "provider": provider,
        "system_prompt": body.instructions,
        "output_schema": body.output_schema,
        "tools": body.tools,
        "mcp_servers": body.mcp_servers,
        "allowed_skills": body.allowed_skills,
        "context": body.context or {},
        "step_name": "agent-run",
        "output_key": "result",
        # Raw step definition so the ephemeral path (SandboxExecutor ->
        # step_runner -> spawner.spawn) sees allowed_skills and can
        # materialize just that subset with per-skill Landlock grants.
        # Other executors ignore raw_step and read allowed_skills directly.
        # mcp_servers names are selected here too: step_runner only injects
        # catalog entries (StepInput.mcp_servers) whose names the step lists,
        # so without this the sandbox never sees request-level MCP servers.
        "raw_step": {
            "name": "agent-run",
            "prompt": body.prompt,
            "output_key": "result",
            "allowed_skills": body.allowed_skills,
            "mcp_servers": [s["name"] for s in (body.mcp_servers or []) if "name" in s],
        },
        "metadata": StepMetadata(user_id=user_id),
    }
    if sandbox_image:
        step_input_kwargs["sandbox_image"] = sandbox_image
    step_input = StepInput(**step_input_kwargs)

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
