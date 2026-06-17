"""PoC: RHOSO Diagnose-and-Fix with quality gates (RHOSSTRAT-872 + RHOSSTRAT-1276).

Extends the Goose-replacement PoC with patterns from the diagnose-and-fix
feasibility assessment (lcore-pydantic-ai-diagnose-and-fix.md):

  - output_validator quality gate — agent can't declare "ready to upgrade"
    while critical blockers exist in the cluster state
  - Human-in-the-loop approval — destructive actions (disable compute,
    migrate VMs, reset volumes) require simulated operator approval
  - Structured output — UpgradeReadinessReport (Pydantic model)
  - Self-verification — agent re-checks cluster state after each remediation
  - Mutable cluster state — remediation actually changes mock state

This is the "full loop" demo: the agent discovers issues, fixes them (with
approval), verifies the fixes, and produces a structured readiness report.

Run: uv run python playground/try_rhoso_diagnose_and_fix.py
"""

import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode, UserPromptNode
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai_skills import SkillsCapability
from pydantic_graph import End

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model
from playground.rhoso_mcp_server import (
    config_server,
    logs_server,
    openstackclient_server,
    upgrade_server,
)

SKILLS_DIR = str(Path(__file__).resolve().parent.parent / "examples" / "skills")


# =============================================================================
# Mutable cluster state (agent remediations change this)
# =============================================================================

cluster_state: dict = {}
action_log: list[dict] = []


def reset_cluster() -> None:
    """Reset to a broken cluster state for testing."""
    global cluster_state
    cluster_state = {
        "compute_services": {
            "compute-0": {"state": "up", "status": "enabled"},
            "compute-1": {"state": "up", "status": "enabled"},
            "compute-2": {"state": "up", "status": "disabled", "reason": "Maintenance scheduled"},
            "compute-3": {"state": "down", "status": "enabled"},
        },
        "error_vms": {
            "a1b2c3d4-0006": {
                "name": "test-vm-broken",
                "status": "ERROR",
                "host": "compute-3",
            },
        },
        "stuck_volumes": {
            "vol-0006": {
                "name": "stuck-volume",
                "status": "detaching",
                "attached_to": "a1b2c3d4-0006",
            },
        },
        "vms_on_host": {
            "compute-2": ["a1b2c3d4-0005", "a1b2c3d4-0007"],
        },
    }
    action_log.clear()


# =============================================================================
# Structured output
# =============================================================================


class RemediationAction(BaseModel):
    """A single remediation action taken during pre-upgrade preparation."""

    action: str
    target: str
    result: str
    success: bool


class UpgradeReadinessReport(BaseModel):
    """Structured report on cluster upgrade readiness."""

    summary: str
    blockers_found: list[str]
    blockers_resolved: list[str] = Field(default_factory=list)
    actions_taken: list[RemediationAction] = Field(default_factory=list)
    remaining_blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ready_to_upgrade: bool


# =============================================================================
# Upgrade readiness agent
# =============================================================================

upgrade_agent = Agent(
    make_model(),
    defer_model_check=True,
    output_type=UpgradeReadinessReport,
    retries=3,
    instructions="""\
You are an RHOSO upgrade readiness agent running inside an OpenStackAssistant pod.
Your job is to prepare the cluster for an upgrade from RHOSO 18 to 19.

Workflow:
1. Use run_pre_upgrade_check to identify blockers and warnings
2. Use get_compute_services, get_server_list, get_volume_list to gather details
3. For each critical blocker, use the remediation tools to fix it:
   - reset_error_vm: delete or reset VMs in ERROR state
   - reset_stuck_volume: force-reset volumes stuck in transitional states
   - disable_compute_service: disable a compute node's nova-compute
   - migrate_vms_off_host: live-migrate all VMs off a host
4. After each remediation, verify the fix by re-checking cluster state
5. If a fix doesn't work, try a different approach
6. Return a structured UpgradeReadinessReport

You MUST attempt to fix all critical blockers before declaring ready_to_upgrade.
You MUST verify every remediation by checking the state afterward.
Only set ready_to_upgrade=true if ALL critical blockers are resolved.
""",
    toolsets=[
        MCPToolset(openstackclient_server),
        MCPToolset(config_server),
        MCPToolset(logs_server),
        MCPToolset(upgrade_server),
    ],
    capabilities=[SkillsCapability(directories=[SKILLS_DIR])],
)


# --- Remediation tools (write operations, with approval gates) ---


@upgrade_agent.tool_plain
def get_cluster_blocker_status() -> dict:
    """Get current state of known blockers (error VMs, stuck volumes, down services)."""
    down_services = [
        h for h, s in cluster_state["compute_services"].items() if s["state"] == "down"
    ]
    return {
        "down_compute_services": down_services,
        "error_vms": list(cluster_state["error_vms"].keys()),
        "stuck_volumes": list(cluster_state["stuck_volumes"].keys()),
        "vms_needing_migration": {
            host: vms for host, vms in cluster_state["vms_on_host"].items() if vms
        },
    }


@upgrade_agent.tool_plain
def reset_error_vm(vm_id: str, reason: str) -> dict:
    """Delete or reset a VM in ERROR state.

    Args:
        vm_id: The VM instance ID to reset.
        reason: Why this VM needs to be reset.
    """
    print(f"    [approval] Reset error VM {vm_id}")
    print(f"               Reason: {reason}")
    print(f"               -> AUTO-APPROVED (simulated)")

    if vm_id in cluster_state["error_vms"]:
        del cluster_state["error_vms"][vm_id]
        action_log.append({"action": "reset_error_vm", "target": vm_id, "reason": reason})
        return {"success": True, "message": f"VM {vm_id} deleted/reset"}
    return {"success": False, "error": f"VM {vm_id} not found in error state"}


@upgrade_agent.tool_plain
def reset_stuck_volume(volume_id: str, reason: str) -> dict:
    """Force-reset a volume stuck in a transitional state (attaching/detaching).

    Args:
        volume_id: The volume ID to reset.
        reason: Why this volume needs to be reset.
    """
    print(f"    [approval] Reset stuck volume {volume_id}")
    print(f"               Reason: {reason}")
    print(f"               -> AUTO-APPROVED (simulated)")

    if volume_id in cluster_state["stuck_volumes"]:
        del cluster_state["stuck_volumes"][volume_id]
        action_log.append({"action": "reset_stuck_volume", "target": volume_id, "reason": reason})
        return {"success": True, "message": f"Volume {volume_id} reset to 'available'"}
    return {"success": False, "error": f"Volume {volume_id} not found in stuck state"}


@upgrade_agent.tool_plain
def migrate_vms_off_host(hostname: str, reason: str) -> dict:
    """Live-migrate all VMs off a compute host before upgrading it.

    Args:
        hostname: The compute host to evacuate.
        reason: Why VMs need to be migrated off.
    """
    print(f"    [approval] Migrate all VMs off {hostname}")
    print(f"               Reason: {reason}")
    print(f"               -> AUTO-APPROVED (simulated)")

    vms = cluster_state["vms_on_host"].get(hostname, [])
    if not vms:
        return {"success": True, "message": f"No VMs on {hostname}, already empty"}

    migrated = list(vms)
    cluster_state["vms_on_host"][hostname] = []
    action_log.append({
        "action": "migrate_vms_off_host",
        "target": hostname,
        "vms_migrated": migrated,
        "reason": reason,
    })
    return {
        "success": True,
        "message": f"Migrated {len(migrated)} VMs off {hostname}: {migrated}",
    }


# --- Quality gate: output_validator ---


@upgrade_agent.output_validator
async def verify_readiness(ctx: RunContext, report: UpgradeReadinessReport) -> UpgradeReadinessReport:
    """Verify the agent's report matches actual cluster state."""
    actual_blockers = []
    if cluster_state["error_vms"]:
        actual_blockers.append(f"{len(cluster_state['error_vms'])} error VMs remain")
    if cluster_state["stuck_volumes"]:
        actual_blockers.append(f"{len(cluster_state['stuck_volumes'])} stuck volumes remain")

    down = [h for h, s in cluster_state["compute_services"].items() if s["state"] == "down"]
    if down:
        actual_blockers.append(f"Compute services down: {', '.join(down)}")

    if actual_blockers and report.ready_to_upgrade:
        raise ModelRetry(
            f"Report says ready_to_upgrade=true but blockers remain: "
            f"{'; '.join(actual_blockers)}. Fix them or set ready_to_upgrade=false."
        )

    if not report.actions_taken and report.blockers_found:
        raise ModelRetry(
            "Blockers were found but no remediation actions taken. "
            "Use the remediation tools (reset_error_vm, reset_stuck_volume, "
            "migrate_vms_off_host) to fix blockers."
        )

    return report


# =============================================================================
# Demo
# =============================================================================


async def full_upgrade_readiness() -> None:
    """Full upgrade readiness workflow with diagnose-and-fix loop."""
    reset_cluster()

    print("=" * 70)
    print("  RHOSO Upgrade Readiness: Diagnose and Fix")
    print("  Patterns: output_validator, approval gates, structured output")
    print("=" * 70)
    print()
    print("Initial cluster state:")
    print(f"  Down services: {[h for h, s in cluster_state['compute_services'].items() if s['state'] == 'down']}")
    print(f"  Error VMs:     {list(cluster_state['error_vms'].keys())}")
    print(f"  Stuck volumes: {list(cluster_state['stuck_volumes'].keys())}")
    print(f"  VMs to migrate: {cluster_state['vms_on_host']}")
    print()

    step = 0
    async with upgrade_agent.iter(
        "Prepare this RHOSO 18 cluster for upgrade to RHOSO 19. "
        "Check for blockers, fix everything you can, and report readiness."
    ) as run:
        async for node in run:
            if isinstance(node, CallToolsNode):
                for part in node.model_response.parts:
                    if isinstance(part, ToolCallPart):
                        step += 1
                        args = str(part.args)
                        if len(args) > 100:
                            args = args[:100] + "..."
                        print(f"  step {step} [tool] {part.tool_name}({args})")
            elif isinstance(node, ModelRequestNode):
                for part in node.request.parts:
                    if isinstance(part, ToolReturnPart):
                        content = str(part.content)
                        if len(content) > 150:
                            content = content[:150] + "..."
                        print(f"         -> {content}")

    report = run.result.output

    print()
    print("=" * 70)
    print("  UPGRADE READINESS REPORT")
    print("=" * 70)
    print(f"  Summary: {report.summary}")
    print()
    print("  Blockers found:")
    for b in report.blockers_found:
        print(f"    - {b}")
    print()
    print("  Blockers resolved:")
    for b in report.blockers_resolved:
        print(f"    - {b}")
    print()
    print("  Actions taken:")
    for a in report.actions_taken:
        status = "OK" if a.success else "FAILED"
        print(f"    [{status}] {a.action} on {a.target} -> {a.result}")
    if report.remaining_blockers:
        print()
        print("  Remaining blockers:")
        for b in report.remaining_blockers:
            print(f"    - {b}")
    if report.warnings:
        print()
        print("  Warnings:")
        for w in report.warnings:
            print(f"    - {w}")
    print()
    print(f"  Ready to upgrade: {report.ready_to_upgrade}")
    print()
    print("Final cluster state:")
    print(f"  Down services: {[h for h, s in cluster_state['compute_services'].items() if s['state'] == 'down']}")
    print(f"  Error VMs:     {list(cluster_state['error_vms'].keys())}")
    print(f"  Stuck volumes: {list(cluster_state['stuck_volumes'].keys())}")
    print(f"  VMs to migrate: {cluster_state['vms_on_host']}")
    print()
    print(f"  Total tool calls: {step}")
    print(f"  Remediations executed: {len(action_log)}")


async def main() -> None:
    """Run the RHOSO diagnose-and-fix demo."""
    await full_upgrade_readiness()


if __name__ == "__main__":
    asyncio.run(main())
