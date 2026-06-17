#!/usr/bin/env python3
"""Cluster health check — equivalent of Goose's /cluster-health recipe.

This script is invoked by the pydantic-ai-skills `run_skill_script` tool.
It simulates the same diagnostic workflow a Goose recipe would perform:
query cluster state, evaluate health, and return a structured report.

In production this would call `oc` and `openstack` CLIs using the pod's
ServiceAccount token. For the PoC it returns mock data.

Usage (via pydantic-ai-skills):
    Agent calls: run_skill_script("rhoso-upgrade", "cluster-health.py", {})

Usage (standalone):
    python scripts/cluster-health.py [--format json|text]
"""

import argparse
import json
import sys


def check_compute_services():
    """Check nova-compute service health."""
    services = [
        {"host": "compute-0", "state": "up", "status": "enabled"},
        {"host": "compute-1", "state": "up", "status": "enabled"},
        {"host": "compute-2", "state": "up", "status": "disabled"},
        {"host": "compute-3", "state": "down", "status": "enabled"},
    ]
    issues = [s for s in services if s["state"] != "up" or s["status"] != "enabled"]
    return {"services": services, "issues": issues, "healthy": len(issues) == 0}


def check_network_agents():
    """Check neutron agent health."""
    agents = [
        {"host": "compute-0", "type": "OVN Controller", "alive": True},
        {"host": "compute-1", "type": "OVN Controller", "alive": True},
        {"host": "compute-2", "type": "OVN Controller", "alive": True},
        {"host": "compute-3", "type": "OVN Controller", "alive": False},
        {"host": "networker-0", "type": "OVN Metadata", "alive": True},
    ]
    issues = [a for a in agents if not a["alive"]]
    return {"agents": agents, "issues": issues, "healthy": len(issues) == 0}


def check_instances():
    """Check for VMs in error or unexpected states."""
    problem_vms = [
        {"id": "a1b2c3d4-0006", "name": "test-vm-broken", "status": "ERROR", "host": "compute-3"},
    ]
    return {"problem_vms": problem_vms, "healthy": len(problem_vms) == 0}


def check_volumes():
    """Check for stuck or error volumes."""
    problem_volumes = [
        {"id": "vol-0006", "name": "stuck-volume", "status": "detaching"},
    ]
    return {"problem_volumes": problem_volumes, "healthy": len(problem_volumes) == 0}


def run_health_check():
    """Run all health checks and return aggregate report."""
    compute = check_compute_services()
    network = check_network_agents()
    instances = check_instances()
    volumes = check_volumes()

    all_healthy = all([compute["healthy"], network["healthy"], instances["healthy"], volumes["healthy"]])

    return {
        "overall_healthy": all_healthy,
        "summary": "UNHEALTHY — 4 issues found" if not all_healthy else "HEALTHY",
        "checks": {
            "compute_services": compute,
            "network_agents": network,
            "instances": instances,
            "volumes": volumes,
        },
    }


def format_text(report):
    """Format report as human-readable text."""
    lines = []
    lines.append(f"Cluster Health: {report['summary']}")
    lines.append("=" * 50)

    for name, check in report["checks"].items():
        status = "OK" if check["healthy"] else "ISSUES FOUND"
        lines.append(f"\n{name.replace('_', ' ').title()}: {status}")
        if not check["healthy"]:
            issues = check.get("issues") or check.get("problem_vms") or check.get("problem_volumes") or []
            for issue in issues:
                lines.append(f"  - {issue}")

    return "\n".join(lines)


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(description="RHOSO cluster health check")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    report = run_health_check()

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(format_text(report))

    sys.exit(0 if report["overall_healthy"] else 1)


if __name__ == "__main__":
    main()
