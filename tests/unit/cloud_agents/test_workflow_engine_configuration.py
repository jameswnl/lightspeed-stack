"""Unit tests for workflow engine and spawner configuration models."""

# pylint: disable=no-member

from typing import Any

import pytest
from pydantic import ValidationError

from models.config import (
    CompactionConfiguration,
    Configuration,
    DatabaseConfiguration,
    LlamaStackConfiguration,
    PostgreSQLDatabaseConfiguration,
    ServiceConfiguration,
    SpawnerConfiguration,
    UserDataCollection,
    WorkflowEngineConfiguration,
)


def test_workflow_engine_defaults() -> None:
    """WorkflowEngineConfiguration uses sensible defaults."""
    config = WorkflowEngineConfiguration()
    assert config.enabled is False
    assert config.max_concurrent_workflows == 10
    assert config.transcript_retention_days == 30


def test_workflow_engine_custom_values() -> None:
    """WorkflowEngineConfiguration accepts custom values."""
    config = WorkflowEngineConfiguration(
        enabled=True,
        max_concurrent_workflows=5,
        transcript_retention_days=90,
    )
    assert config.enabled is True
    assert config.max_concurrent_workflows == 5
    assert config.transcript_retention_days == 90


def test_workflow_engine_rejects_non_positive_workflows() -> None:
    """max_concurrent_workflows rejects zero and negative values."""
    with pytest.raises(ValidationError):
        WorkflowEngineConfiguration(max_concurrent_workflows=0)

    with pytest.raises(ValidationError):
        WorkflowEngineConfiguration(max_concurrent_workflows=-1)


def test_workflow_engine_rejects_non_positive_retention() -> None:
    """transcript_retention_days rejects zero and negative values."""
    with pytest.raises(ValidationError):
        WorkflowEngineConfiguration(transcript_retention_days=0)


def test_workflow_engine_rejects_unknown_field() -> None:
    """Unknown fields are forbidden."""
    with pytest.raises(ValidationError):
        WorkflowEngineConfiguration(unknown_field=True)  # type: ignore[call-arg]


def test_spawner_configuration_kubernetes() -> None:
    """SpawnerConfiguration accepts kubernetes type."""
    config = SpawnerConfiguration(
        type="kubernetes",
        namespace="agents",
        service_account="agent-sa",
    )
    assert config.type == "kubernetes"
    assert config.sandbox_image == "lightspeed-agentic-sandbox:latest"
    assert config.max_pods == 10
    assert config.namespace == "agents"
    assert config.service_account == "agent-sa"


def test_spawner_configuration_podman() -> None:
    """SpawnerConfiguration accepts podman type."""
    config = SpawnerConfiguration(type="podman")
    assert config.type == "podman"
    assert config.namespace is None


def test_spawner_configuration_custom_image() -> None:
    """SpawnerConfiguration accepts custom sandbox image."""
    config = SpawnerConfiguration(
        type="kubernetes",
        sandbox_image="my-sandbox:v2",
        max_pods=20,
    )
    assert config.sandbox_image == "my-sandbox:v2"
    assert config.max_pods == 20


def test_spawner_configuration_rejects_unknown_type() -> None:
    """SpawnerConfiguration rejects invalid type values."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(type="docker")  # type: ignore[arg-type]


def test_spawner_configuration_rejects_non_positive_max_pods() -> None:
    """max_pods rejects zero and negative values."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(type="kubernetes", max_pods=0)


def test_spawner_configuration_rejects_unknown_field() -> None:
    """Unknown fields are forbidden."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(type="kubernetes", unknown=True)  # type: ignore[call-arg]


def _make_config(**overrides: Any) -> Configuration:
    """Create a minimal Configuration with optional overrides."""
    defaults = {
        "name": "test",
        "service": ServiceConfiguration(),
        "llama_stack": LlamaStackConfiguration(
            use_as_library_client=True,
            library_client_config_path="tests/configuration/run.yaml",
        ),
        "user_data_collection": UserDataCollection(
            feedback_enabled=False, feedback_storage=None
        ),
        "compaction": CompactionConfiguration(),
    }
    defaults.update(overrides)
    return Configuration(**defaults)


def test_root_configuration_has_workflow_engine_field() -> None:
    """Configuration declares a workflow_engine field with defaults."""
    field_info = Configuration.model_fields.get("workflow_engine")
    assert field_info is not None
    assert field_info.annotation is WorkflowEngineConfiguration


def test_root_configuration_default_workflow_engine() -> None:
    """Configuration gets default workflow engine (disabled)."""
    cfg = _make_config()
    assert cfg.workflow_engine.enabled is False
    assert cfg.workflow_engine.max_concurrent_workflows == 10


def test_root_configuration_default_spawner_is_none() -> None:
    """Configuration gets None spawner by default."""
    cfg = _make_config()
    assert cfg.spawner is None


def test_root_configuration_workflow_engine_requires_postgres() -> None:
    """Enabling workflow engine without PostgreSQL raises ValidationError."""
    with pytest.raises(ValidationError, match="PostgreSQL"):
        _make_config(
            workflow_engine=WorkflowEngineConfiguration(enabled=True),
        )


def test_root_configuration_workflow_engine_with_postgres() -> None:
    """Enabling workflow engine with PostgreSQL is valid."""
    cfg = _make_config(
        workflow_engine=WorkflowEngineConfiguration(enabled=True),
        database=DatabaseConfiguration(
            postgres=PostgreSQLDatabaseConfiguration(
                host="localhost",
                port=5432,
                db="lightspeed",
                user="testuser",
                password="testpass",
            ),
        ),
    )
    assert cfg.workflow_engine.enabled is True
