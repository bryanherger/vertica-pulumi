# Vertica Cluster Operations Guide

This guide covers day-to-day operations for managing a Vertica cluster deployed with Pulumi.

## Table of Contents

- [Scaling the Cluster](#scaling-the-cluster)
- [Upgrading Vertica](#upgrading-vertica)
- [Backup and Restore](#backup-and-restore)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

---

## Scaling the Cluster

### Adding Nodes

**Enterprise Mode (EE):**

```bash
# On primary node, as dbadmin
/opt/vertica/bin/adminTools -t db_add_node \
  -d analytics \
  -p 'YOUR_PASSWORD' \
  -s new-node-ip

# Rebalance data across all nodes
/opt/vertica/bin/adminTools -t rebalance_data \
  -d analytics \
  -p 'YOUR_PASSWORD'
```

**Eon Mode:**

```bash
# Add node to cluster
vcluster add_node \
  --db-name analytics \
  --hosts new-node-ip \
  --communal-storage-location s3://bucket/path \
  --username dbadmin \
  --password 'YOUR_PASSWORD'

# In Eon Mode, data is automatically rebalanced via shard reorganization
# No manual rebalance needed
```

**Pulumi Integration:**

To add nodes via infrastructure:

1. Update `config/config.yaml`:
   ```yaml
   compute:
     aws:
       node_count: 5  # Changed from 3
   ```

2. Run Pulumi update:
   ```bash
   pulumi up
   ```

3. Run the appropriate add_node command above

### Removing Nodes

**Enterprise Mode:**

```bash
# Remove node (data is redistributed automatically)
/opt/vertica/bin/adminTools -t db_remove_node \
  -d analytics \
  -p 'YOUR_PASSWORD' \
  -s node-ip-to-remove
```

**Eon Mode:**

```bash
vcluster remove_node \
  --db-name analytics \
  --hosts node-ip-to-remove \
  --username dbadmin \
  --password 'YOUR_PASSWORD'
```

**IMPORTANT:** In Eon Mode, removing nodes may affect shard coverage. Ensure you have enough nodes for your shard count.

---

## Upgrading Vertica

### Pre-Upgrade Checklist

1. **Backup the database** (see [Backup and Restore](#backup-and-restore))
2. Verify all nodes are UP: `SELECT * FROM nodes;`
3. Check current version: `SELECT version();`
4. Review release notes for breaking changes

### Upgrade Process

**Enterprise Mode:**

```bash
# On each node, as root
rpm -Uvh vertica-NEWVERSION.x86_64.rpm

# Restart the database
/opt/vertica/bin/adminTools -t stop_db -d analytics
/opt/vertica/bin/adminTools -t start_db -d analytics
```

**Eon Mode:**

```bash
# Upgrade RPM on all nodes
# Then restart database - Eon Mode handles catalog sync automatically
vcluster stop_db --db-name analytics
vcluster start_db --db-name analytics
```

### Rolling Upgrade (Minimize Downtime)

For Enterprise Mode with K-Safety > 0:

```bash
# Upgrade one node at a time
/opt/vertica/bin/adminTools -t upgrade_node \
  -d analytics \
  -s node1-ip \
  -p 'PASSWORD'

# Wait for node to come back UP
# Repeat for each node
```

---

## Backup and Restore

### Enterprise Mode Backups

**Full Backup:**

```bash
# Create backup configuration
/opt/vertica/bin/vbr -t init -c /tmp/backup_config.ini

# Edit config to specify backup location
# Then run backup
/opt/vertica/bin/vbr -t backup -c /tmp/backup_config.ini
```

**Restore:**

```bash
# Stop database first
/opt/vertica/bin/adminTools -t stop_db -d analytics

# Restore from backup
/opt/vertica/bin/vbr -t restore -c /tmp/backup_config.ini

# Start database
/opt/vertica/bin/adminTools -t start_db -d analytics
```

### Eon Mode Backups

**Key Concept:** In Eon Mode, S3 communal storage IS the backup. The data is already durable. However, you may want:

- **Snapshot backups** for point-in-time recovery
- **Cross-region replication** for disaster recovery

**S3 Snapshot (Manual):**

```bash
# Create timestamped snapshot
aws s3 sync s3://bucket/analytics s3://bucket/analytics-backup-$(date +%Y%m%d)
```

**Cross-Region Replication:**

```bash
# Set up replication via AWS CLI
aws s3api put-bucket-replication \
  --bucket vertica-primary \
  --replication-configuration file://replication-config.json
```

### Automated Backups

Create a cron job on the primary node:

```bash
# Edit crontab
sudo crontab -e

# Add daily backup at 2 AM
0 2 * * * /opt/vertica/bin/vbr -t backup -c /etc/vertica/backup.ini >> /var/log/vertica-backup.log 2>&1
```

For Eon Mode, use AWS S3 lifecycle policies (see `docs/s3-lifecycle.json`).

---

## Monitoring

### Database Health Checks

```sql
-- Node status
SELECT node_name, node_state, node_address FROM nodes;

-- Resource usage
SELECT * FROM v_monitor.system_resource_usage;

-- Active queries
SELECT * FROM v_monitor.sessions;

-- Storage usage
SELECT * FROM v_monitor.storage_usage;
```

### System Metrics

```bash
# On each node, check Vertica processes
ps aux | grep vertica

# Disk usage
df -h /data/depot /data/catalog

# Memory usage
free -h

# Network connections
ss -tlnp | grep 5433
```

### CloudWatch Integration (AWS)

Set up CloudWatch alarms for:
- CPU utilization > 80%
- Disk space < 20% free
- Memory usage > 90%
- Network errors

```bash
# Create alarm via AWS CLI
aws cloudwatch put-metric-alarm \
  --alarm-name vertica-high-cpu \
  --metric-name CPUUtilization \
  --namespace AWS/EC2 \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=InstanceId,Value=i-1234567890abcdef0
```

---

## Troubleshooting

### Node Down

```bash
# Check node status
/opt/vertica/bin/adminTools -t db_status -s UP

# If node is DOWN, try restarting
/opt/vertica/bin/adminTools -t restart_node -d analytics -s node-ip

# If that fails, check logs
sudo tail -100 /opt/vertica/log/adminTools.log
sudo tail -100 /opt/vertica/log/vertica.log
```

### Database Won't Start

```bash
# Check for locked processes
sudo lsof -i :5433

# Check disk space
df -h

# Verify communal storage access (Eon Mode)
aws s3 ls s3://your-bucket/path/

# Force start if needed
/opt/vertica/bin/adminTools -t start_db -d analytics --force
```

### Slow Queries

```sql
-- Identify long-running queries
SELECT * FROM v_monitor.query_plans WHERE is_executing = 't';

-- Check for resource contention
SELECT * FROM v_monitor.resource_rejection_reasons;

-- Analyze query performance
SELECT query_duration_us, resource_acquisition_us, execution_us 
FROM v_monitor.query_profiles 
ORDER BY query_duration_us DESC LIMIT 10;
```

### Disk Full

```bash
# Check what's using space
du -sh /data/* | sort -hr

# In Eon Mode, clean old depot data (Vertica manages this, but you can force)
# In Enterprise Mode, consider adding nodes or archiving data
```

### Network Issues

```bash
# Test connectivity between nodes
for ip in 10.0.1.10 10.0.1.11 10.0.1.12; do
  nc -zv $ip 5433
  nc -zv $ip 22
done

# Check security group rules
aws ec2 describe-security-groups --group-ids sg-xxxxxxxx
```

---

## Emergency Procedures

### Complete Cluster Recovery

1. **Don't panic** - data is likely safe in S3 (Eon) or backups (EE)
2. Redeploy infrastructure with Pulumi
3. For Eon: use `revive_db` to restore from S3
4. For EE: restore from latest backup

### Contact Information

- Vertica Documentation: https://docs.vertica.com
- Vertica Support: https://www.vertica.com/support
- This Project Issues: https://github.com/YOURNAME/vertica-pulumi/issues
