"""PoC: Diagnose-and-fix agentic workflow.

Simulates the "diagnose this cluster and fix what you can" pattern:
  1. DISCOVER — scan hosts and services
  2. DIAGNOSE — correlate findings
  3. PLAN — decide what to fix
  4. ACT — run remediation (with human approval gate)
  5. VERIFY — check if the fix worked
  6. ITERATE — retry if still broken
  7. REPORT — summarize actions taken

Tests Pydantic AI's ability to drive a multi-step, self-correcting workflow
with a output_validator quality gate.

Run: uv run python playground/try_diagnose_and_fix.py
"""

import asyncio
import sys
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode, UserPromptNode
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from pydantic_graph import End

import sys; sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model


# =============================================================================
# Simulated cluster state (mutable — the agent can change it via remediation)
# =============================================================================

cluster_state: dict = {}
action_log: list[dict] = []


def reset_cluster() -> None:
    """Reset cluster to a broken state for testing."""
    global cluster_state
    cluster_state = {
        "hosts": {
            "web-01": {
                "role": "webserver",
                "cpu": 45,
                "memory": 62,
                "disk": 78,
                "status": "healthy",
                "services": {
                    "nginx": "running",
                    "app": "running",
                },
            },
            "web-02": {
                "role": "webserver",
                "cpu": 92,
                "memory": 88,
                "disk": 45,
                "status": "degraded",
                "services": {
                    "nginx": "running",
                    "app": "crashed",
                },
            },
            "db-01": {
                "role": "database",
                "cpu": 30,
                "memory": 55,
                "disk": 95,
                "status": "critical",
                "services": {
                    "postgresql": "running",
                },
            },
        },
        "recent_deploys": [
            {
                "host": "web-02",
                "app": "frontend",
                "version": "v2.3.1",
                "time": "2026-06-10T14:25:00Z",
                "status": "deployed",
            },
        ],
        "alerts": [
            "web-02: CPU spike 92%, app process crashed after deploy v2.3.1",
            "db-01: disk usage 95%, slow queries detected (>5s)",
        ],
    }
    action_log.clear()


# =============================================================================
# Structured output for the final report
# =============================================================================


class RemediationAction(BaseModel):
    """A single remediation action taken by the agent."""

    host: str
    action: str
    result: str
    success: bool


class DiagnosticReport(BaseModel):
    """Final structured report from the diagnostic agent."""

    summary: str
    issues_found: list[str]
    actions_taken: list[RemediationAction]
    remaining_issues: list[str] = Field(default_factory=list)
    cluster_healthy: bool


# =============================================================================
# Diagnostic agent with tools
# =============================================================================

diag_agent = Agent(
    make_model(),
    defer_model_check=True,
    output_type=DiagnosticReport,
    retries=3,
    instructions="""\
You are a cluster diagnostic and remediation agent.

Your workflow:
1. Use list_hosts to discover hosts
2. Use check_host to inspect each host
3. Use get_alerts to see active alerts
4. Use get_recent_deploys to check recent deployments
5. For each issue found, use run_remediation to fix it
6. After remediation, use check_host again to verify the fix worked
7. If verification fails, try a different remediation approach

You MUST verify every remediation by checking the host status afterward.
You MUST attempt to fix all issues you find, not just report them.

Return a structured DiagnosticReport when all issues are addressed.
""",
)


@diag_agent.tool_plain
def list_hosts() -> list[dict]:
    """List all hosts with their role and current status."""
    return [
        {"hostname": k, "role": v["role"], "status": v["status"]}
        for k, v in cluster_state["hosts"].items()
    ]


@diag_agent.tool_plain
def check_host(hostname: str) -> dict:
    """Get detailed status for a host including resource usage and services."""
    host = cluster_state["hosts"].get(hostname)
    if not host:
        return {"error": f"Unknown host: {hostname}"}
    return {"hostname": hostname, **host}


@diag_agent.tool_plain
def get_alerts() -> list[str]:
    """Get active cluster alerts."""
    return cluster_state["alerts"]


@diag_agent.tool_plain
def get_recent_deploys() -> list[dict]:
    """Get recent deployments across the cluster."""
    return cluster_state["recent_deploys"]


@diag_agent.tool_plain
def run_remediation(hostname: str, action: str, reason: str) -> dict:
    """Run a remediation action on a host.

    Available actions:
    - restart_service:<service_name> — restart a service
    - rollback_deploy:<app_name> — rollback the last deployment
    - cleanup_disk — remove old logs and temp files
    - scale_resources — increase resource limits

    Args:
        hostname: Target host
        action: One of the available actions above
        reason: Why this remediation is needed
    """
    host = cluster_state["hosts"].get(hostname)
    if not host:
        return {"success": False, "error": f"Unknown host: {hostname}"}

    # --- Simulate human approval ---
    print(f"    [approval] Agent requests: {action} on {hostname}")
    print(f"               Reason: {reason}")
    print(f"               -> AUTO-APPROVED (simulated)")

    # --- Simulate remediation effects ---
    result = _apply_remediation(hostname, host, action)

    action_log.append(
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "host": hostname,
            "action": action,
            "reason": reason,
            "result": result,
        }
    )

    return result


def _apply_remediation(hostname: str, host: dict, action: str) -> dict:
    """Simulate the effect of a remediation action on cluster state."""
    if action.startswith("restart_service:"):
        service = action.split(":", 1)[1]
        if service in host["services"]:
            host["services"][service] = "running"
            if hostname == "web-02" and service == "app":
                host["cpu"] = 55
                host["memory"] = 60
                host["status"] = "healthy"
            return {"success": True, "message": f"Service {service} restarted on {hostname}"}
        return {"success": False, "error": f"Service {service} not found on {hostname}"}

    if action.startswith("rollback_deploy:"):
        app = action.split(":", 1)[1]
        for deploy in cluster_state["recent_deploys"]:
            if deploy["host"] == hostname and deploy["app"] == app:
                deploy["status"] = "rolled_back"
                host["services"]["app"] = "running"
                host["cpu"] = 40
                host["memory"] = 55
                host["status"] = "healthy"
                return {"success": True, "message": f"Rolled back {app} on {hostname}"}
        return {"success": False, "error": f"No recent deploy of {app} on {hostname}"}

    if action == "cleanup_disk":
        if host["disk"] > 80:
            host["disk"] = max(host["disk"] - 30, 45)
            if host["disk"] < 90:
                host["status"] = "healthy"
            return {
                "success": True,
                "message": f"Cleaned disk on {hostname}, now at {host['disk']}%",
            }
        return {"success": False, "error": "Disk usage already acceptable"}

    if action == "scale_resources":
        host["cpu"] = max(host["cpu"] - 20, 20)
        host["memory"] = max(host["memory"] - 15, 30)
        return {"success": True, "message": f"Scaled resources on {hostname}"}

    return {"success": False, "error": f"Unknown action: {action}"}


# --- Result validator: quality gate ---


@diag_agent.output_validator
async def verify_all_fixed(ctx: RunContext, report: DiagnosticReport) -> DiagnosticReport:
    """Verify the agent actually fixed things — check cluster state."""
    unhealthy = [
        f"{name} ({h['status']})"
        for name, h in cluster_state["hosts"].items()
        if h["status"] != "healthy"
    ]
    if unhealthy and report.cluster_healthy:
        raise ModelRetry(
            f"Report says cluster is healthy but these hosts are still unhealthy: "
            f"{', '.join(unhealthy)}. Investigate and fix them."
        )
    if not report.actions_taken:
        raise ModelRetry(
            "No remediation actions were taken. You must attempt to fix issues, "
            "not just report them. Use run_remediation tool."
        )
    return report


# =============================================================================
# Examples
# =============================================================================


async def full_diagnostic() -> None:
    """Full diagnostic workflow with step-by-step visibility."""
    reset_cluster()

    print("=== Diagnose and Fix: Full Workflow ===")
    print()
    print("Initial cluster state:")
    for name, host in cluster_state["hosts"].items():
        print(f"  {name}: {host['status']} (cpu={host['cpu']}%, disk={host['disk']}%)")
    print()

    step = 0
    async with diag_agent.iter(
        "The cluster has multiple alerts firing. Diagnose all issues and fix what you can."
    ) as run:
        async for node in run:
            if isinstance(node, UserPromptNode):
                print(f"[user] {node.user_prompt}")
                print()
            elif isinstance(node, CallToolsNode):
                for part in node.model_response.parts:
                    if isinstance(part, ToolCallPart):
                        step += 1
                        args = str(part.args)
                        if len(args) > 100:
                            args = args[:100] + "..."
                        print(f"  step {step} [tool] {part.tool_name}({args})")
                    elif isinstance(part, TextPart):
                        pass
            elif isinstance(node, ModelRequestNode):
                for part in node.request.parts:
                    if isinstance(part, ToolReturnPart):
                        content = str(part.content)
                        if len(content) > 120:
                            content = content[:120] + "..."
                        print(f"         [result] {part.tool_name} -> {content}")

    report = run.result.output
    print()
    print("=" * 60)
    print("DIAGNOSTIC REPORT")
    print("=" * 60)
    print(f"Summary: {report.summary}")
    print()
    print("Issues found:")
    for issue in report.issues_found:
        print(f"  - {issue}")
    print()
    print("Actions taken:")
    for action in report.actions_taken:
        status = "OK" if action.success else "FAILED"
        print(f"  [{status}] {action.host}: {action.action} -> {action.result}")
    if report.remaining_issues:
        print()
        print("Remaining issues:")
        for issue in report.remaining_issues:
            print(f"  - {issue}")
    print()
    print(f"Cluster healthy: {report.cluster_healthy}")
    print()
    print("Final cluster state:")
    for name, host in cluster_state["hosts"].items():
        print(f"  {name}: {host['status']} (cpu={host['cpu']}%, disk={host['disk']}%)")
    print()
    print(f"Total tool calls: {step}")
    print(f"Action log: {len(action_log)} remediation(s) executed")


async def main() -> None:
    """Run diagnostic examples."""
    await full_diagnostic()


if __name__ == "__main__":
    asyncio.run(main())
