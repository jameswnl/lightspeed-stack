"""Integration tests for one-step workflows via POST /v1/workflows/run.

A one-shot agent invocation is a one-step workflow definition submitted to
the normal workflow path. These tests pin the documented one-step contract
(``agent`` / ``result`` naming, full former-standalone input coverage,
identical normalization to multi-step steps) against the real installed
cloud-agents package -- no LLM calls, no mocks of cloud-agents itself.
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods,unspecified-encoding

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from cloud_agents.workflow.core.definition import WorkflowDefinition
from cloud_agents.workflow.core.execution import (
    build_step_input,
    normalize_definition,
)
from cloud_agents.workflow.core.validation import validate_definition


def _one_step_definition(step: dict[str, Any]) -> dict[str, Any]:
    """Wrap a single step dict in the documented one-step definition shape."""
    return {
        "apiVersion": "v1",
        "kind": "AgentWorkflow",
        "metadata": {"name": "one-shot-agent"},
        "spec": {"steps": [step]},
    }


class TestOneStepWorkflowContract:
    """The documented one-step workflow shape validates and normalizes."""

    def test_full_one_step_shape(self) -> None:
        """Every former standalone input is expressible on a one-step workflow.

        Covers prompt, instructions, provider/model (run-level), tools,
        MCP servers, skills, permissions, spawn mode, sandbox config,
        output schema, context, and timeout.
        """
        definition = _one_step_definition(
            {
                "name": "agent",
                "type": "agent",
                "prompt": "Inspect the cluster",
                "output_key": "result",
                "instructions": "Be concise.",
                "spawn": "ephemeral",
                "spawn_config": {"sandbox_image": "custom-sandbox:v2"},
                "tools": ["kubectl_get"],
                "mcp_servers": ["cluster"],
                "allowed_skills": ["kubernetes"],
                "permissions": {"service_account": "agent-runner"},
                "output_schema": {"type": "object"},
                "context": {"cluster": "prod"},
                "timeout_seconds": 120,
            }
        )
        run_context = {
            "provider": {"name": "openai", "model": "gpt-4o"},
            "sandbox_image": "sandbox:latest",
        }

        assert validate_definition(definition) == []

        agent_steps, _ = normalize_definition(definition)
        assert len(agent_steps) == 1
        step_input = build_step_input(definition["spec"]["steps"][0], run_context)
        assert step_input.prompt == "Inspect the cluster"
        assert step_input.provider["name"] == "openai"
        assert step_input.provider["model"] == "gpt-4o"
        assert step_input.tools == ["kubectl_get"]
        assert step_input.allowed_skills == ["kubernetes"]
        assert step_input.output_schema == {"type": "object"}
        assert step_input.timeout_seconds == 120

    def test_bare_one_step_defaults(self) -> None:
        """A bare single step defaults to agent/result naming and ephemeral spawn.

        Locks in the canonical one-step defaults: the ``agent`` /
        ``result`` naming convention and the ``ephemeral`` spawn default.
        """
        definition = _one_step_definition({"prompt": "Inspect the cluster"})

        assert validate_definition(definition) == []

        agent_steps, _ = normalize_definition(definition)
        assert len(agent_steps) == 1
        assert agent_steps[0].name == "agent"
        assert agent_steps[0].output_key == "result"
        assert agent_steps[0].spawn == "ephemeral"

    def test_one_step_matches_multi_step_normalization(self) -> None:
        """A one-step workflow normalizes identically to the same multi-step step.

        This is the core #55 invariant: one-shot runs take exactly the same
        path and semantics as a step in a multi-step workflow.
        """
        step = {
            "name": "agent",
            "type": "agent",
            "prompt": "Inspect the cluster",
            "output_key": "result",
            "spawn": "none",
            "tools": ["kubectl_get"],
            "timeout_seconds": 60,
        }
        one_step = _one_step_definition(dict(step))
        multi_step = {
            "apiVersion": "v1",
            "kind": "AgentWorkflow",
            "metadata": {"name": "two-step"},
            "spec": {
                "steps": [
                    dict(step),
                    {
                        "name": "verify",
                        "type": "agent",
                        "prompt": "Verify",
                        "output_key": "verification",
                        "spawn": "none",
                    },
                ]
            },
        }

        assert validate_definition(one_step) == []
        assert validate_definition(multi_step) == []

        single_normalized, _ = normalize_definition(one_step)
        multi_normalized, _ = normalize_definition(multi_step)
        assert single_normalized[0].model_dump() == multi_normalized[0].model_dump()

    def test_secret_value_rejected(self) -> None:
        """Inline MCP secret values fail validation, never reaching run state."""
        definition = _one_step_definition(
            {
                "name": "agent",
                "type": "agent",
                "prompt": "Inspect the cluster",
                "output_key": "result",
                "mcp_servers": [
                    {
                        "name": "cluster",
                        "url": "https://admin:s3cret@example.com/mcp",
                    }
                ],
            }
        )

        assert validate_definition(definition) != []
        with pytest.raises(ValueError, match="credentialed URL"):
            normalize_definition(definition)


class TestWorkflowDefinitionExecution:
    """Test that cloud-agents workflow definitions are compatible."""

    def test_triage_classify_definition_parses(self) -> None:
        """The triage-classify workflow YAML parses into a valid definition."""
        wf_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "lightspeed-cloud-agents"
            / "examples"
            / "workflow-definitions"
            / "triage-classify-workflow.yaml"
        )
        if not wf_path.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")
        with open(wf_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        definition = WorkflowDefinition.model_validate(raw)
        assert definition.metadata["name"] == "triage-classify-alerts"
        assert len(definition.spec.steps) == 3
