"""Workflow executor factory for lightspeed-stack.

Creates a LocalWorkflowRunner wired to storage stores and an optional
spawner, driven by lightspeed-stack's configuration.
"""

from typing import Any, Optional

from cloud_agents.workflow.executor.base import WorkflowRunner
from cloud_agents.workflow.executor.local.executor import LocalWorkflowRunner

from log import get_logger
from workflow.storage import WorkflowStorageFactory

logger = get_logger(__name__)


def create_workflow_runner(
    spawner: Optional[Any] = None,
) -> WorkflowRunner:
    """Create a LocalWorkflowRunner with storage stores and optional spawner.

    Parameters:
        spawner: Optional AgentSpawner for ephemeral step execution.

    Returns:
        Configured WorkflowRunner instance.
    """
    run_state_store = WorkflowStorageFactory.get_run_state_store()
    transcript_store = WorkflowStorageFactory.get_transcript_store()

    logger.info("Creating LocalWorkflowRunner for workflow engine")
    return LocalWorkflowRunner(
        spawner=spawner,
        run_state_store=run_state_store,
        transcript_store=transcript_store,
    )
