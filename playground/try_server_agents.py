"""PoC: Cloud agents — multiple collaborating server-side agents.

Demonstrates three agent types working together in a single process:
  - Monitoring agent: autonomous loop, detects anomalies
  - Diagnostic agent: investigates and remediates with tools + quality gate
  - Conversational agent: user-facing, delegates investigation to diagnostic agent

Three scenarios:
  1. Autonomous detection and fix — monitoring detects, dispatches diagnostic
  2. User-triggered investigation — user asks conversational agent, it delegates
  3. Predictive preemption — trend detection triggers preemptive cleanup

See spec: docs/design/cloud-agents/cloud-agents.md

Run: uv run python playground/try_server_agents.py
"""

import asyncio
import copy
import sys
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode, UserPromptNode
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from pydantic_graph import End

sys.path.append(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from playground.common import make_model


# =============================================================================
# Structured output models (reusable across agents)
# =============================================================================


class MonitoringAlert(BaseModel):
    """Structured alert from the monitoring agent."""

    host: str
    metric: str
    value: str
    severity: Literal["low", "medium", "high", "critical"]
    context: str
    recommended_action: str


class MonitoringResult(BaseModel):
    """Full monitoring check result — may contain zero or more alerts."""

    alerts: list[MonitoringAlert] = Field(default_factory=list)
    cluster_healthy: bool


class RemediationAction(BaseModel):
    """A single remediation action taken by the diagnostic agent."""

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
# Simulated cluster state (mutable — agents can change it via remediation)
# =============================================================================

cluster_state: dict = {}
action_log: list[dict] = []


def reset_cluster_healthy() -> None:
    """Reset cluster to a fully healthy baseline."""
    global cluster_state
    cluster_state = {
        "hosts": {
            "web-01": {
                "role": "webserver",
                "cpu": 45,
                "memory": 62,
                "disk": 78,
                "status": "healthy",
                "services": {"nginx": "running", "app": "running"},
            },
            "web-02": {
                "role": "webserver",
                "cpu": 35,
                "memory": 50,
                "disk": 45,
                "status": "healthy",
                "services": {"nginx": "running", "app": "running"},
            },
            "db-01": {
                "role": "database",
                "cpu": 30,
                "memory": 55,
                "disk": 70,
                "status": "healthy",
                "services": {"postgresql": "running"},
            },
            "cache-01": {
                "role": "cache",
                "cpu": 15,
                "memory": 40,
                "disk": 30,
                "status": "healthy",
                "services": {"redis": "running"},
            },
        },
        "recent_deploys": [],
        "alerts": [],
    }
    action_log.clear()


def simulate_bad_deploy() -> None:
    """Simulate a bad deployment on web-02 — app crashes, CPU spikes."""
    host = cluster_state["hosts"]["web-02"]
    host["cpu"] = 92
    host["memory"] = 88
    host["status"] = "degraded"
    host["services"]["app"] = "crashed"
    cluster_state["recent_deploys"].append(
        {
            "host": "web-02",
            "app": "frontend",
            "version": "v2.3.1",
            "time": datetime.now(timezone.utc).isoformat(),
            "status": "deployed",
        }
    )
    cluster_state["alerts"].append(
        "web-02: CPU spike 92%, app process crashed after deploy v2.3.1"
    )


def simulate_disk_growth(host: str, target_pct: int) -> None:
    """Simulate disk growing to a target percentage."""
    cluster_state["hosts"][host]["disk"] = target_pct
    if target_pct >= 90:
        cluster_state["hosts"][host]["status"] = "warning"
        cluster_state["alerts"].append(
            f"{host}: disk usage {target_pct}%, approaching critical threshold"
        )


def print_cluster_state(label: str) -> None:
    """Print current cluster state with a label."""
    print(f"{label}:")
    for name, host in cluster_state["hosts"].items():
        svc_status = ", ".join(f"{s}={st}" for s, st in host["services"].items())
        print(
            f"  {name}: {host['status']} "
            f"(cpu={host['cpu']}%, mem={host['memory']}%, disk={host['disk']}%) "
            f"[{svc_status}]"
        )
    print()


# =============================================================================
# Diagnostic agent — tools and quality gate
# (per-agent tool registration — each agent gets only its prescribed tools)
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
2. Use check_host to inspect each host that needs attention
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
    - restart_service:<service_name> — restart a crashed service
    - rollback_deploy:<app_name> — rollback the last deployment
    - cleanup_disk — remove old logs and temp files
    - scale_resources — increase resource limits
    """
    host = cluster_state["hosts"].get(hostname)
    if not host:
        return {"success": False, "error": f"Unknown host: {hostname}"}

    print(f"    [approval] Agent requests: {action} on {hostname}")
    print(f"               Reason: {reason}")
    print(f"               -> AUTO-APPROVED (simulated)")

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
    """Simulate remediation effects on cluster state."""
    if action.startswith("restart_service:"):
        service = action.split(":", 1)[1]
        if service in host["services"]:
            host["services"][service] = "running"
            if service == "app":
                host["cpu"] = max(host["cpu"] - 30, 35)
                host["memory"] = max(host["memory"] - 25, 45)
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
        if host["disk"] > 70:
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
        if host["cpu"] < 80 and host["memory"] < 80:
            host["status"] = "healthy"
        return {"success": True, "message": f"Scaled resources on {hostname}"}

    return {"success": False, "error": f"Unknown action: {action}"}


@diag_agent.output_validator
async def verify_all_fixed(ctx: RunContext, report: DiagnosticReport) -> DiagnosticReport:
    """Quality gate — reject report if hosts are still broken."""
    unhealthy = [
        f"{name} ({h['status']})"
        for name, h in cluster_state["hosts"].items()
        if h["status"] != "healthy"
    ]
    if unhealthy and report.cluster_healthy:
        raise ModelRetry(
            f"Report claims healthy but these hosts are still unhealthy: "
            f"{', '.join(unhealthy)}. Investigate and fix them."
        )
    if not report.actions_taken:
        raise ModelRetry(
            "No remediation actions taken. Use run_remediation tool to fix issues."
        )
    return report


# =============================================================================
# Monitoring agent — detection only, no remediation tools
# =============================================================================

monitor_agent = Agent(
    make_model(),
    defer_model_check=True,
    output_type=MonitoringResult,
    instructions="""\
You are a cluster health monitoring agent. Your job is detection only.

Use get_cluster_summary to check all hosts. Analyze the results and report:
- Any hosts that are not healthy
- Resource usage anomalies (CPU > 80%, disk > 85%)
- Crashed or stopped services

For each issue, create a MonitoringAlert with severity:
- critical: service down, host unreachable, disk > 95%
- high: CPU > 90%, disk > 90%, degraded status
- medium: CPU > 80%, disk > 85%
- low: minor anomalies, informational

Set cluster_healthy=False if any alerts have severity high or critical.
If everything looks good, return an empty alerts list with cluster_healthy=True.
""",
)


@monitor_agent.tool_plain
def get_cluster_summary() -> list[dict]:
    """Get summary of all hosts with metrics and service status."""
    return [
        {
            "hostname": name,
            "role": h["role"],
            "status": h["status"],
            "cpu": h["cpu"],
            "memory": h["memory"],
            "disk": h["disk"],
            "services": h["services"],
        }
        for name, h in cluster_state["hosts"].items()
    ]


# =============================================================================
# Conversational agent — user-facing, delegates to diagnostic agent
# =============================================================================

conv_agent = Agent(
    make_model(),
    defer_model_check=True,
    instructions="""\
You are a helpful cluster assistant. Users ask you about their cluster health.

For simple factual questions, answer directly using get_cluster_overview.
For questions that require investigation or diagnosis (e.g. "why is my app slow",
"what's wrong with my cluster"), use investigate_cluster to delegate to the
diagnostic agent. Present the diagnostic findings in a user-friendly format.
""",
)


@conv_agent.tool_plain
def get_cluster_overview() -> dict:
    """Get a quick overview of cluster health for simple questions."""
    hosts = {}
    for name, h in cluster_state["hosts"].items():
        hosts[name] = {"status": h["status"], "role": h["role"]}
    return {
        "hosts": hosts,
        "active_alerts": len(cluster_state["alerts"]),
        "recent_deploys": len(cluster_state["recent_deploys"]),
    }


@conv_agent.tool
async def investigate_cluster(ctx: RunContext[None], question: str) -> str:
    """Delegate to the diagnostic agent for active cluster investigation."""
    result = await diag_agent.run(question, usage=ctx.usage)
    return result.output.model_dump_json(indent=2)


# =============================================================================
# Helper: run diagnostic agent with step-by-step visibility
# =============================================================================


async def run_diagnostic_visible(prompt: str) -> DiagnosticReport:
    """Run the diagnostic agent with iter() and print each step."""
    step = 0
    async with diag_agent.iter(prompt) as run:
        async for node in run:
            if isinstance(node, CallToolsNode):
                for part in node.model_response.parts:
                    if isinstance(part, ToolCallPart):
                        step += 1
                        args = str(part.args)
                        if len(args) > 100:
                            args = args[:100] + "..."
                        print(f"  [diag] step {step} [tool] {part.tool_name}({args})")
            elif isinstance(node, ModelRequestNode):
                for part in node.request.parts:
                    if isinstance(part, ToolReturnPart):
                        content = str(part.content)
                        if len(content) > 120:
                            content = content[:120] + "..."
                        print(f"         [result] {part.tool_name} -> {content}")

    return run.result.output


def print_report(report: DiagnosticReport) -> None:
    """Print a structured diagnostic report."""
    print()
    print("  " + "=" * 56)
    print("  DIAGNOSTIC REPORT")
    print("  " + "=" * 56)
    print(f"  Summary: {report.summary}")
    print()
    print("  Issues found:")
    for issue in report.issues_found:
        print(f"    - {issue}")
    print()
    print("  Actions taken:")
    for action in report.actions_taken:
        status = "OK" if action.success else "FAILED"
        print(f"    [{status}] {action.host}: {action.action} -> {action.result}")
    if report.remaining_issues:
        print()
        print("  Remaining issues:")
        for issue in report.remaining_issues:
            print(f"    - {issue}")
    print(f"\n  Cluster healthy: {report.cluster_healthy}")


# =============================================================================
# Scenario 1: Autonomous detection and fix
# =============================================================================


async def scenario_autonomous() -> None:
    """Monitoring agent detects anomaly, dispatches diagnostic agent."""
    print("=" * 60)
    print("SCENARIO 1: Autonomous Detection and Fix")
    print("=" * 60)
    print()

    reset_cluster_healthy()
    print_cluster_state("Initial state (healthy)")

    # Simulate a bad deploy
    print("[event] Deploying frontend v2.3.1 to web-02...")
    simulate_bad_deploy()
    print("[event] Deploy complete. App crashed on web-02.")
    print()
    print_cluster_state("State after deploy")

    # Monitoring agent checks the cluster
    print("[monitor] Running health check...")
    monitor_result = await monitor_agent.run("Check all hosts for issues.")
    result = monitor_result.output
    print(f"[monitor] Cluster healthy: {result.cluster_healthy}")
    print(f"[monitor] Alerts: {len(result.alerts)}")

    for alert in result.alerts:
        print(
            f"[monitor] ALERT [{alert.severity}] {alert.host}: "
            f"{alert.metric}={alert.value} — {alert.context}"
        )

    # Dispatch diagnostic agent for high/critical alerts
    critical_alerts = [a for a in result.alerts if a.severity in ("high", "critical")]
    if critical_alerts:
        alert_context = "; ".join(
            f"{a.host}: {a.metric}={a.value} ({a.context})" for a in critical_alerts
        )
        print()
        print(f"[monitor] {len(critical_alerts)} critical alert(s). Dispatching diagnostic agent...")
        print()

        report = await run_diagnostic_visible(
            f"The monitoring agent detected these issues: {alert_context}. "
            f"Investigate and fix all issues."
        )
        print_report(report)
    else:
        print("[monitor] No critical alerts. Cluster healthy.")

    print()
    print_cluster_state("Final state")
    print(f"Total remediations: {len(action_log)}")
    print()


# =============================================================================
# Scenario 2: User-triggered investigation
# =============================================================================


async def scenario_user_triggered() -> None:
    """User asks conversational agent, which delegates to diagnostic agent."""
    print("=" * 60)
    print("SCENARIO 2: User-Triggered Investigation")
    print("=" * 60)
    print()

    reset_cluster_healthy()
    # Pre-seed issues
    simulate_bad_deploy()
    simulate_disk_growth("db-01", 92)

    print_cluster_state("State (pre-existing issues)")

    user_question = "My web application is responding slowly and I'm seeing database errors. What's wrong?"
    print(f"[user] {user_question}")
    print()

    # Conversational agent handles the question
    print("[assistant] Investigating...")
    result = await conv_agent.run(user_question)
    print()
    print("[assistant] Response to user:")
    print(result.output)
    print()
    print(f"Total tokens across both agents: {result.usage.total_tokens}")
    print()


# =============================================================================
# Scenario 3: Predictive preemption
# =============================================================================


async def scenario_predictive() -> None:
    """Trend detection triggers preemptive action before threshold breach."""
    print("=" * 60)
    print("SCENARIO 3: Predictive Preemption")
    print("=" * 60)
    print()

    reset_cluster_healthy()
    print_cluster_state("Initial state (healthy)")

    # Simulate disk gradually filling — not yet critical, but trending
    print("[predict] Simulating disk growth on db-01...")
    simulate_disk_growth("db-01", 82)
    print_cluster_state("State after disk growth (82% — below 90% threshold)")

    # Application-level trend analysis (not LLM — deterministic)
    disk_pct = cluster_state["hosts"]["db-01"]["disk"]
    growth_rate_per_hour = 2  # simulated: 2% per hour
    hours_to_critical = (95 - disk_pct) / growth_rate_per_hour
    print(
        f"[predict] Trend analysis: db-01 disk at {disk_pct}%, "
        f"growing ~{growth_rate_per_hour}%/hour. "
        f"Will hit 95% critical in ~{hours_to_critical:.0f} hours."
    )
    print("[predict] Triggering preemptive cleanup...")
    print()

    report = await run_diagnostic_visible(
        f"PREEMPTIVE ACTION: db-01 disk is at {disk_pct}% and growing at "
        f"~{growth_rate_per_hour}%/hour. It will reach the 95% critical threshold "
        f"in approximately {hours_to_critical:.0f} hours. "
        f"Clean up disk space now to prevent an outage. "
        f"After cleanup, verify the disk usage is back to a safe level."
    )
    print_report(report)
    print()
    print_cluster_state("Final state (after preemptive cleanup)")


# =============================================================================
# Main
# =============================================================================


async def main() -> None:
    """Run all three cloud agent scenarios."""
    await scenario_autonomous()
    await scenario_user_triggered()
    await scenario_predictive()


if __name__ == "__main__":
    asyncio.run(main())
