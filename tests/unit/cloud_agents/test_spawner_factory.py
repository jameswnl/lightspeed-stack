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


def test_build_spawner_openshell_full_config(mocker: MockerFixture) -> None:
    """openshell type forwards gateway/workspace/TLS/bearer token, not driver.

    No `driver` kwarg is forwarded -- the compute driver the gateway itself
    uses is the gateway's own concern, not something lightspeed-stack
    configures or passes through.
    """
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
        openshell_workspace="lcore",
        openshell_http_endpoint="https://sandboxes.example.com",
        openshell_bearer_token="secret-token",
    )

    build_spawner(config)

    mock.assert_called_once_with(
        "openshell",
        gateway_url="localhost:9080",
        workspace="lcore",
        http_endpoint="https://sandboxes.example.com",
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
        type="openshell",
        openshell_gateway_url="localhost:9080",
    )

    build_spawner(config)

    mock.assert_called_once_with(
        "openshell",
        gateway_url="localhost:9080",
        workspace="default",
        http_endpoint="",
        tls_ca="",
        tls_cert="",
        tls_key="",
        bearer_token="",
        max_pods=10,
    )


def test_build_spawner_caches_singleton(mocker: MockerFixture) -> None:
    """Second call returns the cached spawner without rebuilding."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
    )

    first = build_spawner(config)
    second = build_spawner(config)

    mock.assert_called_once()
    assert first is second


def test_build_spawner_openshell_with_oidc_passes_provider(
    mocker: MockerFixture,
) -> None:
    """OIDC config builds a token provider and passes bearer_token_provider.

    No static bearer_token is forwarded in this case (kept empty, same as
    the no-auth default) -- the OIDC fields and openshell_bearer_token are
    mutually exclusive at the SpawnerConfiguration level already.
    """
    mock = _patch_cloud_agents_build_spawner(mocker)
    mock_provider_cls = mocker.patch(
        "workflow.spawner_factory.OidcClientCredentialsTokenProvider"
    )
    mock_provider = mock_provider_cls.return_value
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
        openshell_oidc_issuer="https://keycloak.example.com/realms/agents",
        openshell_oidc_client_id="my-client",
        openshell_oidc_client_secret="my-secret",
        openshell_oidc_audience="my-audience",
    )

    build_spawner(config)

    mock_provider_cls.assert_called_once_with(
        issuer="https://keycloak.example.com/realms/agents",
        client_id="my-client",
        client_secret="my-secret",
        audience="my-audience",
    )
    mock.assert_called_once_with(
        "openshell",
        gateway_url="localhost:9080",
        workspace="default",
        http_endpoint="",
        tls_ca="",
        tls_cert="",
        tls_key="",
        bearer_token="",
        max_pods=10,
        bearer_token_provider=mock_provider.get_token,
    )


def test_build_spawner_openshell_without_oidc_omits_provider_kwarg(
    mocker: MockerFixture,
) -> None:
    """Without OIDC config, bearer_token_provider is not passed at all.

    Regression test for backward compatibility: existing deployments using
    the static bearer_token (or no auth) must see the exact same call
    signature as before this feature existed.
    """
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
        openshell_bearer_token="secret-token",
    )

    build_spawner(config)

    _, kwargs = mock.call_args
    assert "bearer_token_provider" not in kwargs
    assert kwargs["bearer_token"] == "secret-token"


def test_reset_spawner_forces_rebuild(mocker: MockerFixture) -> None:
    """reset_spawner() clears the cache so the next call rebuilds."""
    mock = _patch_cloud_agents_build_spawner(mocker)
    config = SpawnerConfiguration(
        type="openshell",
        openshell_gateway_url="localhost:9080",
    )

    build_spawner(config)
    reset_spawner()
    build_spawner(config)

    assert mock.call_count == 2
