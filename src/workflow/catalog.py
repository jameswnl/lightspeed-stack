"""Resolve workflow provider selections against the operator's catalog (issue #51).

Callers send logical names. This module maps them to the cloud-agents provider
dict the stack builds itself, so a caller never chooses an env var name, a K8s
Secret name or any other physical identifier.

Two modes, decided by whether ``workflow_engine.providers`` is empty:

* governed: the catalog is authoritative, unknown names deny (400), and
  ``workflow_engine.default_*`` are the defaults;
* legacy: today's behaviour (``inference.default_*`` and the built-in
  provider-to-env-key table), minus any caller-chosen credential.
"""

import copy
from dataclasses import dataclass
from typing import Any, Final, Optional

from fastapi import HTTPException, status

from models.config import WorkflowEngineConfiguration, WorkflowInferenceProvider
from workflow.provider_credentials import credentials_secret_for

# RFC 9745 Deprecation (a date, not "true") and RFC 8594 Sunset for the legacy
# ``credentials_secret`` field. Deprecated 2026-10-01 (stack#51 Phase 1);
# removal planned for 2027-04-01.
LEGACY_DEPRECATION: Final[str] = "@1790812800"
LEGACY_SUNSET: Final[str] = "Thu, 01 Apr 2027 00:00:00 GMT"

_ALLOWED_KEYS: Final[frozenset[str]] = frozenset(
    {"name", "model", "credential_ref", "credentials_secret"}
)


@dataclass(frozen=True)
class ResolvedProvider:
    """The outcome of resolving a run-level provider selection.

    Attributes:
        catalog_name: Logical catalog name (the provider name itself in legacy mode).
        executor_type: cloud-agents provider type the run uses.
        model: Resolved model identifier.
        credential_ref: Logical name of the entry's credential, if it has one.
        provider: Provider dict handed to cloud-agents, built by the stack.
        legacy_credentials_secret: True when the caller used the deprecated
            ``credentials_secret`` field (accepted only as a logical name).
    """

    catalog_name: str
    executor_type: str
    model: str
    credential_ref: Optional[str]
    provider: dict[str, str]
    legacy_credentials_secret: bool = False


def _bad_request(detail: str) -> HTTPException:
    """Build a 400 for a name the deployment does not have or a forbidden field."""
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _credential_ref_name(value: Any) -> Optional[str]:
    """Extract the logical name from a ``credential_ref`` value.

    Parameters:
        value: ``{"name": ...}`` as sent by the caller, or None.

    Returns:
        The name, or None when no ref was sent.

    Raises:
        HTTPException: 400 if the value is not an object with a string name.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"name", "key", "version"}:
        raise _bad_request("credential_ref must be {name: <logical secret name>}")
    name = value.get("name")
    if not isinstance(name, str):
        raise _bad_request("credential_ref.name must be a string")
    return name


def _lookup(
    engine: WorkflowEngineConfiguration, name: str, model: Optional[str]
) -> tuple[WorkflowInferenceProvider, str]:
    """Resolve a catalog name and model, enforcing allowed_models.

    Parameters:
        engine: Workflow engine configuration (governed mode).
        name: Logical provider name.
        model: Requested model, or None to use ``default_model``.

    Returns:
        The catalog entry and the model to use.

    Raises:
        HTTPException: 400 for an unknown name, a missing model, or a model
            outside the entry's allowed_models.
    """
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise _bad_request("model must be a non-empty string.")
    entry = engine.provider(name)
    if entry is None:
        raise _bad_request(f"Unknown provider '{name}'.")
    if model is None:
        if name != engine.default_provider:
            raise _bad_request(f"A model is required for provider '{name}'.")
        model = engine.default_model
    if model is None:
        raise _bad_request("No model given and no default_model configured.")
    if entry.allowed_models is not None and model not in entry.allowed_models:
        raise _bad_request(f"Model '{model}' is not allowed for provider '{name}'.")
    return entry, model


def _build_provider(
    engine: WorkflowEngineConfiguration, entry: WorkflowInferenceProvider, model: str
) -> dict[str, str]:
    """Build the provider dict for cloud-agents from operator config only."""
    provider = {"name": entry.executor_type, "model": model}
    if entry.credential is not None:
        binding = engine.binding(entry.credential.name)
        if binding is not None and binding.env is not None:
            provider["credentials_secret"] = binding.env
    return provider


def resolve_run_provider(
    engine: WorkflowEngineConfiguration,
    inference: Any,
    requested: Optional[dict[str, Any]],
) -> ResolvedProvider:
    """Resolve the run-level provider selection.

    Parameters:
        engine: Workflow engine configuration.
        inference: Inference configuration (``default_provider`` / ``default_model``
            used in legacy mode only).
        requested: The ``provider`` object from the request, or None.

    Returns:
        The resolved provider, with a dict built by the stack.

    Raises:
        HTTPException: 400 for unknown names or models, a credential the caller
            may not choose, or unknown keys.
    """
    requested = requested or {}
    unknown = sorted(set(requested) - _ALLOWED_KEYS)
    if unknown:
        raise _bad_request(f"Unknown provider keys: {unknown}.")
    ref = _credential_ref_name(requested.get("credential_ref"))

    if not engine.governed:
        return _resolve_legacy(inference, requested, ref)

    name = requested.get("name") or engine.default_provider
    if not name:
        raise _bad_request("No provider given and no default_provider configured.")
    entry, model = _lookup(engine, name, requested.get("model"))
    own_ref = entry.credential.name if entry.credential else None
    if ref is not None and ref != own_ref:
        raise _bad_request("credential_ref does not match the provider's credential.")

    legacy = False
    if "credentials_secret" in requested:
        # Deprecated field: accepted only as the entry's logical credential name.
        if own_ref is None or requested["credentials_secret"] != own_ref:
            raise _bad_request(
                "credentials_secret cannot be set by callers; use credential_ref "
                "with the provider's logical credential name."
            )
        legacy = True
    return ResolvedProvider(
        catalog_name=entry.name,
        executor_type=entry.executor_type,
        model=model,
        credential_ref=own_ref,
        provider=_build_provider(engine, entry, model),
        legacy_credentials_secret=legacy,
    )


def _resolve_legacy(
    inference: Any, requested: dict[str, Any], ref: Optional[str]
) -> ResolvedProvider:
    """Resolve without a catalog: today's behaviour minus caller credentials."""
    if ref is not None or "credentials_secret" in requested:
        raise _bad_request(
            "credential_ref and credentials_secret are not accepted: no provider "
            "catalog is configured, so the stack chooses the credential."
        )
    name = requested.get("name") or inference.default_provider or ""
    model = requested.get("model") or inference.default_model or ""
    provider = {"name": name, "model": model}
    cred = credentials_secret_for(name)
    if cred:
        provider["credentials_secret"] = cred
    return ResolvedProvider(
        catalog_name=name,
        executor_type=name,
        model=model,
        credential_ref=None,
        provider=provider,
    )


def normalize_definition(
    engine: WorkflowEngineConfiguration,
    definition: dict[str, Any],
    resolved: ResolvedProvider,
) -> dict[str, Any]:
    """Rewrite provider overrides in a definition to executor types.

    ``definition.provider`` and each step's ``inference_provider`` must resolve
    to the same catalog entry as the run provider (the model may differ):
    until cloud-agents#269 gives each step its own credential lease, the
    credential is picked by provider name, so another entry would silently run
    on the run entry's credential.

    Parameters:
        engine: Workflow engine configuration.
        definition: Caller's definition (never modified).
        resolved: The resolved run-level provider.

    Returns:
        A normalized copy; the definition unchanged in legacy mode.

    Raises:
        HTTPException: 400 for an unknown override provider, a model outside
            allowed_models, or an override on a different catalog entry.
    """
    if not engine.governed:
        return definition
    normalized = copy.deepcopy(definition)
    scopes = [(normalized, "provider")]
    steps = normalized.get("spec", {}).get("steps", [])
    scopes += [(s, "inference_provider") for s in steps if isinstance(s, dict)]
    for scope, key in scopes:
        override = scope.get(key)
        if override is None:
            continue
        if not isinstance(override, dict) or not isinstance(override.get("name"), str):
            # A model-only or string override would be merged onto the run
            # provider by the executor and dodge allowed_models, so it must
            # name a catalog entry.
            raise _bad_request(f"{key} must name a catalog provider.")
        entry, model = _lookup(engine, override["name"], override.get("model"))
        if entry.name != resolved.catalog_name:
            raise _bad_request(
                f"Provider override '{entry.name}' must use the same catalog entry "
                f"as the run provider '{resolved.catalog_name}'."
            )
        override["name"] = entry.executor_type
        override["model"] = model
    return normalized
