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


def test_spawner_configuration_rejects_kubernetes_type() -> None:
    """spawn:ephemeral uses OpenShellSpawner only -- kubernetes is no longer a valid type."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(type="kubernetes")  # type: ignore[arg-type]


def test_spawner_configuration_rejects_podman_type() -> None:
    """spawn:ephemeral uses OpenShellSpawner only -- podman is no longer a valid type."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(type="podman")  # type: ignore[arg-type]


def test_spawner_configuration_custom_image() -> None:
    """SpawnerConfiguration accepts custom sandbox image."""
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
        sandbox_image="my-sandbox:v2",
        max_pods=20,
    )
    assert config.sandbox_image == "my-sandbox:v2"
    assert config.max_pods == 20


def test_spawner_configuration_openshell_defaults() -> None:
    """SpawnerConfiguration fills in sensible defaults besides type/gateway."""
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
    )
    assert config.type == "openshell"
    assert config.openshell_gateway_url == "localhost:9080"
    assert config.openshell_workspace == "default"
    assert config.openshell_tls_ca is None
    assert config.openshell_tls_cert is None
    assert config.openshell_tls_key is None
    assert config.openshell_bearer_token is None


def test_spawner_configuration_openshell_custom_values() -> None:
    """SpawnerConfiguration accepts custom openshell fields."""
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
        openshell_workspace="lcore",
        openshell_bearer_token="secret-token",
    )
    assert config.openshell_gateway_url == "localhost:9080"
    assert config.openshell_workspace == "lcore"
    assert (
        config.openshell_bearer_token is not None
        and config.openshell_bearer_token.get_secret_value() == "secret-token"
    )


def test_spawner_configuration_openshell_requires_gateway_url() -> None:
    """openshell type without openshell_gateway_url is rejected."""
    with pytest.raises(ValidationError, match="openshell_gateway_url"):
        SpawnerConfiguration(type="openshell")


def test_spawner_configuration_rejects_driver_field() -> None:
    """openshell_driver is not a client-side concern -- no such field exists.

    OpenShellSpawner's whole lifecycle (create/exec/expose/query/destroy) is
    proxied through the gateway's own network address, so lightspeed-stack
    never needs to know which compute driver the gateway itself uses. Old
    YAML setting this is rejected outright by extra="forbid", not silently
    accepted and ignored.
    """
    with pytest.raises(ValidationError):
        SpawnerConfiguration(
            type="openshell",
            openshell_gateway_url="localhost:9080",
            openshell_driver="kubernetes",  # type: ignore[call-arg]
        )


def test_spawner_configuration_rejects_unknown_type() -> None:
    """SpawnerConfiguration rejects invalid type values."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(type="docker")  # type: ignore[arg-type]


def test_spawner_configuration_rejects_non_positive_max_pods() -> None:
    """max_pods rejects zero and negative values."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(
            type="openshell",
            openshell_gateway_url="localhost:9080",
            max_pods=0,
        )


def test_spawner_configuration_rejects_unknown_field() -> None:
    """Unknown fields are forbidden."""
    with pytest.raises(ValidationError):
        SpawnerConfiguration(
            type="openshell",
            openshell_gateway_url="localhost:9080",
            unknown=True,  # type: ignore[call-arg]
        )


def test_spawner_configuration_rejects_leftover_kubernetes_fields() -> None:
    """namespace/service_account (kubernetes-spawner-only) are gone.

    Old kubernetes-type YAML that still sets these is rejected outright by
    extra="forbid" rather than silently accepted and ignored.
    """
    with pytest.raises(ValidationError):
        SpawnerConfiguration(
            type="openshell",
            openshell_gateway_url="localhost:9080",
            namespace="agents",  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        SpawnerConfiguration(
            type="openshell",
            openshell_gateway_url="localhost:9080",
            service_account="agent-sa",  # type: ignore[call-arg]
        )


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
