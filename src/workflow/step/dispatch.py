"""Step executor dispatch for lightspeed-stack.

Extends cloud-agents' dispatch with spawn=none support via
InProcessStepExecutor.
"""

# pylint: disable=import-outside-toplevel

from __future__ import annotations

from typing import Any

from cloud_agents.workflow.executor.step.base import StepExecutor

from log import get_logger
from workflow.step.in_process import InProcessStepExecutor

logger = get_logger(__name__)


def get_step_executor(
    step: dict[str, Any],
    spawner: Any = None,
    transcript_store: Any = None,
) -> StepExecutor:
    """Select the right StepExecutor based on step spawn mode.

    Parameters:
        step: Step definition dict from the workflow.
        spawner: AgentSpawner instance (required for ephemeral).
        transcript_store: Optional TranscriptStore.

    Returns:
        StepExecutor for the step's spawn mode.

    Raises:
        ValueError: If spawn mode is unknown or ephemeral without spawner.
        NotImplementedError: If spawn mode is not yet implemented.
    """
    mode = step.get("spawn", "none")
    step_name = step.get("name", "unknown")

    if mode == "none":
        logger.debug(
            "Step '%s' using InProcessStepExecutor (spawn: none)",
            step_name,
        )
        return InProcessStepExecutor()

    if mode == "local":
        raise NotImplementedError(
            f"spawn: local is not yet implemented (step '{step_name}'). "
            "Use spawn: none or spawn: ephemeral."
        )

    if mode == "ephemeral":
        if spawner is None:
            raise ValueError(
                f"Step '{step_name}' requires spawn: ephemeral but no "
                "spawner is configured."
            )
        from cloud_agents.workflow.executor.step.dispatch import (
            get_step_executor as ca_get_step_executor,
        )

        return ca_get_step_executor(step, spawner, transcript_store)

    raise ValueError(
        f"Unknown spawn mode '{mode}' for step '{step_name}'. "
        "Valid values: none, local, ephemeral."
    )
