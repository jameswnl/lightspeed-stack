# RHOSO Upgrade Procedures: 18 to 19

## Prerequisites

- OpenShift 4.16+ cluster with RHOSO 18 installed
- `oc` CLI authenticated with cluster-admin privileges
- `openstack` CLI configured with admin credentials
- Sufficient compute capacity for live migration during rolling upgrades

## Step 1: Back Up Critical Data

### OpenStack Database (MariaDB/Galera)

```bash
# Create a database backup job
oc create -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: mariadb-backup
  namespace: openstack
spec:
  template:
    spec:
      containers:
      - name: backup
        image: registry.redhat.io/rhoso/openstack-mariadb-rhel9:latest
        command: ["bash", "-c", "mysqldump --all-databases > /backup/openstack-db-backup.sql"]
        volumeMounts:
        - name: backup-volume
          mountPath: /backup
      volumes:
      - name: backup-volume
        persistentVolumeClaim:
          claimName: db-backup-pvc
      restartPolicy: Never
EOF
```

### etcd Backup

```bash
# Take an etcd snapshot on the OpenShift cluster
oc debug node/<control-plane-node> -- \
  chroot /host /usr/local/bin/cluster-backup.sh /home/core/backup
```

### Export OpenStack CRs

```bash
oc get openstackcontrolplane -n openstack -o yaml > controlplane-backup.yaml
oc get openstackdataplanenodeset -n openstack -o yaml > dataplane-backup.yaml
```

## Step 2: Pre-flight Validation

```bash
# Check compute services
openstack compute service list --format json | \
  jq '.[] | select(.Status != "enabled" or .State != "up")'

# Check network agents
openstack network agent list --format json | \
  jq '.[] | select(.Alive != true or ."Admin State" != true)'

# Check for error VMs
openstack server list --all-projects --status ERROR --format json

# Check volume health
openstack volume list --all-projects --format json | \
  jq '.[] | select(.Status == "error" or .Status == "attaching" or .Status == "detaching")'

# Check OpenShift cluster health
oc get nodes
oc get clusteroperators | grep -v "True.*False.*False"
```

## Step 3: Update the OpenStack Operator

```bash
# Verify current operator version
oc get csv -n openstack-operators | grep openstack

# Update the subscription to the RHOSO 19 channel
oc patch subscription openstack-operator -n openstack-operators \
  --type merge -p '{"spec":{"channel":"stable-1.1"}}'

# Wait for the operator to update
oc get csv -n openstack-operators -w
```

## Step 4: Upgrade Control Plane

```bash
# The operator will reconcile the OpenStackControlPlane CR automatically.
# Monitor the rollout:
oc get pods -n openstack -w

# Verify API endpoints
openstack endpoint list
openstack catalog list

# Check control plane service health
openstack compute service list
openstack network agent list
openstack volume service list
```

## Step 5: Rolling Data Plane Upgrade

For each compute node:

```bash
HOST="compute-0.example.com"

# 1. Disable compute service
openstack compute service set --disable --disable-reason "Upgrading to RHOSO 19" \
  "$HOST" nova-compute

# 2. Migrate all VMs off the node
for SERVER_ID in $(openstack server list --host "$HOST" --all-projects -f value -c ID); do
  echo "Migrating $SERVER_ID..."
  openstack server migrate --live-migration "$SERVER_ID"
done

# 3. Wait for migrations
watch "openstack server list --host $HOST --all-projects -f value -c ID | wc -l"

# 4. Upgrade the dataplane nodeset for this node
oc patch openstackdataplanenodeset compute-nodes -n openstack \
  --type merge -p '{"spec":{"nodeTemplate":{"ansible":{"ansibleVars":{"edpm_override_host":"'"$HOST"'"}}}}}'

# 5. Trigger a deployment
oc create -f - <<EOF
apiVersion: dataplane.openstack.org/v1beta1
kind: OpenStackDataPlaneDeployment
metadata:
  name: upgrade-${HOST%%.*}
  namespace: openstack
spec:
  nodeSets:
  - compute-nodes
EOF

# 6. Wait for deployment to complete
oc wait openstackdataplanedeployment "upgrade-${HOST%%.*}" \
  -n openstack --for=condition=Ready --timeout=30m

# 7. Re-enable compute service
openstack compute service set --enable "$HOST" nova-compute
```

## Step 6: Post-Upgrade Verification

```bash
# Verify all compute services
openstack compute service list

# Verify all network agents
openstack network agent list

# Launch test VM
openstack server create --flavor m1.small --image cirros \
  --network private-net --wait test-upgrade-vm

# Verify networking
openstack floating ip create external-net
openstack server add floating ip test-upgrade-vm <floating-ip>
ping -c 3 <floating-ip>

# Clean up test resources
openstack server delete test-upgrade-vm
openstack floating ip delete <floating-ip>
```

## Rollback

If the upgrade fails mid-way:

1. **Control plane rollback**: Revert the operator subscription channel and restore the OpenStackControlPlane CR from backup
2. **Data plane rollback**: The dataplane nodes that haven't been upgraded yet remain on RHOSO 18 and continue functioning
3. **Database rollback**: Restore the MariaDB backup if data corruption is suspected
