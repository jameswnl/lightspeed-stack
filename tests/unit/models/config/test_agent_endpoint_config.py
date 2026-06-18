"""Unit tests for AgentEndpointConfig and AgentResourceConfig."""

import pytest
from pydantic import ValidationError

from models.config import AgentEndpointConfig, AgentResourceConfig


class TestAgentResourceConfig:
    """Tests for AgentResourceConfig."""

    def test_defaults(self) -> None:
        """Test default values."""
        cfg = AgentResourceConfig()
        assert cfg.max_tokens_per_run == 50000
        assert cfg.timeout_seconds == 600

    def test_custom_values(self) -> None:
        """Test custom values."""
        cfg = AgentResourceConfig(max_tokens_per_run=10000, timeout_seconds=120)
        assert cfg.max_tokens_per_run == 10000
        assert cfg.timeout_seconds == 120


class TestAgentEndpointConfig:
    """Tests for AgentEndpointConfig."""

    def test_valid_config(self) -> None:
        """Test creating a valid agent config."""
        cfg = AgentEndpointConfig(
            name="diagnostic-agent",
            endpoint="http://diagnostic-agent:8080",
            type="diagnostic",
            skills=["openshift-troubleshooting"],
        )
        assert cfg.name == "diagnostic-agent"
        assert str(cfg.endpoint) == "http://diagnostic-agent:8080/"
        assert cfg.type == "diagnostic"
        assert cfg.skills == ["openshift-troubleshooting"]
        assert cfg.resources is None

    def test_with_resources(self) -> None:
        """Test config with resource limits."""
        cfg = AgentEndpointConfig(
            name="diag",
            endpoint="http://diag:8080",
            type="diagnostic",
            resources=AgentResourceConfig(
                max_tokens_per_run=10000, timeout_seconds=60
            ),
        )
        assert cfg.resources is not None
        assert cfg.resources.max_tokens_per_run == 10000

    def test_missing_name_rejected(self) -> None:
        """Test that missing name is rejected."""
        with pytest.raises(ValidationError):
            AgentEndpointConfig(
                endpoint="http://diag:8080",
                type="diagnostic",
            )

    def test_missing_endpoint_rejected(self) -> None:
        """Test that missing endpoint is rejected."""
        with pytest.raises(ValidationError):
            AgentEndpointConfig(
                name="diag",
                type="diagnostic",
            )

    def test_missing_type_rejected(self) -> None:
        """Test that missing type is rejected."""
        with pytest.raises(ValidationError):
            AgentEndpointConfig(
                name="diag",
                endpoint="http://diag:8080",
            )

    def test_extra_fields_rejected(self) -> None:
        """Test that extra fields are rejected (extra='forbid')."""
        with pytest.raises(ValidationError):
            AgentEndpointConfig(
                name="diag",
                endpoint="http://diag:8080",
                type="diagnostic",
                unknown_field="value",
            )

    def test_empty_skills_default(self) -> None:
        """Test that skills defaults to empty list."""
        cfg = AgentEndpointConfig(
            name="diag",
            endpoint="http://diag:8080",
            type="diagnostic",
        )
        assert cfg.skills == []

    def test_invalid_url_rejected(self) -> None:
        """Test that a malformed URL is rejected."""
        with pytest.raises(ValidationError):
            AgentEndpointConfig(
                name="diag",
                endpoint="not-a-url",
                type="diagnostic",
            )

    def test_invalid_type_rejected(self) -> None:
        """Test that an unsupported agent type is rejected."""
        with pytest.raises(ValidationError):
            AgentEndpointConfig(
                name="diag",
                endpoint="http://diag:8080",
                type="unknown_type",
            )

    def test_valid_types_accepted(self) -> None:
        """Test that all valid agent types are accepted."""
        for agent_type in ("conversational", "diagnostic", "autonomous"):
            cfg = AgentEndpointConfig(
                name="test",
                endpoint="http://test:8080",
                type=agent_type,
            )
            assert cfg.type == agent_type

    def test_json_round_trip(self) -> None:
        """Test serialization round-trip."""
        cfg = AgentEndpointConfig(
            name="diagnostic-agent",
            endpoint="http://diagnostic-agent:8080",
            type="diagnostic",
            skills=["openshift-troubleshooting"],
            resources=AgentResourceConfig(max_tokens_per_run=25000),
        )
        json_str = cfg.model_dump_json()
        restored = AgentEndpointConfig.model_validate_json(json_str)
        assert restored.name == "diagnostic-agent"
        assert restored.resources.max_tokens_per_run == 25000
