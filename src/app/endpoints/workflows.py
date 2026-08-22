"""Handler for REST API calls to manage workflow executions."""

# pylint: disable=import-outside-toplevel,global-statement,invalid-name

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from authentication import get_auth_dependency
from authentication.interface import AuthTuple
from authorization.middleware import authorize
from configuration import configuration
from log import get_logger
from models.api.requests.agents import ApproveWorkflowRequest, RunWorkflowRequest
from models.config import Action
from utils.endpoints import check_configuration_loaded

logger = get_logger(__name__)
router = APIRouter(tags=["workflows"])

_executor = None


def _get_executor() -> Any:
    """Get the workflow executor singleton.

    Returns:
        The workflow executor instance.

    Raises:
        HTTPException: If the workflow engine is not enabled.
    """
    global _executor
    if _executor is not None:
        return _executor

    if not configuration.workflow_engine_configuration.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow engine is not enabled. "
            "Set workflow_engine.enabled=true in configuration.",
        )

    from workflow.executor_factory import create_workflow_executor

    _executor = create_workflow_executor()
    return _executor


@router.post(
    "/workflows/run",
    status_code=status.HTTP_202_ACCEPTED,
)
@authorize(Action.WORKFLOW_START)
async def start_workflow_handler(
    request: Request,
    body: RunWorkflowRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Start a new workflow execution.

    Parameters:
        request: FastAPI request (used by middleware).
        body: Workflow definition and configuration.
        auth: Authentication tuple (used by middleware).

    Returns:
        Workflow ID and initial status.
    """
    _ = request
    user_id, username, _, _ = auth

    check_configuration_loaded(configuration)
    executor = _get_executor()

    inference = configuration.inference
    provider = body.provider or {
        "name": inference.default_provider or "",
        "model": inference.default_model or "",
    }

    workflow_input = {
        "definition": body.definition,
        "provider": provider,
        "sandbox_image": body.sandbox_image or "lightspeed-agentic-sandbox:latest",
        "approval_policy": body.approval_policy,
        "authz_context": {
            "user_id": user_id,
            "username": username,
        },
    }

    workflow_id = await executor.start(workflow_input)

    logger.info(
        "Workflow started: id=%s, user=%s",
        workflow_id,
        username,
    )

    return {
        "workflow_id": workflow_id,
        "status": "running",
    }


@router.get("/workflows/{workflow_id}")
@authorize(Action.WORKFLOW_VIEW)
async def get_workflow_handler(
    request: Request,
    workflow_id: str,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Get the current status of a workflow execution.

    Parameters:
        request: FastAPI request (used by middleware).
        workflow_id: Target workflow execution ID.
        auth: Authentication tuple (used by middleware).

    Returns:
        Workflow status including step results and events.
    """
    _ = auth
    _ = request

    check_configuration_loaded(configuration)
    executor = _get_executor()

    try:
        wf_status = await executor.get_status(workflow_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        ) from exc

    return {
        "workflow_id": wf_status.workflow_id,
        "status": wf_status.status,
        "steps": wf_status.steps,
        "events": wf_status.events,
        "is_terminal": wf_status.is_terminal,
    }


@router.post("/workflows/{workflow_id}/approve")
@authorize(Action.WORKFLOW_APPROVE)
async def approve_workflow_handler(
    request: Request,
    workflow_id: str,
    body: ApproveWorkflowRequest,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, str]:
    """Send an approval signal to a paused workflow.

    Parameters:
        request: FastAPI request (used by middleware).
        workflow_id: Target workflow execution ID.
        body: Approval decision.
        auth: Authentication tuple (used by middleware).

    Returns:
        Acknowledgement message.
    """
    _ = request
    _, username, _, _ = auth

    check_configuration_loaded(configuration)
    executor = _get_executor()

    from cloud_agents.workflow.executor.base import ApprovalDecision

    decision = ApprovalDecision(
        step_name=body.step_name,
        decision=body.decision,
        approver=body.approver or username,
    )

    try:
        await executor.approve(workflow_id, decision)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    logger.info(
        "Workflow %s: step '%s' %s by %s",
        workflow_id,
        body.step_name,
        body.decision,
        decision.approver,
    )

    return {"message": f"Step '{body.step_name}' {body.decision}."}


@router.post("/workflows/{workflow_id}/cancel")
@authorize(Action.WORKFLOW_CANCEL)
async def cancel_workflow_handler(
    request: Request,
    workflow_id: str,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, str]:
    """Cancel a running workflow.

    Parameters:
        request: FastAPI request (used by middleware).
        workflow_id: Target workflow execution ID.
        auth: Authentication tuple (used by middleware).

    Returns:
        Acknowledgement message.
    """
    _ = auth
    _ = request

    check_configuration_loaded(configuration)
    executor = _get_executor()

    try:
        await executor.cancel(workflow_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        ) from exc

    logger.info("Workflow %s cancelled", workflow_id)

    return {"message": f"Workflow '{workflow_id}' cancelled."}


@router.get("/workflows/{workflow_id}/transcripts")
@authorize(Action.WORKFLOW_VIEW)
async def get_transcripts_handler(
    request: Request,
    workflow_id: str,
    auth: Annotated[AuthTuple, Depends(get_auth_dependency())],
) -> dict[str, Any]:
    """Get step transcripts for a workflow.

    Parameters:
        request: FastAPI request (used by middleware).
        workflow_id: Target workflow execution ID.
        auth: Authentication tuple (used by middleware).

    Returns:
        Transcripts keyed by step name.
    """
    _ = auth
    _ = request

    check_configuration_loaded(configuration)
    executor = _get_executor()

    try:
        transcripts = await executor.get_step_transcripts(workflow_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        ) from exc

    return {"workflow_id": workflow_id, "transcripts": transcripts}
