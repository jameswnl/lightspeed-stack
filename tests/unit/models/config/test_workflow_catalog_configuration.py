"""Unit tests for the workflow provider catalog and secret registry (issue #51)."""

# pylint: disable=too-few-public-methods

from typing import Any, Optional

import pytest
from pydantic import ValidationError

from models.common.secrets import SecretRef
from models.config import (
    InferenceConfiguration,
    SecretBinding,
    UnifiedInferenceProvider,
    WorkflowEngineConfiguration,
    WorkflowInferenceProvider,
)


def _engine(**overrides: Any) -> dict[str, Any]:
    """Valid governed-mode workflow_engine config; ``overrides`` replace keys."""
    config: dict[str, Any] = {
        "enabled": True,
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
    config.update(overrides)
    return config


def _error(config: dict[str, Any]) -> str:
    """Return the validation error text for an invalid config."""
    with pytest.raises(ValidationError) as exc_info:
        WorkflowEngineConfiguration.model_validate(config)
    return str(exc_info.value).lower()


class TestSecretRef:
    """SecretRef holds a logical name, never a physical identifier."""

    def test_valid_ref(self) -> None:
        """A logical name with a slash is accepted and the model is frozen."""
        ref = SecretRef(name="inference/anthropic-prod")
        assert ref.name == "inference/anthropic-prod"
        with pytest.raises(ValidationError):
            ref.name = "other"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "name", ["", "UPPER", "has space", "-lead", "a" * 129, "../etc/passwd"]
    )
    def test_invalid_names_rejected(self, name: str) -> None:
        """Names outside ^[a-z0-9][a-z0-9/_.-]{0,127}$ are rejected."""
        with pytest.raises(ValidationError):
            SecretRef(name=name)

    def test_extra_fields_rejected(self) -> None:
        """Unknown fields (e.g. an env var name) are rejected."""
        with pytest.raises(ValidationError):
            SecretRef(name="a/b", env="DATABASE_URL")  # type: ignore[call-arg]


class TestSecretBinding:
    """SecretBinding maps a logical ref to exactly one backend location."""

    def test_env_binding(self) -> None:
        """An env binding needs only ``env``."""
        binding = SecretBinding(name="a/b", backend="env", env="MY_KEY")
        assert binding.env == "MY_KEY"

    def test_k8s_binding_needs_secret_name_and_key(self) -> None:
        """A k8s binding needs secret_name and key."""
        SecretBinding(name="a/b", backend="k8s", secret_name="s", key="k")
        with pytest.raises(ValidationError):
            SecretBinding(name="a/b", backend="k8s", secret_name="s")

    def test_file_binding_needs_path(self) -> None:
        """A file binding needs path."""
        SecretBinding(name="a/b", backend="file", path="/run/secrets/x")
        with pytest.raises(ValidationError):
            SecretBinding(name="a/b", backend="file")

    def test_fields_of_other_backends_rejected(self) -> None:
        """An env binding may not also carry k8s or file fields."""
        with pytest.raises(ValidationError):
            SecretBinding(name="a/b", backend="env", env="K", secret_name="s")
        with pytest.raises(ValidationError):
            SecretBinding(name="a/b", backend="env", env="K", path="/x")

    def test_env_binding_requires_env(self) -> None:
        """An env binding without ``env`` is rejected."""
        with pytest.raises(ValidationError):
            SecretBinding(name="a/b", backend="env")  # type: ignore[call-arg]


class TestWorkflowInferenceProvider:
    """A catalog entry holds one credential and an optional model allow-list."""

    def test_defaults(self) -> None:
        """Optional fields default to None."""
        entry = WorkflowInferenceProvider(name="p", executor_type="openai")
        assert entry.credential is None
        assert entry.allowed_models is None
        assert entry.base_url is None

    def test_empty_allowed_models_rejected(self) -> None:
        """allowed_models [] is rejected (G4): remove the provider instead."""
        with pytest.raises(ValidationError, match="allowed_models"):
            WorkflowInferenceProvider(
                name="p", executor_type="openai", allowed_models=[]
            )

    def test_unknown_executor_type_rejected(self) -> None:
        """Only executor types cloud-agents knows are accepted."""
        with pytest.raises(ValidationError):
            WorkflowInferenceProvider(name="p", executor_type="ollama")  # type: ignore[arg-type]

    def test_credential_is_a_secret_ref(self) -> None:
        """The credential is a SecretRef, so env var names are not accepted."""
        entry = WorkflowInferenceProvider(
            name="p", executor_type="openai", credential={"name": "a/b"}  # type: ignore[arg-type]
        )
        assert isinstance(entry.credential, SecretRef)


class TestWorkflowEngineLoadChecks:
    """Config load fails fast on the plan's load-time checks (Phase 1 subset)."""

    def test_valid_governed_config_loads(self) -> None:
        """The example catalog loads and exposes governed mode."""
        cfg = WorkflowEngineConfiguration.model_validate(_engine())
        assert cfg.governed is True
        assert [p.name for p in cfg.providers] == ["claude-prod", "openai-team-b"]

    def test_empty_catalog_is_legacy_mode(self) -> None:
        """No providers means legacy mode and none of the catalog checks apply."""
        cfg = WorkflowEngineConfiguration.model_validate({"enabled": True})
        assert cfg.governed is False
        assert cfg.providers == []

    def test_executor_type_must_be_approved(self, mocker: Any) -> None:
        """executor_type must be in cloud-agents APPROVED_INFERENCE_PROVIDERS."""
        mocker.patch(
            "models.config.approved_inference_providers",
            return_value=frozenset({"openai"}),
        )
        assert "not approved" in _error(_engine())

    def test_credential_needs_a_binding(self) -> None:
        """A credential ref with no secrets binding fails load."""
        config = _engine()
        config["providers"][0]["credential"] = {"name": "inference/missing"}
        assert "no secrets binding" in _error(config)

    def test_pre_269_credential_must_be_env_backed(self) -> None:
        """Before cloud-agents#269 inference credentials must be env-backed."""
        config = _engine()
        config["secrets"][0] = {
            "name": "inference/anthropic-prod",
            "backend": "k8s",
            "secret_name": "s",
            "key": "k",
        }
        assert "must be env" in _error(config)

    def test_default_provider_must_be_in_catalog(self) -> None:
        """default_provider must name a catalog entry."""
        assert "default_provider" in _error(_engine(default_provider="gone"))

    def test_default_model_must_pass_allowed_models(self) -> None:
        """default_model must satisfy the default provider's allowed_models."""
        assert "default_model" in _error(_engine(default_model="claude-opus-4"))

    def test_defaults_required_in_governed_mode(self) -> None:
        """A catalog without a default provider is rejected (no silent fallback)."""
        config = _engine()
        del config["default_provider"]
        assert "default_provider" in _error(config)

    def test_default_model_required_in_governed_mode(self) -> None:
        """No default_model fails at load even when the entry allows any model."""
        config = _engine(default_provider="openai-team-b")
        del config["default_model"]
        assert "default_model" in _error(config)

    def test_duplicate_provider_names_rejected(self) -> None:
        """Provider names are unique."""
        config = _engine()
        config["providers"].append(dict(config["providers"][0]))
        assert "duplicate" in _error(config)

    def test_duplicate_secret_names_rejected(self) -> None:
        """Secret names are unique."""
        config = _engine()
        config["secrets"].append(dict(config["secrets"][0]))
        assert "duplicate" in _error(config)

    def test_secrets_without_catalog_are_allowed(self) -> None:
        """A registry alone (legacy mode) is harmless and loads."""
        cfg = WorkflowEngineConfiguration.model_validate(
            {"secrets": _engine()["secrets"]}
        )
        assert cfg.governed is False

    def test_provider_lookup_helpers(self) -> None:
        """Lookup helpers return entries and bindings by logical name."""
        cfg = WorkflowEngineConfiguration.model_validate(_engine())
        assert cfg.provider("claude-prod").executor_type == "anthropic"  # type: ignore[union-attr]
        assert cfg.provider("nope") is None
        binding: Optional[SecretBinding] = cfg.binding("inference/openai-team-b")
        assert binding is not None and binding.env == "OPENAI_TEAM_B_KEY"
        assert cfg.binding("nope") is None


class TestInferenceAllowedModels:
    """The same [] rejection applies to the Llama Stack provider config (G4)."""

    def test_empty_allowed_models_rejected_for_llama_stack_providers(self) -> None:
        """inference.providers rejects allowed_models [] so both consumers agree."""
        with pytest.raises(ValidationError, match="allowed_models"):
            provider = UnifiedInferenceProvider(  # pyright: ignore[reportCallIssue]
                type="openai", allowed_models=[]
            )
            InferenceConfiguration(  # pyright: ignore[reportCallIssue]
                providers=[provider]
            )
