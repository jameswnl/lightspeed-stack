"""Unit tests for the workflow executor factory."""

# pylint: disable=protected-access

from __future__ import annotations

from pytest_mock import MockerFixture

from workflow.executor_factory import create_workflow_runner


class TestCreateWorkflowExecutor:
    """Tests for create_workflow_runner."""

    def test_creates_local_executor(self, mocker: MockerFixture) -> None:
        """Creates a LocalWorkflowRunner wired to stores."""
        mock_run_store = mocker.MagicMock()
        mock_transcript_store = mocker.MagicMock()
        mock_factory = mocker.patch("workflow.executor_factory.WorkflowStorageFactory")
        mock_factory.get_run_state_store.return_value = mock_run_store
        mock_factory.get_transcript_store.return_value = mock_transcript_store

        executor = create_workflow_runner()

        mock_factory.get_run_state_store.assert_called_once()
        mock_factory.get_transcript_store.assert_called_once()
        assert executor is not None
        assert executor._store is mock_run_store
        assert executor._transcript_store is mock_transcript_store

    def test_passes_spawner_to_executor(self, mocker: MockerFixture) -> None:
        """Spawner is forwarded to the LocalWorkflowRunner."""
        mock_factory = mocker.patch("workflow.executor_factory.WorkflowStorageFactory")
        mock_factory.get_run_state_store.return_value = mocker.MagicMock()
        mock_factory.get_transcript_store.return_value = mocker.MagicMock()

        mock_spawner = mocker.MagicMock()
        executor = create_workflow_runner(spawner=mock_spawner)

        assert executor._spawner is mock_spawner

    def test_no_spawner_by_default(self, mocker: MockerFixture) -> None:
        """Without a spawner, the executor has no spawner."""
        mock_factory = mocker.patch("workflow.executor_factory.WorkflowStorageFactory")
        mock_factory.get_run_state_store.return_value = mocker.MagicMock()
        mock_factory.get_transcript_store.return_value = mocker.MagicMock()

        executor = create_workflow_runner()

        assert executor._spawner is None

    def test_initializes_tracing(self, mocker: MockerFixture) -> None:
        """Tracing is initialized so cloud-agents spans are exported.

        Without a global TracerProvider the executor's spans are dropped by a
        NoOp tracer even when OTEL_EXPORTER_OTLP_ENDPOINT is set, so the
        in-process path must call init_tracing itself.
        """
        mock_factory = mocker.patch("workflow.executor_factory.WorkflowStorageFactory")
        mock_factory.get_run_state_store.return_value = mocker.MagicMock()
        mock_factory.get_transcript_store.return_value = mocker.MagicMock()
        mock_init_tracing = mocker.patch("workflow.executor_factory.init_tracing")

        create_workflow_runner()

        mock_init_tracing.assert_called_once_with("workflow-runner")
