"""Unit tests for the workflow storage factory."""

# pylint: disable=too-few-public-methods

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from pytest_mock import MockerFixture

from workflow.storage import WorkflowStorageFactory


@pytest.fixture(autouse=True)
def reset_factory() -> Generator[None, None, None]:
    """Reset factory state before each test."""
    WorkflowStorageFactory.reset()
    yield
    WorkflowStorageFactory.reset()


def _make_pg_config(mocker: MockerFixture) -> Any:
    """Create a mock PostgreSQL config."""
    pg = mocker.MagicMock()
    pg.host = "localhost"
    pg.port = 5432
    pg.db = "lightspeed"
    pg.user = "testuser"
    pg.password = mocker.MagicMock()
    pg.password.get_secret_value.return_value = "testpass"
    return pg


def _make_wf_config(mocker: MockerFixture) -> Any:
    """Create a mock workflow engine config."""
    wf = mocker.MagicMock()
    wf.transcript_retention_days = 30
    return wf


class TestBuildDbUrl:
    """Tests for _build_db_url."""

    def test_builds_correct_url(self, mocker: MockerFixture) -> None:
        """URL includes all connection parameters."""
        pg = _make_pg_config(mocker)
        url = WorkflowStorageFactory.build_db_url(pg)
        assert url == "postgresql://testuser:testpass@localhost:5432/lightspeed"

    def test_encodes_special_characters_in_password(
        self, mocker: MockerFixture
    ) -> None:
        """Special characters in password are URL-encoded."""
        pg = _make_pg_config(mocker)
        pg.password.get_secret_value.return_value = "p@ss w/rd"
        url = WorkflowStorageFactory.build_db_url(pg)
        assert "p%40ss+w%2Frd" in url


class TestInitialize:
    """Tests for initialize."""

    @pytest.mark.asyncio
    async def test_creates_and_connects_stores(self, mocker: MockerFixture) -> None:
        """Initialize creates both stores and calls connect."""
        mock_run_store = mocker.AsyncMock()
        mock_run_state_cls = mocker.patch(
            "workflow.storage.RunStateStore", return_value=mock_run_store
        )

        mock_transcript_store = mocker.AsyncMock()
        mock_transcript_cls = mocker.patch(
            "workflow.storage.TranscriptStore",
            return_value=mock_transcript_store,
        )

        pg = _make_pg_config(mocker)
        wf = _make_wf_config(mocker)

        await WorkflowStorageFactory.initialize(pg, wf)

        mock_run_state_cls.assert_called_once()
        mock_run_store.connect.assert_called_once()

        mock_transcript_cls.assert_called_once_with(
            db_url="postgresql://testuser:testpass@localhost:5432/lightspeed",
            retention_days=30,
        )
        mock_transcript_store.connect.assert_called_once()


class TestGetStores:
    """Tests for get_run_state_store and get_transcript_store."""

    def test_get_run_state_store_before_init_raises(self) -> None:
        """Accessing store before initialize raises RuntimeError."""
        with pytest.raises(RuntimeError, match="not initialized"):
            WorkflowStorageFactory.get_run_state_store()

    def test_get_transcript_store_before_init_raises(self) -> None:
        """Accessing store before initialize raises RuntimeError."""
        with pytest.raises(RuntimeError, match="not initialized"):
            WorkflowStorageFactory.get_transcript_store()

    @pytest.mark.asyncio
    async def test_get_stores_after_init(self, mocker: MockerFixture) -> None:
        """Stores are accessible after initialize."""
        mocker.patch(
            "workflow.storage.RunStateStore",
            return_value=mocker.AsyncMock(),
        )
        mocker.patch(
            "workflow.storage.TranscriptStore",
            return_value=mocker.AsyncMock(),
        )

        await WorkflowStorageFactory.initialize(
            _make_pg_config(mocker), _make_wf_config(mocker)
        )

        assert WorkflowStorageFactory.get_run_state_store() is not None
        assert WorkflowStorageFactory.get_transcript_store() is not None


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_stores(self, mocker: MockerFixture) -> None:
        """Cleanup calls close on both stores and resets state."""
        mock_run_store = mocker.AsyncMock()
        mocker.patch("workflow.storage.RunStateStore", return_value=mock_run_store)

        mock_transcript_store = mocker.AsyncMock()
        mocker.patch(
            "workflow.storage.TranscriptStore",
            return_value=mock_transcript_store,
        )

        await WorkflowStorageFactory.initialize(
            _make_pg_config(mocker), _make_wf_config(mocker)
        )
        await WorkflowStorageFactory.cleanup()

        mock_run_store.close.assert_called_once()
        mock_transcript_store.close.assert_called_once()

        with pytest.raises(RuntimeError):
            WorkflowStorageFactory.get_run_state_store()

    @pytest.mark.asyncio
    async def test_cleanup_when_not_initialized(self) -> None:
        """Cleanup is safe to call when not initialized."""
        await WorkflowStorageFactory.cleanup()
