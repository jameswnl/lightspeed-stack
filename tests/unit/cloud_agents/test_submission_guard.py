"""Unit tests for the workflow submission hardening guard (issue #51, Phase 0a)."""

# pylint: disable=too-few-public-methods

from __future__ import annotations

import copy
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from workflow.limits import (
    MAX_DEFINITION_BYTES,
    MAX_MCP_SERVERS_PER_STEP,
    MAX_SECRET_HEADERS_PER_SERVER,
    MAX_WORKFLOW_STEPS,
)
from workflow.submission_guard import (
    enforce_submission_hardening,
    reject_oversized_definition,
)

DEFAULT_IMAGE = "spawner-default:v1"


def _step(name: str = "agent", **extra: Any) -> dict[str, Any]:
    """Build a minimal agent step."""
    return {
        "name": name,
        "type": "agent",
        "prompt": "Do it",
        "output_key": "result",
        **extra,
    }


def _definition(steps: Optional[list[dict[str, Any]]] = None, **top: Any) -> dict:
    """Build a minimal definition; ``top`` keys land at top level."""
    spec_extra = top.pop("spec", {})
    return {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "t"},
        "spec": {"steps": steps or [_step(spawn="ephemeral")], **spec_extra},
        **top,
    }


def _check(
    definition: dict[str, Any],
    *,
    provider: Optional[dict[str, Any]] = None,
    sandbox_image: Optional[str] = None,
    is_admin: bool = False,
    spawner_configured: bool = True,
) -> None:
    """Call the guard with test defaults."""
    enforce_submission_hardening(
        definition,
        provider or {"name": "openai", "model": "gpt-4o"},
        sandbox_image,
        is_admin=is_admin,
        default_sandbox_image=DEFAULT_IMAGE,
        spawner_configured=spawner_configured,
    )


def _status(definition: dict[str, Any], **kwargs: Any) -> Optional[int]:
    """Return the rejection status code, or None when accepted."""
    try:
        _check(definition, **kwargs)
    except HTTPException as exc:
        return exc.status_code
    return None


class TestCredentialRoutes:
    """G1/G2: callers cannot choose the credential env var."""

    @pytest.mark.parametrize("value", ["DATABASE_URL", None, ""])
    def test_run_provider_credentials_secret_rejected(self, value: Any) -> None:
        """Run-level credentials_secret is 400 whatever its value, even for admins."""
        provider = {"name": "openai", "model": "m", "credentials_secret": value}
        assert _status(_definition(), provider=provider) == 400
        assert _status(_definition(), provider=provider, is_admin=True) == 400

    def test_definition_provider_credentials_secret_rejected(self) -> None:
        """definition.provider.credentials_secret is 400."""
        definition = _definition(
            provider={"name": "bedrock", "model": "m", "credentials_secret": "X_KEY"}
        )
        assert _status(definition) == 400
        assert _status(definition, is_admin=True) == 400

    def test_definition_provider_without_credentials_accepted(self) -> None:
        """A definition provider with no credentials_secret passes."""
        definition = _definition(provider={"name": "openai", "model": "m"})
        assert _status(definition) is None


class TestSandboxImage:
    """G10: callers cannot choose the sandbox image."""

    def test_default_image_accepted(self) -> None:
        """Naming the spawner default image is fine."""
        assert _status(_definition(), sandbox_image=DEFAULT_IMAGE) is None

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d["spec"].update(spawn_config={"sandbox_image": "evil:1"}),
            lambda d: d["spec"]["steps"][0].update(
                spawn_config={"sandbox_image": "evil:1"}
            ),
            lambda d: d.update(skills={"image": "evil:1"}),
        ],
        ids=["workflow-spawn-config", "step-spawn-config", "skills-image"],
    )
    def test_custom_image_in_definition_forbidden(self, mutate: Any) -> None:
        """Non-default images in the definition are 403 for non-admins."""
        definition = _definition()
        mutate(definition)
        assert _status(definition) == 403
        assert _status(definition, is_admin=True) is None

    def test_request_sandbox_image_forbidden(self) -> None:
        """A non-default request-level sandbox_image is 403 without ADMIN."""
        assert _status(_definition(), sandbox_image="evil:1") == 403
        assert _status(_definition(), sandbox_image="evil:1", is_admin=True) is None


class TestAdvisory:
    """G11: advisory mode needs ADMIN."""

    def test_advisory_forbidden_without_admin(self) -> None:
        """advisory: true is 403 for non-admins, allowed for admins."""
        definition = _definition(advisory=True)
        assert _status(definition) == 403
        assert _status(definition, is_admin=True) is None


class TestSpawnMode:
    """Workflow spawn none/local needs ADMIN; ephemeral needs a spawner."""

    @pytest.mark.parametrize("mode", ["none", "local"])
    def test_step_spawn_forbidden_without_admin(self, mode: str) -> None:
        """Step-level none/local is 403 for non-admins."""
        definition = _definition([_step(spawn=mode)])
        assert _status(definition) == 403
        assert _status(definition, is_admin=True) is None

    def test_workflow_spawn_forbidden_without_admin(self) -> None:
        """Workflow-level none applies to steps that do not override it."""
        definition = _definition([_step()], spec={"spawn": "none"})
        assert _status(definition) == 403

    def test_step_override_of_workflow_spawn_uses_effective_mode(self) -> None:
        """A step overriding workflow spawn:none with ephemeral passes."""
        definition = _definition([_step(spawn="ephemeral")], spec={"spawn": "none"})
        assert _status(definition) is None

    def test_default_spawn_is_ephemeral_and_needs_spawner(self) -> None:
        """No spawn anywhere defaults to ephemeral, 403 without a spawner."""
        definition = _definition([_step()])
        assert _status(definition, spawner_configured=False) == 403
        assert _status(definition, spawner_configured=True) is None

    def test_ephemeral_without_spawner_forbidden_even_for_admin(self) -> None:
        """Admins cannot spawn ephemeral steps without a spawner."""
        definition = _definition([_step(spawn="ephemeral")])
        assert _status(definition, is_admin=True, spawner_configured=False) == 403

    def test_none_without_spawner_fine_for_admin(self) -> None:
        """spawn none never needs a spawner."""
        definition = _definition([_step(spawn="none")])
        assert _status(definition, is_admin=True, spawner_configured=False) is None


class TestLimits:
    """G9: byte and count caps."""

    def test_oversized_definition_413(self) -> None:
        """A definition over MAX_DEFINITION_BYTES is 413."""
        big = _definition([_step(prompt="x" * (MAX_DEFINITION_BYTES + 1))])
        with pytest.raises(HTTPException) as exc_info:
            reject_oversized_definition(big)
        assert exc_info.value.status_code == 413

    def test_small_definition_passes_size_check(self) -> None:
        """A normal definition passes the size check."""
        reject_oversized_definition(_definition())

    def test_too_many_steps_422(self) -> None:
        """More than MAX_WORKFLOW_STEPS steps is 422."""
        steps = [
            _step(f"s{i}", spawn="ephemeral") for i in range(MAX_WORKFLOW_STEPS + 1)
        ]
        assert _status(_definition(steps)) == 422

    def test_max_steps_accepted(self) -> None:
        """Exactly MAX_WORKFLOW_STEPS steps passes."""
        steps = [_step(f"s{i}", spawn="ephemeral") for i in range(MAX_WORKFLOW_STEPS)]
        assert _status(_definition(steps)) is None

    def test_too_many_mcp_servers_on_step_422(self) -> None:
        """More than MAX_MCP_SERVERS_PER_STEP servers on a step is 422."""
        names = [f"srv{i}" for i in range(MAX_MCP_SERVERS_PER_STEP + 1)]
        definition = _definition([_step(spawn="ephemeral", mcp_servers=names)])
        assert _status(definition) == 422

    def test_too_many_workflow_level_mcp_servers_422(self) -> None:
        """Workflow-level defaults are counted too."""
        names = [f"srv{i}" for i in range(MAX_MCP_SERVERS_PER_STEP + 1)]
        definition = _definition(spec={"mcp_servers": names})
        assert _status(definition) == 422

    def test_too_many_secret_headers_422(self) -> None:
        """An inline MCP entry with too many secret_headers is 422."""
        headers = {
            f"h{i}": {"secret_name": "s", "key": "k"}
            for i in range(MAX_SECRET_HEADERS_PER_SERVER + 1)
        }
        server = {
            "name": "x",
            "url": "https://m.example.com",
            "secret_headers": headers,
        }
        definition = _definition([_step(spawn="ephemeral", mcp_servers=[server])])
        assert _status(definition) == 422


def test_guard_does_not_mutate_inputs() -> None:
    """The guard is a pure check."""
    definition = _definition(advisory=False)
    before = copy.deepcopy(definition)
    _check(definition)
    assert definition == before


def test_approval_step_does_not_need_spawner() -> None:
    """A human-approval step has no spawn mode, so it never needs a spawner."""
    definition = _definition(
        [_step(spawn="none"), {"name": "gate", "type": "human-approval"}]
    )
    assert _status(definition, is_admin=True, spawner_configured=False) is None


def test_non_admin_none_spawn_forbidden_without_spawner() -> None:
    """Admin is the only bypass for spawn none; a missing spawner is not."""
    definition = _definition([_step(spawn="none")])
    assert _status(definition, spawner_configured=False) == 403
