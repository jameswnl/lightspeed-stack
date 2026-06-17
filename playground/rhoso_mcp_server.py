"""In-process MCP servers simulating RHOSO cluster context.

Models the architecture from the RHOSSTRAT-872 child tickets — four separate
MCP servers, each with a distinct responsibility:

  - openstackclient_server (RHOSSTRAT-981): Read-only OpenStack CLI commands
  - config_server          (RHOSSTRAT-980): OpenStack service configuration files
  - logs_server            (RHOSSTRAT-962): Log snippets by service/time/request
  - upgrade_server         (RHOSSTRAT-979): Upgrade readiness checks (orchestrates the above)

All return realistic mock data with deliberate problems so the agent has
something meaningful to reason about.

Used by try_rhoso_upgrade.py; not intended to run standalone.
"""

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# RHOSSTRAT-981: OpenStackClient MCP (Read-Only)
# ---------------------------------------------------------------------------

openstackclient_server = FastMCP(name="OpenStackClient (Read-Only)")


@openstackclient_server.tool()
def get_compute_services() -> list[dict]:
    """List nova-compute services with their state and status."""
    return [
        {
            "host": "compute-0.example.com",
            "binary": "nova-compute",
            "status": "enabled",
            "state": "up",
            "zone": "nova",
        },
        {
            "host": "compute-1.example.com",
            "binary": "nova-compute",
            "status": "enabled",
            "state": "up",
            "zone": "nova",
        },
        {
            "host": "compute-2.example.com",
            "binary": "nova-compute",
            "status": "disabled",
            "state": "up",
            "zone": "nova",
            "disabled_reason": "Maintenance scheduled",
        },
        {
            "host": "compute-3.example.com",
            "binary": "nova-compute",
            "status": "enabled",
            "state": "down",
            "zone": "nova",
        },
    ]


@openstackclient_server.tool()
def get_network_agents() -> list[dict]:
    """List neutron agents with alive status and admin state."""
    return [
        {
            "host": "compute-0.example.com",
            "agent_type": "OVN Controller agent",
            "alive": True,
            "admin_state_up": True,
        },
        {
            "host": "compute-1.example.com",
            "agent_type": "OVN Controller agent",
            "alive": True,
            "admin_state_up": True,
        },
        {
            "host": "compute-2.example.com",
            "agent_type": "OVN Controller agent",
            "alive": True,
            "admin_state_up": True,
        },
        {
            "host": "compute-3.example.com",
            "agent_type": "OVN Controller agent",
            "alive": False,
            "admin_state_up": True,
        },
        {
            "host": "networker-0.example.com",
            "agent_type": "OVN Metadata agent",
            "alive": True,
            "admin_state_up": True,
        },
    ]


@openstackclient_server.tool()
def get_server_list() -> list[dict]:
    """List all VMs with their status, host, and flavor."""
    return [
        {
            "id": "a1b2c3d4-0001",
            "name": "web-frontend-1",
            "status": "ACTIVE",
            "host": "compute-0.example.com",
            "flavor": "m1.large",
            "project": "production",
        },
        {
            "id": "a1b2c3d4-0002",
            "name": "web-frontend-2",
            "status": "ACTIVE",
            "host": "compute-0.example.com",
            "flavor": "m1.large",
            "project": "production",
        },
        {
            "id": "a1b2c3d4-0003",
            "name": "db-primary",
            "status": "ACTIVE",
            "host": "compute-1.example.com",
            "flavor": "m1.xlarge",
            "project": "production",
        },
        {
            "id": "a1b2c3d4-0004",
            "name": "db-replica",
            "status": "ACTIVE",
            "host": "compute-1.example.com",
            "flavor": "m1.xlarge",
            "project": "production",
        },
        {
            "id": "a1b2c3d4-0005",
            "name": "batch-worker-1",
            "status": "ACTIVE",
            "host": "compute-2.example.com",
            "flavor": "m1.medium",
            "project": "staging",
        },
        {
            "id": "a1b2c3d4-0006",
            "name": "test-vm-broken",
            "status": "ERROR",
            "host": "compute-3.example.com",
            "flavor": "m1.small",
            "project": "dev",
            "fault": "Host compute-3 is not available",
        },
        {
            "id": "a1b2c3d4-0007",
            "name": "monitoring-agent",
            "status": "SHUTOFF",
            "host": "compute-2.example.com",
            "flavor": "m1.small",
            "project": "ops",
        },
    ]


@openstackclient_server.tool()
def get_volume_list() -> list[dict]:
    """List volumes with their status and attachment info."""
    return [
        {
            "id": "vol-0001",
            "name": "web-data",
            "status": "in-use",
            "size_gb": 100,
            "attached_to": "a1b2c3d4-0001",
            "type": "ceph-ssd",
        },
        {
            "id": "vol-0002",
            "name": "db-primary-data",
            "status": "in-use",
            "size_gb": 500,
            "attached_to": "a1b2c3d4-0003",
            "type": "ceph-ssd",
        },
        {
            "id": "vol-0003",
            "name": "db-replica-data",
            "status": "in-use",
            "size_gb": 500,
            "attached_to": "a1b2c3d4-0004",
            "type": "ceph-ssd",
        },
        {
            "id": "vol-0004",
            "name": "batch-scratch",
            "status": "in-use",
            "size_gb": 200,
            "attached_to": "a1b2c3d4-0005",
            "type": "ceph-hdd",
        },
        {
            "id": "vol-0005",
            "name": "orphaned-volume",
            "status": "available",
            "size_gb": 50,
            "attached_to": None,
            "type": "ceph-ssd",
        },
        {
            "id": "vol-0006",
            "name": "stuck-volume",
            "status": "detaching",
            "size_gb": 100,
            "attached_to": "a1b2c3d4-0006",
            "type": "ceph-ssd",
        },
    ]


@openstackclient_server.tool()
def get_openstack_versions() -> dict:
    """Return current RHOSO and OpenStack component versions."""
    return {
        "rhoso_version": "18.0.3",
        "openstack_release": "Antelope",
        "target_rhoso_version": "19.0.0",
        "target_openstack_release": "Dalmatian",
        "openshift_version": "4.16.12",
        "operator_version": "openstack-operator.v1.0.3",
        "compute_nodes": 4,
        "control_plane_replicas": 3,
    }


@openstackclient_server.tool()
def get_hypervisor_stats() -> dict:
    """Show aggregate hypervisor resource usage across the cluster."""
    return {
        "count": 4,
        "vcpus_used": 22,
        "vcpus_total": 64,
        "memory_mb_used": 45056,
        "memory_mb_total": 131072,
        "local_disk_gb_used": 340,
        "local_disk_gb_total": 2000,
        "running_vms": 5,
    }


# ---------------------------------------------------------------------------
# RHOSSTRAT-980: RHOSO Configuration-Aware MCP
# ---------------------------------------------------------------------------

config_server = FastMCP(name="RHOSO Configuration")


@config_server.tool()
def get_nova_config() -> dict:
    """Fetch nova.conf configuration for the Nova compute service."""
    return {
        "service": "nova",
        "config_file": "/etc/nova/nova.conf",
        "sections": {
            "DEFAULT": {
                "transport_url": "rabbit://nova:****@rabbitmq.openstack.svc:5672/nova",
                "compute_driver": "libvirt.LibvirtDriver",
                "state_path": "/var/lib/nova",
                "log_dir": "/var/log/nova",
                "debug": "false",
                "cpu_allocation_ratio": "4.0",
                "ram_allocation_ratio": "1.0",
                "resume_guests_state_on_host_boot": "true",
            },
            "api": {
                "auth_strategy": "keystone",
            },
            "libvirt": {
                "virt_type": "kvm",
                "cpu_mode": "host-model",
                "inject_password": "false",
                "live_migration_uri": "qemu+ssh://%s/system",
            },
            "placement": {
                "auth_url": "https://keystone.openstack.svc:5000/v3",
                "auth_type": "password",
            },
            "upgrade_levels": {
                "compute": "auto",
            },
        },
    }


@config_server.tool()
def get_neutron_config() -> dict:
    """Fetch neutron configuration for the Neutron networking service."""
    return {
        "service": "neutron",
        "config_file": "/etc/neutron/neutron.conf",
        "sections": {
            "DEFAULT": {
                "core_plugin": "ml2",
                "service_plugins": "ovn-router,segments,trunk",
                "transport_url": "rabbit://neutron:****@rabbitmq.openstack.svc:5672/neutron",
                "debug": "false",
            },
            "ml2": {
                "type_drivers": "geneve,vlan,flat",
                "tenant_network_types": "geneve",
                "mechanism_drivers": "ovn",
            },
            "ovn": {
                "ovn_nb_connection": "tcp:ovndb-nb.openstack.svc:6641",
                "ovn_sb_connection": "tcp:ovndb-sb.openstack.svc:6642",
                "ovn_metadata_enabled": "true",
            },
        },
    }


@config_server.tool()
def get_cinder_config() -> dict:
    """Fetch cinder.conf configuration for the Block Storage service."""
    return {
        "service": "cinder",
        "config_file": "/etc/cinder/cinder.conf",
        "sections": {
            "DEFAULT": {
                "transport_url": "rabbit://cinder:****@rabbitmq.openstack.svc:5672/cinder",
                "enabled_backends": "ceph",
                "default_volume_type": "ceph-ssd",
                "debug": "false",
            },
            "ceph": {
                "volume_driver": "cinder.volume.drivers.rbd.RBDDriver",
                "rbd_pool": "volumes",
                "rbd_ceph_conf": "/etc/ceph/ceph.conf",
                "rbd_user": "openstack",
            },
        },
    }


@config_server.tool()
def get_openstackcontrolplane_cr() -> dict:
    """Fetch the OpenStackControlPlane custom resource from OpenShift."""
    return {
        "apiVersion": "core.openstack.org/v1beta1",
        "kind": "OpenStackControlPlane",
        "metadata": {
            "name": "openstack-control-plane",
            "namespace": "openstack",
        },
        "spec": {
            "storageClass": "ocs-storagecluster-ceph-rbd",
            "secret": "osp-secret",
            "nova": {
                "enabled": True,
                "template": {
                    "cellTemplates": {
                        "cell0": {"hasCompute": False},
                        "cell1": {"hasCompute": True},
                    }
                },
            },
            "neutron": {"enabled": True},
            "cinder": {"enabled": True},
            "glance": {"enabled": True},
            "keystone": {"enabled": True},
            "placement": {"enabled": True},
            "horizon": {"enabled": True},
        },
        "status": {
            "conditions": [
                {"type": "Ready", "status": "True"},
                {"type": "InputReady", "status": "True"},
            ]
        },
    }


# ---------------------------------------------------------------------------
# RHOSSTRAT-962: RHOSO Logs MCP
# ---------------------------------------------------------------------------

logs_server = FastMCP(name="RHOSO Logs")


@logs_server.tool()
def get_service_logs(service: str, lines: int = 50) -> dict:
    """Fetch recent log lines from an OpenStack service.

    Args:
        service: OpenStack service name (nova, neutron, cinder, keystone, etc.)
        lines: Number of recent log lines to return (default 50).
    """
    log_samples = {
        "nova": [
            "2026-06-10 14:23:01 INFO nova.compute.manager [req-abc123] Instance a1b2c3d4-0006 failed to spawn on compute-3: Host not available",
            "2026-06-10 14:23:01 ERROR nova.compute.manager [req-abc123] BuildAbortException: Build of instance a1b2c3d4-0006 aborted",
            "2026-06-10 14:25:15 WARNING nova.scheduler.host_manager Host compute-3.example.com has not reported in 120 seconds",
            "2026-06-10 14:30:00 INFO nova.compute.resource_tracker [req-def456] Compute node compute-0: vcpus=16 used=8, memory_mb=32768 used=16384",
            "2026-06-10 14:30:00 INFO nova.compute.resource_tracker [req-ghi789] Compute node compute-1: vcpus=16 used=10, memory_mb=32768 used=24576",
            "2026-06-10 14:30:00 WARNING nova.compute.resource_tracker [req-jkl012] Compute node compute-3: no resource update received",
        ],
        "neutron": [
            "2026-06-10 14:20:00 INFO neutron.plugins.ml2.drivers.ovn OVN Controller on compute-0 is healthy",
            "2026-06-10 14:20:00 INFO neutron.plugins.ml2.drivers.ovn OVN Controller on compute-1 is healthy",
            "2026-06-10 14:20:00 WARNING neutron.plugins.ml2.drivers.ovn OVN Controller on compute-3 has not reported since 2026-06-10 14:05:00",
            "2026-06-10 14:25:30 ERROR neutron.agent.ovn.metadata Agent on compute-3 is unreachable",
        ],
        "cinder": [
            "2026-06-10 14:22:00 WARNING cinder.volume.manager Volume vol-0006 stuck in 'detaching' state for 45 minutes",
            "2026-06-10 14:22:00 INFO cinder.volume.manager Attempting automatic cleanup for stale volume operations",
            "2026-06-10 14:22:01 ERROR cinder.volume.manager Cannot clean up vol-0006: attached instance a1b2c3d4-0006 is in ERROR state",
        ],
    }

    logs = log_samples.get(service, [f"No logs available for service: {service}"])
    return {
        "service": service,
        "lines_requested": lines,
        "lines_returned": len(logs),
        "logs": logs[:lines],
    }


@logs_server.tool()
def search_logs(query: str, service: str = "all") -> dict:
    """Search logs across OpenStack services for a keyword or request ID.

    Args:
        query: Search string (keyword, request ID, instance ID, etc.)
        service: Limit search to a specific service, or 'all' for all services.
    """
    all_matches = [
        {
            "service": "nova",
            "timestamp": "2026-06-10 14:23:01",
            "level": "ERROR",
            "message": "BuildAbortException: Build of instance a1b2c3d4-0006 aborted",
            "request_id": "req-abc123",
        },
        {
            "service": "nova",
            "timestamp": "2026-06-10 14:25:15",
            "level": "WARNING",
            "message": "Host compute-3.example.com has not reported in 120 seconds",
            "request_id": "req-mno345",
        },
        {
            "service": "neutron",
            "timestamp": "2026-06-10 14:25:30",
            "level": "ERROR",
            "message": "Agent on compute-3 is unreachable",
            "request_id": "req-pqr678",
        },
        {
            "service": "cinder",
            "timestamp": "2026-06-10 14:22:01",
            "level": "ERROR",
            "message": "Cannot clean up vol-0006: attached instance a1b2c3d4-0006 is in ERROR state",
            "request_id": "req-stu901",
        },
    ]

    matches = [
        m
        for m in all_matches
        if query.lower() in m["message"].lower()
        or query.lower() in m.get("request_id", "").lower()
        or query.lower() in m.get("service", "").lower()
    ]
    if service != "all":
        matches = [m for m in matches if m["service"] == service]

    return {
        "query": query,
        "service_filter": service,
        "matches_found": len(matches),
        "results": matches,
    }


# ---------------------------------------------------------------------------
# RHOSSTRAT-979: RHOSO Upgrade Assistance MCP
# Orchestrates the above MCPs for upgrade-specific checks.
# ---------------------------------------------------------------------------

upgrade_server = FastMCP(name="RHOSO Upgrade Assistance")


@upgrade_server.tool()
def run_pre_upgrade_check() -> dict:
    """Run an aggregated pre-flight check for upgrade readiness.

    Analyzes compute services, network agents, VM states, and volumes
    to identify blockers and warnings before starting an upgrade.
    """
    return {
        "ready": False,
        "blockers": [
            {
                "severity": "critical",
                "category": "compute",
                "message": "compute-3.example.com nova-compute is DOWN",
                "action": "Investigate and restore the compute service, or remove the node from the cluster",
            },
            {
                "severity": "critical",
                "category": "instances",
                "message": "1 VM in ERROR state: test-vm-broken (a1b2c3d4-0006) on compute-3",
                "action": "Delete or reset the error VM before upgrading",
            },
            {
                "severity": "high",
                "category": "volumes",
                "message": "1 volume stuck in 'detaching' state: stuck-volume (vol-0006)",
                "action": "Force-reset volume state: openstack volume set --state available vol-0006",
            },
        ],
        "warnings": [
            {
                "severity": "medium",
                "category": "compute",
                "message": "compute-2.example.com is disabled (reason: Maintenance scheduled)",
                "action": "Confirm this is intentional; re-enable after upgrade if needed",
            },
            {
                "severity": "low",
                "category": "instances",
                "message": "1 VM in SHUTOFF state: monitoring-agent on compute-2",
                "action": "No action required, but verify if it should be running",
            },
        ],
        "healthy_checks": [
            "compute-0 and compute-1: nova-compute UP and enabled",
            "All OVN Controller agents alive on compute-0, compute-1, compute-2",
            "OVN Metadata agent alive on networker-0",
            "5 volumes healthy (4 in-use, 1 available)",
            "OpenShift cluster version 4.16.12 meets minimum requirement",
        ],
    }


@upgrade_server.tool()
def check_version_compatibility() -> dict:
    """Check if the current deployment version is compatible with the target upgrade."""
    return {
        "current": {
            "rhoso": "18.0.3",
            "openstack": "Antelope",
            "openshift": "4.16.12",
        },
        "target": {
            "rhoso": "19.0.0",
            "openstack": "Dalmatian",
            "openshift_minimum": "4.16.0",
        },
        "compatible": True,
        "notes": [
            "Direct upgrade from RHOSO 18 to 19 is supported",
            "OpenShift 4.16.12 meets minimum requirement (4.16.0)",
            "Skip-level upgrades (e.g. 17 -> 19) are NOT supported",
        ],
        "deprecated_configs": [
            {
                "service": "nova",
                "option": "force_config_drive",
                "section": "DEFAULT",
                "action": "Remove from nova.conf; config drive is now always used",
            },
            {
                "service": "neutron",
                "option": "allow_overlapping_ips",
                "section": "DEFAULT",
                "action": "Remove; overlapping IPs are always allowed in Dalmatian",
            },
        ],
    }


@upgrade_server.tool()
def get_upgrade_plan(source_version: str, target_version: str) -> dict:
    """Generate a step-by-step upgrade plan for the specified version transition.

    Args:
        source_version: Current RHOSO version (e.g., '18').
        target_version: Target RHOSO version (e.g., '19').
    """
    return {
        "source": source_version,
        "target": target_version,
        "estimated_duration": "2-4 hours (depends on cluster size)",
        "phases": [
            {
                "phase": 1,
                "name": "Backup",
                "steps": [
                    "Back up MariaDB/Galera database",
                    "Back up etcd on OpenShift cluster",
                    "Export OpenStackControlPlane CR as YAML",
                    "Export OpenStackDataPlaneNodeSet CRs",
                ],
                "estimated_time": "15-30 minutes",
            },
            {
                "phase": 2,
                "name": "Pre-flight Validation",
                "steps": [
                    "Run run_pre_upgrade_check and resolve all blockers",
                    "Verify OpenShift cluster health (oc get nodes, oc get co)",
                    "Check deprecated configuration options",
                    "Verify CRD compatibility with target version",
                ],
                "estimated_time": "15-30 minutes",
            },
            {
                "phase": 3,
                "name": "Control Plane Upgrade",
                "steps": [
                    "Update OpenStack operator subscription channel to stable-1.1",
                    "Wait for operator CSV to reach Succeeded phase",
                    "Monitor control plane pod restarts",
                    "Verify API endpoints respond (openstack endpoint list)",
                    "Run database schema migrations if needed",
                ],
                "estimated_time": "30-60 minutes",
            },
            {
                "phase": 4,
                "name": "Data Plane Upgrade (Rolling)",
                "steps": [
                    "For each compute node:",
                    "  1. Disable nova-compute service",
                    "  2. Live-migrate all VMs to other nodes",
                    "  3. Update OpenStackDataPlaneNodeSet for the node",
                    "  4. Create OpenStackDataPlaneDeployment",
                    "  5. Wait for deployment to complete",
                    "  6. Re-enable nova-compute service",
                ],
                "estimated_time": "30-60 minutes per node",
            },
            {
                "phase": 5,
                "name": "Post-Upgrade Verification",
                "steps": [
                    "Confirm all compute and network services are up",
                    "Launch test VM and verify end-to-end functionality",
                    "Test volume attach/detach",
                    "Verify networking (floating IPs, security groups)",
                    "Clean up test resources",
                ],
                "estimated_time": "15-30 minutes",
            },
        ],
    }
