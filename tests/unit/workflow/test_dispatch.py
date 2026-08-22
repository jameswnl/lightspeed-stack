"""Unit tests for the workflow step dispatch."""

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from workflow.step.dispatch import get_step_executor
from workflow.step.in_process import InProcessStepExecutor


class TestGetStepExecutor:
    """Tests for get_step_executor."""

    def test_spawn_none_returns_in_process(self) -> None:
        """spawn=none dispatches to InProcessStepExecutor."""
        step = {"name": "analyze", "spawn": "none"}
        executor = get_step_executor(step)
        assert isinstance(executor, InProcessStepExecutor)

    def test_default_spawn_is_none(self) -> None:
        """Default spawn mode is none (in-process)."""
        step = {"name": "analyze"}
        executor = get_step_executor(step)
        assert isinstance(executor, InProcessStepExecutor)

    def test_spawn_local_raises_not_implemented(self) -> None:
        """spawn=local raises NotImplementedError."""
        step = {"name": "remediate", "spawn": "local"}
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            get_step_executor(step)

    def test_spawn_ephemeral_without_spawner_raises(self) -> None:
        """spawn=ephemeral without a spawner raises ValueError."""
        step = {"name": "remediate", "spawn": "ephemeral"}
        with pytest.raises(ValueError, match="no spawner"):
            get_step_executor(step)

    def test_spawn_unknown_raises(self) -> None:
        """Unknown spawn mode raises ValueError."""
        step = {"name": "remediate", "spawn": "docker"}
        with pytest.raises(ValueError, match="Unknown spawn mode"):
            get_step_executor(step)

    def test_spawn_ephemeral_with_spawner(self, mocker: MockerFixture) -> None:
        """spawn=ephemeral with a spawner delegates to cloud-agents."""
        mock_spawner = mocker.MagicMock()
        mock_ca_executor = mocker.MagicMock()
        mock_ca_dispatch = mocker.MagicMock(return_value=mock_ca_executor)
        mocker.patch(
            "cloud_agents.workflow.executor.step.dispatch.get_step_executor",
            mock_ca_dispatch,
        )

        step = {"name": "remediate", "spawn": "ephemeral"}
        executor = get_step_executor(step, spawner=mock_spawner)

        mock_ca_dispatch.assert_called_once_with(step, mock_spawner, None)
        assert executor is mock_ca_executor
