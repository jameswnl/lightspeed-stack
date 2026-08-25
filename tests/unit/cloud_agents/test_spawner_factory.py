"""Unit tests for the lightspeed-stack spawner factory.

cloud_agents.spawner.factory (cloud-agents#182) is injected into sys.modules
rather than imported for real, so these tests don't depend on that module's
actual availability/interface in the editable cloud-agents checkout.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Generator
from typing import Any

import pytest
from pytest_mock import MockerFixture

from models.config import SpawnerConfiguration
from workflow.spawner_factory import build_spawner, reset_spawner


@pytest.fixture(autouse=True)
def reset_singleton() -> Generator[None, None, None]:
    """Reset the module-level spawner singleton before and after each test."""
    reset_spawner()
    yield
    reset_spawner()


def _patch_cloud_agents_build_spawner(mocker: MockerFixture) -> Any:
    """Inject a fake cloud_agents.spawner.factory module and return its mock."""
    mock = mocker.MagicMock()
    fake_module = types.ModuleType("cloud_agents.spawner.factory")
    fake_module.build_spawner = mock  # type: ignore[attr-defined]
    mocker.patch.dict(sys.modules, {"cloud_agents.spawner.factory": fake_module})
    return mock


def test_build_spawner_kubernetes(mocker: MockerFixture) -> None:
    """kubernetes type forwards namespace/service_account/max_pods."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(
        type="kubernetes", namespace="agents", service_account="agent-sa"
    )

    result = build_spawner(config)

    mock.assert_called_once_with(
        "kubernetes",
        max_pods=10,
        namespace="agents",
        service_account="agent-sa",
    )
    assert result is mock.return_value


def test_build_spawner_kubernetes_omits_unset_optional_fields(
    mocker: MockerFixture,
) -> None:
    """kubernetes type omits namespace/service_account when unset, not None."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(type="kubernetes")

    build_spawner(config)

    mock.assert_called_once_with("kubernetes", max_pods=10)


def test_build_spawner_podman(mocker: MockerFixture) -> None:
    """podman type forwards only max_pods (no namespace/service_account)."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(type="podman", max_pods=5)

    build_spawner(config)

    mock.assert_called_once_with("podman", max_pods=5)


def test_build_spawner_openshell_full_config(mocker: MockerFixture) -> None:
    """openshell type forwards gateway/driver/workspace/TLS/bearer token."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
        openshell_driver="podman",
        openshell_workspace="lcore",
        openshell_bearer_token="secret-token",
    )

    build_spawner(config)

    mock.assert_called_once_with(
        "openshell",
        gateway_url="localhost:9080",
        driver="podman",
        workspace="lcore",
        tls_ca="",
        tls_cert="",
        tls_key="",
        bearer_token="secret-token",
        max_pods=10,
    )


def test_build_spawner_openshell_defaults(mocker: MockerFixture) -> None:
    """openshell type with no TLS/bearer config still calls through."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(
        type="openshell", openshell_gateway_url="localhost:9080"
    )

    build_spawner(config)

    mock.assert_called_once_with(
        "openshell",
        gateway_url="localhost:9080",
        driver="kubernetes",
        workspace="default",
        tls_ca="",
        tls_cert="",
        tls_key="",
        bearer_token="",
        max_pods=10,
    )


def test_build_spawner_caches_singleton(mocker: MockerFixture) -> None:
    """Second call returns the cached spawner without rebuilding."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(type="podman")

    first = build_spawner(config)
    second = build_spawner(config)

    mock.assert_called_once()
    assert first is second


def test_reset_spawner_forces_rebuild(mocker: MockerFixture) -> None:
    """reset_spawner() clears the cache so the next call rebuilds."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(type="podman")

    build_spawner(config)
    reset_spawner()
    build_spawner(config)

    assert mock.call_count == 2
