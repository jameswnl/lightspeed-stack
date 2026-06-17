# Common RHOSO Upgrade Issues

## Operator Stuck in Pending

**Symptoms**: The OpenStack operator CSV stays in `Pending` or `InstallReady` after updating the subscription channel.

**Causes**:
- OLM catalog source not refreshed
- Dependency resolution conflict with another operator
- Insufficient cluster resources for the new operator pod

**Resolution**:
1. Refresh the catalog source: `oc delete catalogsource redhat-operators -n openshift-marketplace` (it will be recreated)
2. Check operator dependencies: `oc get csv -n openstack-operators -o json | jq '.items[].spec.installModes'`
3. Check pod scheduling: `oc get pods -n openstack-operators | grep Pending` then `oc describe pod <pod>`

## Live Migration Failures

**Symptoms**: VMs fail to live-migrate during rolling compute upgrade, reporting `NoValidHost` or `MigrationError`.

**Causes**:
- Destination host has insufficient resources
- CPU model incompatibility between source and destination
- libvirt/QEMU version mismatch between upgraded and non-upgraded nodes

**Resolution**:
1. Check available capacity: `openstack hypervisor stats show`
2. For CPU incompatibility, use `openstack server migrate --live-migration --block-migration <server>` (copies disk, avoids shared storage issues)
3. Migrate to a node with the same upgrade status (both old or both new)
4. As a last resort, cold-migrate: `openstack server migrate <server>` then `openstack server resize-confirm <server>`

## Control Plane Pods CrashLoopBackOff After Upgrade

**Symptoms**: One or more control plane pods enter CrashLoopBackOff after the operator reconciles.

**Causes**:
- Deprecated configuration options in the OpenStackControlPlane CR
- Database schema migration failure
- Secret or ConfigMap incompatibility

**Resolution**:
1. Check pod logs: `oc logs <pod> -n openstack --previous`
2. Check for deprecated config: compare your CR against the RHOSO 19 sample CRs
3. Check database migration status: `oc logs <db-sync-job> -n openstack`
4. If a specific service fails, check its config: `oc get configmap <service>-config -n openstack -o yaml`

## Neutron Agents Not Reconnecting

**Symptoms**: After upgrading a compute node, the neutron OVN agent or metadata agent does not report `alive`.

**Causes**:
- OVN controller not restarted after EDPM node upgrade
- Firewall rules blocking OVN southbound DB connection
- Certificate rotation needed

**Resolution**:
1. Restart OVN controller on the node: `ssh <node> sudo systemctl restart ovn-controller`
2. Check OVN SB DB connectivity: `ssh <node> sudo ovs-vsctl get open-vswitch . external_ids:ovn-remote`
3. Verify certificates: `oc get secret ovn-ca-cert -n openstack -o jsonpath='{.data.ca\.crt}' | base64 -d | openssl x509 -noout -dates`

## Cinder Volumes Stuck in Attaching/Detaching

**Symptoms**: Volumes remain in `attaching` or `detaching` state after the upgrade.

**Causes**:
- The Cinder volume service was interrupted during an attach/detach operation
- Stale locks in the database

**Resolution**:
1. Reset the volume state: `openstack volume set --state available <volume-id>`
2. If attached to a VM in ERROR state, force-detach: `openstack server remove volume <server-id> <volume-id>`
3. Check Cinder scheduler and volume services: `openstack volume service list`

## CRD Version Mismatch

**Symptoms**: The operator fails to reconcile with errors about unknown fields or API version incompatibility.

**Causes**:
- Custom Resource Definitions (CRDs) from RHOSO 18 are incompatible with the RHOSO 19 operator
- CRDs were not updated as part of the operator upgrade

**Resolution**:
1. Check CRD versions: `oc get crd | grep openstack`
2. Manually apply updated CRDs if needed: `oc apply -f <crds-from-rhoso19>`
3. Verify the operator can read existing CRs: `oc get openstackcontrolplane -n openstack`
