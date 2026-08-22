"""Workflow executor factory for lightspeed-stack.

Creates a LocalExecutor wired to storage stores and an optional spawner,
driven by lightspeed-stack's configuration rather than environment variables.
"""

from typing import Any, Optional

from cloud_agents.workflow.executor.base import WorkflowExecutor
from cloud_agents.workflow.executor.local.executor import LocalExecutor

from log import get_logger
from workflow.storage import WorkflowStorageFactory

logger = get_logger(__name__)


def create_workflow_executor(
    spawner: Optional[Any] = None,
) -> WorkflowExecutor:
    """Create a LocalExecutor with storage stores and optional spawner.

    Parameters:
        spawner: Optional AgentSpawner for ephemeral step execution.

    Returns:
        Configured WorkflowExecutor instance.
    """
    run_state_store = WorkflowStorageFactory.get_run_state_store()
    transcript_store = WorkflowStorageFactory.get_transcript_store()

    logger.info("Creating LocalExecutor for workflow engine")
    return LocalExecutor(
        spawner=spawner,
        run_state_store=run_state_store,
        transcript_store=transcript_store,
    )
