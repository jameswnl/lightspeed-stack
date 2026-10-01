"""Unit tests for workflow provider resolution against the catalog (issue #51)."""

# pylint: disable=too-few-public-methods

import copy
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from models.config import WorkflowEngineConfiguration
from workflow.catalog import (
    LEGACY_DEPRECATION,
    LEGACY_SUNSET,
    ResolvedProvider,
    normalize_definition,
    resolve_run_provider,
)

INFERENCE = SimpleNamespace(default_provider="openai", default_model="gpt-4o-mini")


def _governed() -> WorkflowEngineConfiguration:
    """Catalog with an env-bound pinned entry, an open entry and a keyless one."""
    return WorkflowEngineConfiguration.model_validate(
        {
            "providers": [
                {
                    "name": "claude-prod",
                    "executor_type": "anthropic",
                    "credential": {"name": "inference/anthropic-prod"},
                    "allowed_models": ["claude-sonnet-4-5", "claude-haiku-4-5"],
                },
                {
                    "name": "openai-team-b",
                    "executor_type": "openai",
                    "credential": {"name": "inference/openai-team-b"},
                },
                {"name": "local-bedrock", "executor_type": "bedrock"},
            ],
            "default_provider": "claude-prod",
            "default_model": "claude-sonnet-4-5",
            "secrets": [
                {
                    "name": "inference/anthropic-prod",
                    "backend": "env",
                    "env": "ANTHROPIC_API_KEY",
                },
                {
                    "name": "inference/openai-team-b",
                    "backend": "env",
                    "env": "OPENAI_TEAM_B_KEY",
                },
            ],
        }
    )


LEGACY = WorkflowEngineConfiguration()


def _status(call: Any) -> Optional[int]:
    """Return the HTTP status a call raises, or None when it returns."""
    try:
        call()
    except HTTPException as exc:
        return exc.status_code
    return None


class TestResolveGoverned:
    """Governed mode: the catalog decides provider, model and credential."""

    def test_defaults_when_no_provider_sent(self) -> None:
        """No provider uses workflow_engine.default_*, ignoring inference.default_*."""
        resolved = resolve_run_provider(_governed(), INFERENCE, None)
        assert resolved.catalog_name == "claude-prod"
        assert resolved.provider == {
            "name": "anthropic",
            "model": "claude-sonnet-4-5",
            "credentials_secret": "ANTHROPIC_API_KEY",
        }

    def test_provider_dict_uses_executor_type_and_operator_env(self) -> None:
        """The dict handed to cloud-agents is rebuilt from the catalog entry."""
        resolved = resolve_run_provider(
            _governed(), INFERENCE, {"name": "openai-team-b", "model": "gpt-4o"}
        )
        assert resolved.provider == {
            "name": "openai",
            "model": "gpt-4o",
            "credentials_secret": "OPENAI_TEAM_B_KEY",
        }
        assert resolved.credential_ref == "inference/openai-team-b"

    def test_entry_without_credential_gets_no_credentials_secret(self) -> None:
        """An entry with no credential never gets a guessed one."""
        resolved = resolve_run_provider(
            _governed(), INFERENCE, {"name": "local-bedrock", "model": "m"}
        )
        assert "credentials_secret" not in resolved.provider
        assert resolved.credential_ref is None

    def test_model_defaults_for_the_default_provider(self) -> None:
        """Naming only the default provider reuses default_model."""
        resolved = resolve_run_provider(_governed(), INFERENCE, {"name": "claude-prod"})
        assert resolved.model == "claude-sonnet-4-5"

    def test_model_required_for_a_non_default_provider(self) -> None:
        """default_model belongs to the default provider only, so others need a model."""
        assert (
            _status(
                lambda: resolve_run_provider(
                    _governed(), INFERENCE, {"name": "openai-team-b"}
                )
            )
            == 400
        )

    def test_unknown_provider_400(self) -> None:
        """A name outside the catalog is 400."""
        assert (
            _status(
                lambda: resolve_run_provider(
                    _governed(), INFERENCE, {"name": "gpt-everything", "model": "m"}
                )
            )
            == 400
        )

    def test_model_outside_allowed_models_400(self) -> None:
        """A model outside allowed_models is 400."""
        assert (
            _status(
                lambda: resolve_run_provider(
                    _governed(), INFERENCE, {"name": "claude-prod", "model": "opus"}
                )
            )
            == 400
        )

    def test_credential_ref_must_match_the_entry(self) -> None:
        """credential_ref equal to the entry's credential is fine; any other is 400."""
        ok = {
            "name": "claude-prod",
            "model": "claude-haiku-4-5",
            "credential_ref": {"name": "inference/anthropic-prod"},
        }
        assert (
            resolve_run_provider(_governed(), INFERENCE, ok).model == "claude-haiku-4-5"
        )
        bad = {**ok, "credential_ref": {"name": "inference/openai-team-b"}}
        assert _status(lambda: resolve_run_provider(_governed(), INFERENCE, bad)) == 400

    def test_credential_ref_on_keyless_entry_400(self) -> None:
        """An entry with no credential cannot be given one by the caller."""
        body = {
            "name": "local-bedrock",
            "model": "m",
            "credential_ref": {"name": "inference/anthropic-prod"},
        }
        assert (
            _status(lambda: resolve_run_provider(_governed(), INFERENCE, body)) == 400
        )

    @pytest.mark.parametrize("value", ["DATABASE_URL", "OPENAI_API_KEY", None, ""])
    def test_physical_credentials_secret_400(self, value: Any) -> None:
        """Env var names (or null/empty) in credentials_secret are 400."""
        body = {
            "name": "claude-prod",
            "model": "claude-haiku-4-5",
            "credentials_secret": value,
        }
        assert (
            _status(lambda: resolve_run_provider(_governed(), INFERENCE, body)) == 400
        )

    def test_legacy_credentials_secret_matching_logical_name_accepted(self) -> None:
        """The legacy field is accepted only as the entry's logical credential name."""
        body = {
            "name": "claude-prod",
            "model": "claude-haiku-4-5",
            "credentials_secret": "inference/anthropic-prod",  # gitleaks:allow (logical name, not a secret)
        }
        resolved = resolve_run_provider(_governed(), INFERENCE, body)
        assert resolved.legacy_credentials_secret is True
        assert resolved.provider["credentials_secret"] == "ANTHROPIC_API_KEY"

    @pytest.mark.parametrize("model", [3, ["a"], {"a": 1}, "", " "])
    def test_model_must_be_a_non_empty_string(self, model: Any) -> None:
        """A non-string or blank model is 400, whatever allowed_models says."""
        body = {"name": "openai-team-b", "model": model}
        assert (
            _status(lambda: resolve_run_provider(_governed(), INFERENCE, body)) == 400
        )

    def test_unknown_provider_keys_400(self) -> None:
        """Extra keys such as base_url are 400."""
        body = {"name": "claude-prod", "model": "claude-haiku-4-5", "base_url": "x"}
        assert (
            _status(lambda: resolve_run_provider(_governed(), INFERENCE, body)) == 400
        )

    def test_input_not_mutated(self) -> None:
        """The caller's dict is left alone."""
        body = {"name": "openai-team-b", "model": "gpt-4o"}
        before = copy.deepcopy(body)
        resolve_run_provider(_governed(), INFERENCE, body)
        assert body == before


class TestResolveLegacyMode:
    """Empty catalog: behaviour is unchanged apart from the Phase 0a hardening."""

    def test_uses_inference_defaults_and_builtin_credential_table(self) -> None:
        """inference.default_* apply and the built-in env table picks the key."""
        resolved = resolve_run_provider(LEGACY, INFERENCE, None)
        assert resolved.provider == {
            "name": "openai",
            "model": "gpt-4o-mini",
            "credentials_secret": "OPENAI_API_KEY",
        }

    def test_unknown_provider_gets_no_guessed_credential(self) -> None:
        """A provider outside the built-in table gets no credentials_secret."""
        resolved = resolve_run_provider(
            LEGACY, INFERENCE, {"name": "bedrock", "model": "m"}
        )
        assert "credentials_secret" not in resolved.provider

    def test_credential_ref_and_credentials_secret_400(self) -> None:
        """There are no registry names in legacy mode, so both are rejected."""
        for extra in (
            {"credential_ref": {"name": "inference/x"}},
            {"credentials_secret": "OPENAI_API_KEY"},
        ):
            body = {"name": "openai", "model": "gpt-4o", **extra}
            assert (
                _status(lambda b=body: resolve_run_provider(LEGACY, INFERENCE, b))
                == 400
            )


def _resolved(name: str = "claude-prod") -> ResolvedProvider:
    """Resolve a governed entry for the normalization tests."""
    model = "claude-sonnet-4-5" if name == "claude-prod" else "gpt-4o"
    return resolve_run_provider(_governed(), INFERENCE, {"name": name, "model": model})


def _definition(**top: Any) -> dict[str, Any]:
    """Two-step definition with an optional workflow provider."""
    steps = top.pop("steps", [{"name": "a", "type": "agent", "output_key": "x"}])
    return {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "t"},
        "spec": {"steps": steps},
        **top,
    }


class TestNormalizeDefinition:
    """Overrides resolve through the catalog and must stay on the run's entry."""

    def test_definition_provider_rewritten_to_executor_type(self) -> None:
        """definition.provider's logical name becomes the executor type."""
        definition = _definition(
            provider={"name": "claude-prod", "model": "claude-haiku-4-5"}
        )
        out = normalize_definition(_governed(), definition, _resolved())
        assert out["provider"] == {"name": "anthropic", "model": "claude-haiku-4-5"}

    def test_step_inference_provider_rewritten(self) -> None:
        """A step's inference_provider is rewritten the same way."""
        steps = [
            {
                "name": "a",
                "type": "agent",
                "output_key": "x",
                "inference_provider": {"name": "openai-team-b", "model": "gpt-4o-mini"},
            }
        ]
        out = normalize_definition(
            _governed(), _definition(steps=steps), _resolved("openai-team-b")
        )
        assert out["spec"]["steps"][0]["inference_provider"] == {
            "name": "openai",
            "model": "gpt-4o-mini",
        }

    def test_override_on_another_entry_400(self) -> None:
        """Same-entry rule: an override on a different catalog entry is 400."""
        definition = _definition(provider={"name": "openai-team-b", "model": "gpt-4o"})
        assert (
            _status(lambda: normalize_definition(_governed(), definition, _resolved()))
            == 400
        )

    def test_same_executor_type_different_entry_400(self) -> None:
        """Two entries sharing an executor type are still different entries."""
        engine = WorkflowEngineConfiguration.model_validate(
            {
                "providers": [
                    {"name": "a", "executor_type": "openai"},
                    {"name": "b", "executor_type": "openai"},
                ],
                "default_provider": "a",
                "default_model": "m",
            }
        )
        resolved = resolve_run_provider(engine, INFERENCE, {"name": "a", "model": "m"})
        definition = _definition(provider={"name": "b", "model": "m"})
        assert (
            _status(lambda: normalize_definition(engine, definition, resolved)) == 400
        )

    def test_unknown_override_provider_400(self) -> None:
        """An override naming a provider outside the catalog is 400."""
        definition = _definition(provider={"name": "nope", "model": "m"})
        assert (
            _status(lambda: normalize_definition(_governed(), definition, _resolved()))
            == 400
        )

    def test_override_model_must_be_allowed(self) -> None:
        """An override model outside allowed_models is 400."""
        definition = _definition(provider={"name": "claude-prod", "model": "opus"})
        assert (
            _status(lambda: normalize_definition(_governed(), definition, _resolved()))
            == 400
        )

    @pytest.mark.parametrize(
        "override", [{"model": "gpt-evil"}, {"name": "", "model": "m"}, "gpt-evil"]
    )
    def test_unnamed_or_malformed_override_400(self, override: Any) -> None:
        """An override that names no catalog entry cannot dodge allowed_models."""
        for definition in (
            _definition(provider=override),
            _definition(
                steps=[
                    {
                        "name": "a",
                        "type": "agent",
                        "output_key": "x",
                        "inference_provider": override,
                    }
                ]
            ),
        ):
            assert (
                _status(
                    lambda d=definition: normalize_definition(
                        _governed(), d, _resolved()
                    )
                )
                == 400
            )

    def test_input_not_mutated(self) -> None:
        """The caller's definition is never modified; a copy is returned."""
        definition = _definition(
            provider={"name": "claude-prod", "model": "claude-haiku-4-5"}
        )
        before = copy.deepcopy(definition)
        normalize_definition(_governed(), definition, _resolved())
        assert definition == before

    def test_legacy_mode_is_a_no_op(self) -> None:
        """Without a catalog the definition is returned unchanged."""
        resolved = resolve_run_provider(LEGACY, INFERENCE, None)
        definition = _definition(provider={"name": "openai", "model": "gpt-4o"})
        assert normalize_definition(LEGACY, definition, resolved) == definition


def test_deprecation_headers_are_rfc_shaped() -> None:
    """Deprecation is an RFC 9745 @timestamp; Sunset is an HTTP date (RFC 8594)."""
    assert LEGACY_DEPRECATION.startswith("@") and LEGACY_DEPRECATION[1:].isdigit()
    assert LEGACY_SUNSET.endswith("GMT")
