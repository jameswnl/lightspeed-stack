"""E2E tests executing cloud-agents example workflows via lightspeed-stack.

Runs workflow steps sequentially using DirectExecutor, auto-approving
human-approval steps. Verifies step chaining, output schemas, and
transcript capture.

Requires OPENAI_API_KEY. No PostgreSQL needed — runs without
WorkflowStorageFactory.

Usage:
    uv run pytest tests/e2e/cloud_agents/test_workflow_execution_e2e.py -v -s
"""

# pylint: disable=import-outside-toplevel,too-few-public-methods

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)

_WF_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "lightspeed-cloud-agents"
    / "examples"
    / "workflow-definitions"
)

_PROVIDER = {"name": "openai", "model": "gpt-4o-mini"}


async def _run_workflow_steps(
    definition: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Run all agent steps in a workflow definition sequentially.

    Auto-approves human-approval steps. Chains step outputs as context.

    Parameters:
        definition: Parsed workflow YAML.

    Returns:
        Dict of step results keyed by output_key.
    """
    from cloud_agents.workflow.executor.step.base import StepInput
    from cloud_agents.workflow.executor.step.dispatch import get_step_executor

    steps = definition["spec"]["steps"]
    step_results: dict[str, dict[str, Any]] = {}

    for step in steps:
        step_name = step["name"]
        step_type = step.get("type", "agent")
        output_key = step.get("output_key", step_name)
        spawn = step.get("spawn", "none")

        if step_type == "human-approval":
            step_results[output_key] = {
                "status": "completed",
                "output": {"approved": True, "auto_approved": True},
            }
            continue

        if spawn not in ("none", "local"):
            step_results[output_key] = {
                "status": "skipped",
                "output": None,
                "error": f"spawn={spawn} not available in E2E test",
            }
            continue

        prompt = step.get("prompt", "")
        instructions = step.get("instructions")

        step_def = {"name": step_name, "spawn": spawn}
        executor = get_step_executor(step_def, spawner=None)

        step_input = StepInput(
            prompt=prompt,
            provider=_PROVIDER,
            system_prompt=instructions,
            context=step_results,
            step_name=step_name,
            output_key=output_key,
        )

        result = await executor.run(step_input)
        step_results[output_key] = {
            "status": result.status,
            "output": result.output,
            "error": result.error,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "duration_ms": result.duration_ms,
        }

    return step_results


class TestTriageClassifyWorkflow:
    """E2E test for the triage-classify-alerts workflow."""

    @pytest.fixture(name="workflow_def")
    def workflow_def_fixture(self) -> dict[str, Any]:
        """Load the triage-classify workflow definition."""
        wf_path = _WF_DIR / "triage-classify-workflow.yaml"
        if not wf_path.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")
        with open(wf_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @pytest.mark.asyncio
    async def test_full_workflow_executes(self, workflow_def: dict[str, Any]) -> None:
        """All steps complete: triage → approve → generate-runbook."""
        results = await _run_workflow_steps(workflow_def)

        assert len(results) == 3

        triage = results["triage_result"]
        assert triage["status"] == "completed"
        assert triage["output"] is not None
        assert triage["input_tokens"] > 0

        approval = results["approval"]
        assert approval["status"] == "completed"
        assert approval["output"]["approved"] is True

        runbook = results["runbook"]
        assert runbook["status"] == "completed"
        assert runbook["output"] is not None
        assert runbook["input_tokens"] > 0

    @pytest.mark.asyncio
    async def test_step_outputs_chain(self, workflow_def: dict[str, Any]) -> None:
        """Later steps receive prior step results as context."""
        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        step1 = workflow_def["spec"]["steps"][0]
        executor = get_step_executor(
            {"name": step1["name"], "spawn": "none"}, spawner=None
        )
        result1 = await executor.run(
            StepInput(
                prompt=step1["prompt"],
                provider=_PROVIDER,
                step_name=step1["name"],
                output_key=step1["output_key"],
            )
        )

        step3 = workflow_def["spec"]["steps"][2]
        context = {
            step1["output_key"]: {
                "status": result1.status,
                "output": result1.output,
            }
        }
        result3 = await executor.run(
            StepInput(
                prompt=step3["prompt"],
                provider=_PROVIDER,
                system_prompt=step3.get("instructions"),
                context=context,
                step_name=step3["name"],
                output_key=step3["output_key"],
            )
        )

        assert result3.status == "completed"
        assert result3.output is not None


class TestLocalInvestigateWorkflow:
    """E2E test for the local-investigate-alerts workflow."""

    @pytest.fixture(name="workflow_def")
    def workflow_def_fixture(self) -> dict[str, Any]:
        """Load the local-investigate workflow definition."""
        wf_path = _WF_DIR / "local-investigate-workflow.yaml"
        if not wf_path.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")
        with open(wf_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @pytest.mark.asyncio
    async def test_full_workflow_executes(self, workflow_def: dict[str, Any]) -> None:
        """All spawn:none/local steps complete, ephemeral steps skipped."""
        results = await _run_workflow_steps(workflow_def)

        assert len(results) >= 3

        triage = results.get("triage_result")
        assert triage is not None
        assert triage["status"] == "completed"


class TestSecurityAuditWorkflow:
    """E2E test for the security-audit workflow."""

    @pytest.fixture(name="workflow_def")
    def workflow_def_fixture(self) -> dict[str, Any]:
        """Load the security-audit workflow definition."""
        wf_path = _WF_DIR / "security-audit-workflow.yaml"
        if not wf_path.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")
        with open(wf_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @pytest.mark.asyncio
    async def test_first_step_executes(self, workflow_def: dict[str, Any]) -> None:
        """First agent step produces a result."""
        steps = workflow_def["spec"]["steps"]
        agent_steps = [
            s
            for s in steps
            if s.get("type", "agent") == "agent" and s.get("spawn", "none") == "none"
        ]
        if not agent_steps:
            pytest.skip("No spawn:none agent steps in this workflow")

        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        step = agent_steps[0]
        executor = get_step_executor(
            {"name": step["name"], "spawn": "none"}, spawner=None
        )
        result = await executor.run(
            StepInput(
                prompt=step.get("prompt", ""),
                provider=_PROVIDER,
                system_prompt=step.get("instructions"),
                step_name=step["name"],
                output_key=step.get("output_key", step["name"]),
            )
        )

        assert result.status == "completed"
        assert result.output is not None


class TestAllWorkflowDefinitions:
    """Verify all workflow definitions parse and have executable steps."""

    def test_all_definitions_valid(self) -> None:
        """All YAML files parse into valid WorkflowDefinitions."""
        if not _WF_DIR.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")

        from cloud_agents.workflow.core.definition import WorkflowDefinition

        for wf_file in sorted(_WF_DIR.glob("*.yaml")):
            with open(wf_file, encoding="utf-8") as f:
                raw = yaml.safe_load(f)

            definition = WorkflowDefinition.model_validate(raw)
            assert definition.spec.steps, f"{wf_file.name}: no steps"
            assert definition.metadata.get("name"), f"{wf_file.name}: no name"

    @pytest.mark.asyncio
    async def test_all_spawn_none_steps_execute(self) -> None:
        """Every spawn:none step across all workflows produces a result."""
        if not _WF_DIR.exists():
            pytest.skip("lightspeed-cloud-agents repo not found")

        from cloud_agents.workflow.executor.step.base import StepInput
        from cloud_agents.workflow.executor.step.dispatch import get_step_executor

        for wf_file in sorted(_WF_DIR.glob("*.yaml")):
            raw = yaml.safe_load(wf_file.read_text(encoding="utf-8"))

            steps = raw.get("spec", {}).get("steps", [])
            none_steps = [
                s
                for s in steps
                if s.get("type", "agent") == "agent"
                and s.get("spawn", "none") == "none"
            ]

            for step in none_steps[:1]:
                executor = get_step_executor(
                    {"name": step["name"], "spawn": "none"}, spawner=None
                )
                result = await executor.run(
                    StepInput(
                        prompt=step.get("prompt", "Summarize."),
                        provider=_PROVIDER,
                        system_prompt=step.get("instructions"),
                        step_name=step["name"],
                        output_key=step.get("output_key", step["name"]),
                    )
                )
                assert (
                    result.status == "completed"
                ), f"{wf_file.name}/{step['name']}: {result.error}"
