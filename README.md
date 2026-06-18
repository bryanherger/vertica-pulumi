# Pulumi Vertica Cluster

Complete lifecycle management for Vertica clusters using Pulumi and the vcluster CLI.

## Overview

This project provides:

- **Infrastructure as Code**: Provision AWS EC2 instances, VPCs, security groups using Pulumi
- **Database Lifecycle**: Create, revive, start, stop, and drop Vertica databases via vcluster CLI
- **Node Management**: Add, remove, restart individual nodes
- **Subcluster Management**: Add, remove, rename subclusters for workload isolation
- **Status Monitoring**: Check database and node status
- **Cost Management**: Stop/terminate instances when not needed

## Supported vcluster CLI Operations

### Database Lifecycle
- `create_db` - Create a new database (Enterprise or Eon Mode)
- `revive_db` - Revive an Eon Mode database from communal storage
- `start_db` - Start a stopped database
- `stop_db` - Stop a running database
- `drop_db` - Drop (delete) a database

### Node Management
- `add_node` - Add a new node to the database
- `remove_node` - Remove a node from the database
- `restart_node` - Restart a single node
- `stop_node` - Stop a single node
- `start_node` - Start a single node

### Subcluster Management
- `add_subcluster` - Add a new subcluster
- `remove_subcluster` - Remove a subcluster
- `stop_subcluster` - Stop all nodes in a subcluster
- `start_subcluster` - Start all nodes in a subcluster
- `rename_subcluster` - Rename a subcluster

### Status & Information
- `list_all_db` - List all databases
- `db_status` - Get database status
- `node_status` - Get node status
- `show_cluster` - Show cluster configuration
- `list_node` - List all nodes

### Maintenance
- `re_ip` - Reconfigure IP addresses
- `revoke` - Revoke node trust
- `manage_config` / `show_config` - Configuration management
- `set_config_parameter` - Set configuration parameters

## Architecture

```
pulumi-vertica-cluster/
├── __main__.py                          # Main Pulumi program
├── Pulumi.yaml                          # Pulumi project config
├── requirements.txt                     # Python dependencies
├── config/
│   └── config.yaml                      # Vertica cluster configuration
├── modules/
│   ├── __init__.py
│   ├── cluster_management.py            # Lifecycle manager (create/destroy/scale)
│   ├── compute/
│   │   ├── __init__.py
│   │   ├── base.py                      # Abstract compute provider interface
│   │   └── aws.py                       # AWS EC2 implementation
│   └── vertica/
│       ├── __init__.py
│       ├── vcluster.py                  # Full vcluster CLI wrapper
│       ├── rest_api.py                  # Vertica REST API client
│       ├── install.py                   # Installation helpers
│       ├── configure.py                 # Configuration generation
│       └── pulumi_vertica_resources.py  # Pulumi dynamic resources
├── scripts/
│   └── vertica-cli.py                   # CLI tool for operations
└── tests/
    └── test_compute.py                  # Unit tests
```

## Quick Start

### Eon Mode on AWS (recommended)

For a complete walkthrough including Pulumi installation, S3 bucket setup, Vertica RPM/license staging, and a 3-node Eon Mode deployment, see:

**[README_EON.md](README_EON.md)**

### Enterprise Mode (quick local test)

1. Install dependencies:

   ```bash
   # Install Pulumi first: https://www.pulumi.com/docs/install/
   curl -fsSL https://get.pulumi.com | sh

   cd pulumi-vertica-cluster
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configure:

   Copy and edit `config/vertica-cluster.yaml.example`:

   ```bash
   cp config/vertica-cluster.yaml.example config/config.yaml
   ```

   ```yaml
   vertica:
     cluster_name: my-vertica-cluster
     version: "24.1"
     database:
       name: analytics
       admin_username: dbadmin
       admin_password: SecurePassword123!
       shard_count: 6
     nodes:
       data_path: /data/vertica
       catalog_path: /data/catalog
       depot_path: /data/depot
     network:
       port: 5433
       rest_api_port: 5444

   aws:
     region: us-east-1
     instance_type: r6i.2xlarge
     key_name: my-ssh-key
     tags:
       Project: vertica-analytics
       Environment: production
   ```

3. Set Pulumi config:

   ```bash
   pulumi stack init dev
   pulumi config set cluster_name my-vertica-cluster
   pulumi config set node_count 3
   pulumi config set instance_type r6i.2xlarge
   pulumi config set db_name analytics
   pulumi config set --secret db_password SecurePassword123!
   ```

4. Deploy:

   ```bash
   pulumi preview
   pulumi up
   ```

5. Create the database:

   ```bash
   python scripts/install_vertica.py \
       --config config/config.yaml \
       --ssh-key ~/.ssh/my-ssh-key.pem
   ```

## CLI Usage

### Database Lifecycle

```bash
# Create database
python scripts/vertica-cli.py create-db --hosts 10.0.1.10,10.0.1.11,10.0.1.12 --db-name analytics --wait

# Start database
python scripts/vertica-cli.py start-db --hosts 10.0.1.10,10.0.1.11,10.0.1.12 --db-name analytics

# Stop database
python scripts/vertica-cli.py stop-db --hosts 10.0.1.10,10.0.1.11,10.0.1.12 --db-name analytics

# Drop database
python scripts/vertica-cli.py drop-db --hosts 10.0.1.10,10.0.1.11,10.0.1.12 --db-name analytics --force

# Revive Eon Mode database
python scripts/vertica-cli.py revive-db \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
  --db-name analytics \
  --communal-path s3://my-bucket/vertica/communal
```

### Node Management

```bash
# Add node
python scripts/vertica-cli.py add-node \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
  --new-host 10.0.1.13

# Remove node
python scripts/vertica-cli.py remove-node \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12,10.0.1.13 \
  --remove-host 10.0.1.13

# Restart node
python scripts/vertica-cli.py restart-node \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
  --node 10.0.1.11
```

### Subcluster Management

```bash
# Add subcluster
python scripts/vertica-cli.py add-subcluster \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
  --name reporting \
  --sc-hosts 10.0.1.13,10.0.1.14

# Remove subcluster
python scripts/vertica-cli.py remove-subcluster \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
  --name reporting
```

### Status & Monitoring

```bash
# Full status
python scripts/vertica-cli.py status --hosts 10.0.1.10,10.0.1.11,10.0.1.12

# Database status
python scripts/vertica-cli.py node-status --hosts 10.0.1.10,10.0.1.11,10.0.1.12

# List databases
python scripts/vertica-cli.py list-db --host 10.0.1.10

# Show cluster configuration
python scripts/vertica-cli.py show-cluster --hosts 10.0.1.10,10.0.1.11,10.0.1.12
```

### Maintenance

```bash
# Rolling restart (maintains availability)
python scripts/vertica-cli.py rolling-restart \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
  --batch-size 1 \
  --wait 30

# Re-IP after node replacement
python scripts/vertica-cli.py re-ip \
  --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
  --old-ips 10.0.1.11 \
  --new-ips 10.0.1.20
```

## Cost Management

### Stop Instances (hibernate)

To save costs when the database is not needed:

```bash
# Stop database first
python scripts/vertica-cli.py stop-db --hosts ... --db-name analytics

# Stop EC2 instances
aws ec2 stop-instances --instance-ids $(pulumi stack output node_ids)
```

### Start Instances (resume)

```bash
# Start EC2 instances
aws ec2 start-instances --instance-ids $(pulumi stack output node_ids)

# Wait for instances to be ready, then start database
python scripts/vertica-cli.py start-db --hosts ... --db-name analytics
```

### Terminate (destroy)

```bash
# Destroy everything (Pulumi handles cleanup)
pulumi destroy
```

## Pulumi Dynamic Resources

The project also provides Pulumi dynamic resources for declarative management:

```python
from modules.vertica import VerticaDatabase, VerticaNodePool, VerticaSubcluster

# Create a database
db = VerticaDatabase("analytics-db",
    db_name="analytics",
    hosts="10.0.1.10,10.0.1.11,10.0.1.12",
    eon_mode=True,
    shard_count=6,
)

# Manage node pool
nodes = VerticaNodePool("analytics-nodes",
    db_name="analytics",
    hosts="10.0.1.10,10.0.1.11,10.0.1.12",
    new_hosts="10.0.1.13",  # Add new node
)

# Manage subcluster
reporting = VerticaSubcluster("reporting-subcluster",
    db_name="analytics",
    hosts="10.0.1.10,10.0.1.11,10.0.1.12",
    subcluster_name="reporting",
    sc_hosts="10.0.1.13,10.0.1.14",
)
```

## Python API

Use the modules directly in Python:

```python
from modules.vertica import VClusterManager
from modules.cluster_management import ClusterLifecycleManager
from modules.compute import AWSComputeProvider

# Configuration
vertica_config = {
    "cluster_name": "my-cluster",
    "database": {"name": "analytics", "admin_username": "dbadmin"},
    "nodes": {"data_path": "/data/vertica", "catalog_path": "/data/catalog"},
}

# Create managers
vcluster = VClusterManager(vertica_config)
compute = AWSComputeProvider({"aws": {"region": "us-east-1"}})
lifecycle = ClusterLifecycleManager(vcluster, compute, vertica_config)

# Full lifecycle operations
from modules.compute.base import ClusterBuilder

cluster = ClusterBuilder.from_ips(["10.0.1.10", "10.0.1.11", "10.0.1.12"])

# Create database
result = lifecycle.create_cluster("my-cluster", node_count=3)

# Scale out
result = lifecycle.scale_out(cluster, additional_nodes=2)

# Scale in
result = lifecycle.scale_in(cluster, nodes_to_remove=["10.0.1.13"], terminate=True)

# Start/Stop database
result = lifecycle.stop_database(cluster)
result = lifecycle.start_database(cluster)

# Destroy everything
result = lifecycle.destroy_cluster(cluster)
```

## Testing

```bash
# Run unit tests
python -m pytest tests/ -v

# Test vcluster commands (dry-run)
python scripts/vertica-cli.py status --hosts 10.0.1.10 --verbose
```

## Troubleshooting

### vcluster not found

Ensure Vertica is installed on the management node:
```bash
which vcluster || echo "vcluster not found - install Vertica"
```

### Connection refused

Check that nodes are running and accessible:
```bash
# Test SSH connectivity
ssh -i ~/.ssh/my-key ec2-user@10.0.1.10

# Check Vertica service
sudo systemctl status verticad
```

### Database creation fails

Check logs on primary node:
```bash
sudo tail -f /opt/vertica/log/adminTools.log
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new vcluster commands
4. Submit a pull request

## License

MIT
