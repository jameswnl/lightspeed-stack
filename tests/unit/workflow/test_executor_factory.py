"""Unit tests for the workflow executor factory."""

# pylint: disable=protected-access

from __future__ import annotations

from pytest_mock import MockerFixture

from workflow.executor_factory import create_workflow_executor


class TestCreateWorkflowExecutor:
    """Tests for create_workflow_executor."""

    def test_creates_local_executor(self, mocker: MockerFixture) -> None:
        """Creates a LocalExecutor wired to stores."""
        mock_run_store = mocker.MagicMock()
        mock_transcript_store = mocker.MagicMock()
        mock_factory = mocker.patch("workflow.executor_factory.WorkflowStorageFactory")
        mock_factory.get_run_state_store.return_value = mock_run_store
        mock_factory.get_transcript_store.return_value = mock_transcript_store

        executor = create_workflow_executor()

        mock_factory.get_run_state_store.assert_called_once()
        mock_factory.get_transcript_store.assert_called_once()
        assert executor is not None
        assert executor._store is mock_run_store
        assert executor._transcript_store is mock_transcript_store

    def test_passes_spawner_to_executor(self, mocker: MockerFixture) -> None:
        """Spawner is forwarded to the LocalExecutor."""
        mock_factory = mocker.patch("workflow.executor_factory.WorkflowStorageFactory")
        mock_factory.get_run_state_store.return_value = mocker.MagicMock()
        mock_factory.get_transcript_store.return_value = mocker.MagicMock()

        mock_spawner = mocker.MagicMock()
        executor = create_workflow_executor(spawner=mock_spawner)

        assert executor._spawner is mock_spawner

    def test_no_spawner_by_default(self, mocker: MockerFixture) -> None:
        """Without a spawner, the executor has no spawner."""
        mock_factory = mocker.patch("workflow.executor_factory.WorkflowStorageFactory")
        mock_factory.get_run_state_store.return_value = mocker.MagicMock()
        mock_factory.get_transcript_store.return_value = mocker.MagicMock()

        executor = create_workflow_executor()

        assert executor._spawner is None
