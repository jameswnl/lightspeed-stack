"""Submission-time hardening for POST /v1/workflows/run (issue #51, Phase 0a).

Closes the holes where a caller could pick the credential env var, the
sandbox image, advisory mode or a non-sandboxed spawn mode, and bounds the
size of a definition. Pure checks: nothing here mutates its inputs.
"""

import json
from typing import Any, Optional

from fastapi import HTTPException, status

from log import get_logger
from workflow.limits import (
    MAX_DEFINITION_BYTES,
    MAX_MCP_SERVERS_PER_STEP,
    MAX_SECRET_HEADERS_PER_SERVER,
    MAX_WORKFLOW_STEPS,
)

logger = get_logger(__name__)

_PRIVILEGED_SPAWN_MODES = frozenset({"none", "local"})
_DEFAULT_SPAWN = "ephemeral"


def reject_oversized_definition(definition: dict[str, Any]) -> None:
    """Reject a definition whose JSON encoding exceeds the byte cap.

    Parameters:
        definition: Raw workflow definition from the request body.

    Raises:
        HTTPException: 413 when the definition is over MAX_DEFINITION_BYTES.
    """
    size = len(json.dumps(definition).encode("utf-8"))
    if size > MAX_DEFINITION_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Workflow definition exceeds {MAX_DEFINITION_BYTES} bytes.",
        )


def _as_dict(value: Any) -> dict[str, Any]:
    """Return value when it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _image_of(spawn_config: Any) -> Optional[str]:
    """Return the sandbox_image of a raw spawn_config, if any."""
    image = _as_dict(spawn_config).get("sandbox_image")
    return image if isinstance(image, str) else None


def _count_errors(steps: list[Any], spec: dict[str, Any]) -> list[str]:
    """Return count-cap violations for steps, MCP lists and secret headers."""
    errors: list[str] = []
    if len(steps) > MAX_WORKFLOW_STEPS:
        errors.append(f"spec.steps has {len(steps)} steps (max {MAX_WORKFLOW_STEPS})")
    scopes = [("spec", spec)] + [
        (f"step '{_as_dict(s).get('name', i)}'", _as_dict(s))
        for i, s in enumerate(steps)
    ]
    for label, scope in scopes:
        servers = scope.get("mcp_servers") or []
        if not isinstance(servers, list):
            continue
        if len(servers) > MAX_MCP_SERVERS_PER_STEP:
            errors.append(
                f"{label}: {len(servers)} mcp_servers (max {MAX_MCP_SERVERS_PER_STEP})"
            )
        for server in servers:
            headers = _as_dict(_as_dict(server).get("secret_headers"))
            if len(headers) > MAX_SECRET_HEADERS_PER_SERVER:
                errors.append(
                    f"{label}: mcp server has {len(headers)} secret_headers "
                    f"(max {MAX_SECRET_HEADERS_PER_SERVER})"
                )
    return errors


def _effective_spawn_modes(steps: list[Any], spec: dict[str, Any]) -> set[str]:
    """Return the effective spawn mode of each agent step (step, spec, default).

    Approval steps never spawn anything, so they are skipped.
    """
    workflow_spawn = spec.get("spawn")
    return {
        _as_dict(step).get("spawn") or workflow_spawn or _DEFAULT_SPAWN
        for step in steps
        if _as_dict(step).get("type", "agent") == "agent"
    }


def enforce_submission_hardening(  # pylint: disable=too-many-arguments
    definition: dict[str, Any],
    provider: dict[str, Any],
    sandbox_image: Optional[str],
    *,
    is_admin: bool,
    default_sandbox_image: str,
    spawner_configured: bool,
) -> None:
    """Apply the Phase 0a rules to a submission.

    Parameters:
        definition: Raw workflow definition from the request body.
        provider: Run-level provider as sent by the caller (before the
            stack injects its own credentials_secret).
        sandbox_image: Request-level sandbox image override, if any.
        is_admin: Whether the caller holds the ADMIN action.
        default_sandbox_image: The spawner's configured sandbox image.
        spawner_configured: Whether a spawner_configuration exists.

    Shape errors (422 from cloud-agents validation) take precedence over
    these policy errors because the handler validates first. Denials raise
    HTTPException directly and are logged; structured audit events land in
    Phase 4 of the #51 plan.

    Raises:
        HTTPException: 400 when the caller names a credential, 422 when a
            count cap is exceeded, 403 for privileged options used without
            ADMIN or for ephemeral steps without a spawner.
    """
    if "credentials_secret" in provider or "credentials_secret" in _as_dict(
        definition.get("provider")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="credentials_secret cannot be set by callers; the stack "
            "chooses the credential for the provider.",
        )

    spec = _as_dict(definition.get("spec"))
    raw_steps = spec.get("steps")
    steps: list[Any] = raw_steps if isinstance(raw_steps, list) else []

    count_errors = _count_errors(steps, spec)
    if count_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"validation_errors": count_errors},
        )

    spawn_modes = _effective_spawn_modes(steps, spec)
    if "ephemeral" in spawn_modes and not spawner_configured:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ephemeral steps require spawner_configuration.",
        )

    if is_admin:
        return

    denied: list[str] = []
    privileged = sorted(spawn_modes & _PRIVILEGED_SPAWN_MODES)
    if privileged:
        denied.append(f"spawn mode(s) {privileged} require admin")
    if definition.get("advisory"):
        denied.append("advisory mode requires admin")

    images = {
        sandbox_image,
        _image_of(spec.get("spawn_config")),
        _as_dict(definition.get("skills")).get("image"),
        *(_image_of(_as_dict(s).get("spawn_config")) for s in steps),
    }
    if any(i and i != default_sandbox_image for i in images):
        denied.append("a custom sandbox image requires admin")

    if denied:
        logger.info("Workflow submission denied: %s", denied)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"denied": denied},
        )
