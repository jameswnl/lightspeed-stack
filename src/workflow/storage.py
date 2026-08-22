"""Workflow storage factory for RunStateStore and TranscriptStore.

Creates cloud-agents storage backends from lightspeed-stack's
database configuration. Uses raw asyncpg (cloud-agents' native
storage layer) — not SQLAlchemy.
"""

from typing import Optional
from urllib.parse import quote_plus

from cloud_agents.storage.run_state_store import RunStateStore
from cloud_agents.storage.transcript_store import TranscriptStore

from log import get_logger
from models.config import PostgreSQLDatabaseConfiguration, WorkflowEngineConfiguration

logger = get_logger(__name__)


class WorkflowStorageFactory:
    """Factory for creating workflow storage backends.

    Attributes:
        _run_state_store: Singleton RunStateStore instance.
        _transcript_store: Singleton TranscriptStore instance.
    """

    _run_state_store: Optional[RunStateStore] = None
    _transcript_store: Optional[TranscriptStore] = None

    @classmethod
    def build_db_url(cls, pg_config: PostgreSQLDatabaseConfiguration) -> str:
        """Build a PostgreSQL connection URL from config.

        Parameters:
            pg_config: PostgreSQL database configuration.

        Returns:
            Connection URL string for asyncpg.
        """
        password = quote_plus(pg_config.password.get_secret_value())
        return (
            f"postgresql://{pg_config.user}:{password}"
            f"@{pg_config.host}:{pg_config.port}/{pg_config.db}"
        )

    @classmethod
    async def initialize(
        cls,
        pg_config: PostgreSQLDatabaseConfiguration,
        wf_config: WorkflowEngineConfiguration,
    ) -> None:
        """Initialize workflow storage stores and connect to PostgreSQL.

        Parameters:
            pg_config: PostgreSQL database configuration.
            wf_config: Workflow engine configuration.
        """
        db_url = cls.build_db_url(pg_config)

        cls._run_state_store = RunStateStore(db_url=db_url)
        await cls._run_state_store.connect()
        logger.info("WorkflowStorageFactory: RunStateStore connected")

        cls._transcript_store = TranscriptStore(
            db_url=db_url,
            retention_days=wf_config.transcript_retention_days,
        )
        await cls._transcript_store.connect()
        logger.info("WorkflowStorageFactory: TranscriptStore connected")

    @classmethod
    def get_run_state_store(cls) -> RunStateStore:
        """Return the RunStateStore instance.

        Returns:
            The connected RunStateStore.

        Raises:
            RuntimeError: If initialize() has not been called.
        """
        if cls._run_state_store is None:
            raise RuntimeError(
                "WorkflowStorageFactory not initialized — call initialize() first"
            )
        return cls._run_state_store

    @classmethod
    def get_transcript_store(cls) -> TranscriptStore:
        """Return the TranscriptStore instance.

        Returns:
            The connected TranscriptStore.

        Raises:
            RuntimeError: If initialize() has not been called.
        """
        if cls._transcript_store is None:
            raise RuntimeError(
                "WorkflowStorageFactory not initialized — call initialize() first"
            )
        return cls._transcript_store

    @classmethod
    async def cleanup(cls) -> None:
        """Close database connections and reset state."""
        if cls._run_state_store is not None:
            await cls._run_state_store.close()
            logger.info("WorkflowStorageFactory: RunStateStore closed")
        if cls._transcript_store is not None:
            await cls._transcript_store.close()
            logger.info("WorkflowStorageFactory: TranscriptStore closed")
        cls._run_state_store = None
        cls._transcript_store = None

    @classmethod
    def reset(cls) -> None:
        """Reset factory state (for testing purposes)."""
        cls._run_state_store = None
        cls._transcript_store = None
