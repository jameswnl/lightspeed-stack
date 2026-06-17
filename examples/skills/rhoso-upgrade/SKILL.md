---
name: rhoso-upgrade
description: Guide administrators through RHOSO (Red Hat OpenStack Services on OpenShift) cluster upgrades. Use when users ask about upgrading RHOSO, pre-upgrade checks, OpenStack upgrade procedures, or troubleshooting upgrade failures.
---

# RHOSO Upgrade Assistant

## When to use this skill

Use this skill when:
- A user wants to upgrade their RHOSO cluster (e.g., RHOSO 18 to RHOSO 19)
- A user needs to run pre-upgrade checks or validate cluster health before upgrading
- A user asks about the upgrade workflow, sequencing, or rollback procedures
- A user encounters errors during an OpenStack or RHOSO upgrade
- A user needs help migrating workloads off a compute node for maintenance

## Pre-Upgrade Checklist

Before starting any RHOSO upgrade, verify all of these:

1. **Compute services healthy** — all nova-compute services should be `up` and `enabled`
2. **Network agents alive** — all neutron agents (L3, DHCP, OVN, metadata) should report `alive: true`
3. **No error-state VMs** — resolve any VMs in `ERROR` state before proceeding
4. **No stuck volumes** — volumes should not be in `error`, `attaching`, or `detaching` state
5. **Backups current** — database backups of MariaDB/Galera and etcd taken within 24 hours
6. **CRD compatibility** — verify OpenStack CRDs match the target RHOSO version
7. **Sufficient capacity** — enough headroom to live-migrate VMs during rolling compute upgrades

## Upgrade Workflow

The standard RHOSO upgrade follows this sequence:

### Phase 1: Backup
- Back up the OpenStack database (MariaDB/Galera)
- Back up etcd on the OpenShift cluster
- Export current OpenStackControlPlane CR as YAML

### Phase 2: Pre-flight Validation
- Run `run_pre_upgrade_check` to identify blockers
- Resolve any issues found (error VMs, down services, stuck volumes)
- Verify OpenShift cluster health (`oc get nodes`, `oc get co`)

### Phase 3: Control Plane Upgrade
- Update the OpenStack operator subscription to the target channel
- Wait for the operator to reconcile
- Verify control plane pods restart successfully
- Confirm API endpoints respond (`openstack endpoint list`)

### Phase 4: Data Plane Upgrade
- Upgrade compute nodes one at a time (rolling)
- For each compute node:
  1. Disable the nova-compute service: `openstack compute service set --disable <host> nova-compute`
  2. Live-migrate VMs off the node: `openstack server migrate --live-migration --host <dest> <server>`
  3. Wait for migrations to complete
  4. Upgrade the OpenStackDataPlaneNodeSet CR for that node
  5. Verify the node comes back healthy
  6. Re-enable the compute service: `openstack compute service set --enable <host> nova-compute`

### Phase 5: Post-Upgrade Verification
- Confirm all services are up: `openstack compute service list`, `openstack network agent list`
- Launch a test VM to validate end-to-end functionality
- Check Cinder volumes can attach/detach
- Verify Neutron networking (floating IPs, security groups)

## Common Pitfalls

- **Skipping pre-flight checks** — upgrading with error-state VMs or down agents causes cascading failures
- **Upgrading all compute nodes simultaneously** — always do rolling upgrades to maintain capacity
- **Forgetting CRD updates** — OpenStack CRDs must be updated before the operator reconciles
- **Ignoring deprecated configs** — RHOSO 19 drops several deprecated configuration options; check release notes
- **Not backing up** — database corruption during upgrade is rare but unrecoverable without backups

## CLI Command Patterns

When generating `openstack` CLI commands for users:
- Always specify the resource type explicitly (e.g., `openstack server list`, not `openstack list`)
- Use `--format json` or `--format value` when the output needs to be parsed
- For batch operations, show the loop pattern: `for server in $(openstack server list --host <host> -f value -c ID); do ... done`
- Include `--os-cloud` if the user has multiple cloud configs

## Escalation

Escalate to a human operator when:
- The OpenShift cluster itself is unhealthy (node NotReady, degraded cluster operators)
- Database corruption is suspected
- The upgrade has been partially applied and is in an inconsistent state
- Custom operators or CRDs conflict with the upgrade

See [references/upgrade-procedures.md](references/upgrade-procedures.md) for detailed step-by-step procedures and [references/common-issues.md](references/common-issues.md) for known issues and resolutions.
