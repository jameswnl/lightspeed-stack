"""Build cloud-agents AgentSpawner instances from lightspeed-stack config.

Thin adapter: translates lightspeed-stack's own Pydantic SpawnerConfiguration
into the plain-kwargs call cloud_agents.spawner.factory.build_spawner()
expects, mirroring the WorkflowStorageFactory pattern in workflow/storage.py.

The built spawner is cached for the process lifetime (see build_spawner()
below) -- a config reload will not rebuild it or pick up a new gateway URL
without an explicit reset_spawner() call.
"""

from __future__ import annotations

from typing import Any, Optional

from log import get_logger
from models.config import SpawnerConfiguration

logger = get_logger(__name__)

_spawner: Optional[Any] = None  # pylint: disable=invalid-name


def build_spawner(spawner_config: SpawnerConfiguration) -> Any:
    """Build (and cache) a cloud-agents spawner instance from config.

    Parameters:
        spawner_config: Resolved SpawnerConfiguration.

    Returns:
        A cloud_agents AgentSpawner instance.
    """
    global _spawner  # pylint: disable=global-statement
    if _spawner is not None:
        return _spawner

    from cloud_agents.spawner.factory import (  # pylint: disable=import-outside-toplevel
        build_spawner as _cloud_agents_build_spawner,
    )

    if spawner_config.type == "openshell":
        _spawner = _cloud_agents_build_spawner(
            "openshell",
            gateway_url=spawner_config.openshell_gateway_url,
            driver=spawner_config.openshell_driver,
            workspace=spawner_config.openshell_workspace,
            http_endpoint=spawner_config.openshell_http_endpoint or "",
            tls_ca=(
                str(spawner_config.openshell_tls_ca)
                if spawner_config.openshell_tls_ca
                else ""
            ),
            tls_cert=(
                str(spawner_config.openshell_tls_cert)
                if spawner_config.openshell_tls_cert
                else ""
            ),
            tls_key=(
                str(spawner_config.openshell_tls_key)
                if spawner_config.openshell_tls_key
                else ""
            ),
            bearer_token=(
                spawner_config.openshell_bearer_token.get_secret_value()
                if spawner_config.openshell_bearer_token
                else ""
            ),
            max_pods=spawner_config.max_pods,
        )
    elif spawner_config.type == "kubernetes":
        kwargs: dict[str, Any] = {"max_pods": spawner_config.max_pods}
        if spawner_config.namespace:
            kwargs["namespace"] = spawner_config.namespace
        if spawner_config.service_account:
            kwargs["service_account"] = spawner_config.service_account
        _spawner = _cloud_agents_build_spawner("kubernetes", **kwargs)
    else:
        _spawner = _cloud_agents_build_spawner(
            spawner_config.type, max_pods=spawner_config.max_pods
        )

    logger.info("Built %s spawner", spawner_config.type)
    return _spawner


def reset_spawner() -> None:
    """Reset the spawner singleton (for testing)."""
    global _spawner  # pylint: disable=global-statement
    _spawner = None
