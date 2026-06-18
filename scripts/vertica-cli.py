"""
Command-line interface for Vertica cluster lifecycle management.

Provides a CLI for all vcluster operations integrated with infrastructure management:

Database Lifecycle:
  create-db      Create a new database
  revive-db      Revive an Eon Mode database from communal storage
  stop-db        Stop a running database
  start-db       Start a stopped database
  drop-db        Drop (delete) a database

Node Management:
  add-node       Add a new node to the database
  remove-node    Remove a node from the database
  restart-node   Restart a single node
  stop-node      Stop a single node
  start-node     Start a single node

Subcluster Management:
  add-subcluster      Add a new subcluster
  remove-subcluster   Remove a subcluster
  start-subcluster    Start a subcluster
  stop-subcluster     Stop a subcluster
  rename-subcluster   Rename a subcluster

Status & Information:
  list-db        List all databases
  db-status      Show database status
  node-status    Show node status
  show-cluster   Show cluster configuration
  list-nodes     List all nodes

Infrastructure:
  provision      Create EC2 instances for the cluster
  terminate      Terminate EC2 instances
  stop-instances Stop EC2 instances (cost savings)
  start-instances Start EC2 instances
  status         Show overall cluster status

Maintenance:
  re-ip          Reconfigure IP addresses
  revoke         Revoke node trust
  config         Show/set configuration parameters
  rolling-restart Restart nodes one by one

Usage:
  python scripts/vertica-cli.py <command> [options]
"""

import argparse
import json
import sys
import os
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.compute import AWSComputeProvider
from modules.vertica import VClusterManager
from modules.cluster_management import ClusterLifecycleManager


def load_config(config_path: str = "config/config.yaml") -> dict:
    """Load configuration from YAML file."""
    import yaml
    
    if not os.path.exists(config_path):
        # Return defaults
        return {
            "vertica": {
                "cluster_name": "vertica-cluster",
                "database": {
                    "name": "analytics",
                    "admin_username": "dbadmin",
                    "admin_password": "",
                },
                "nodes": {
                    "data_path": "/data/vertica",
                    "catalog_path": "/data/catalog",
                },
                "network": {
                    "port": 5433,
                    "rest_api_port": 5444,
                },
            },
            "aws": {
                "region": "us-east-1",
                "instance_type": "r6i.2xlarge",
            },
        }
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_vcluster_manager(config: dict) -> VClusterManager:
    """Create VClusterManager from config."""
    vertica_config = config.get('vertica', {})
    return VClusterManager(vertica_config)


def get_lifecycle_manager(config: dict) -> ClusterLifecycleManager:
    """Create ClusterLifecycleManager from config."""
    vcluster = get_vcluster_manager(config)
    
    # Create compute provider
    provider_name = config.get('compute', {}).get('provider', 'aws')
    if provider_name == 'aws':
        provider = AWSComputeProvider(config.get('compute', {}))
    else:
        raise ValueError(f"Unsupported provider: {provider_name}")
    
    return ClusterLifecycleManager(vcluster, provider, config.get('vertica', {}))


def print_result(result: dict, verbose: bool = False):
    """Print operation result in a readable format."""
    if result['success']:
        print(f"✓ {result['message']}")
        if result.get('data') and verbose:
            print(json.dumps(result['data'], indent=2))
    else:
        print(f"✗ {result['message']}")
        if result.get('error'):
            print(f"  Error: {result['error']}")
        sys.exit(1)


# ------------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------------

def create_db_command(args):
    """Create a new database."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    # Import cluster from existing infrastructure
    from modules.compute.base import ClusterBuilder
    
    if args.hosts:
        hosts = args.hosts.split(',')
    else:
        # Try to get from Pulumi outputs
        print("Error: --hosts required. Provide comma-separated list of node IPs.")
        return 1
    
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.vcluster.create_database(
        cluster,
        db_name=args.db_name,
        eon_mode=args.eon_mode,
        shard_count=args.shard_count,
    )
    
    if result['success'] and args.wait:
        print("Waiting for database to come up...")
        wait_result = lifecycle.vcluster.wait_for_database(cluster, args.db_name, target_state='up')
        print_result(wait_result, args.verbose)
    else:
        print_result(result, args.verbose)
    
    return 0


def revive_db_command(args):
    """Revive an Eon Mode database."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    communal_path = args.communal_path or config['vertica'].get('communal_storage', {}).get('path', '')
    if not communal_path:
        print("Error: --communal-path required for revive")
        return 1
    
    result = lifecycle.vcluster.revive_database(cluster, communal_path=communal_path)
    print_result(result, args.verbose)
    return 0


def stop_db_command(args):
    """Stop a database."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.vcluster.stop_database(cluster, db_name=args.db_name)
    print_result(result, args.verbose)
    return 0


def start_db_command(args):
    """Start a stopped database."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.start_database(cluster, db_name=args.db_name)
    print_result(result, args.verbose)
    return 0


def drop_db_command(args):
    """Drop a database."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.vcluster.drop_database(cluster, db_name=args.db_name, force=args.force)
    print_result(result, args.verbose)
    return 0


def add_node_command(args):
    """Add a node to the database."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts or not args.new_host:
        print("Error: --hosts and --new-host required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.vcluster.add_node(cluster, new_host=args.new_host,
                                         db_name=args.db_name,
                                         subcluster=args.subcluster)
    print_result(result, args.verbose)
    return 0


def remove_node_command(args):
    """Remove a node from the database."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts or not args.remove_host:
        print("Error: --hosts and --remove-host required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.vcluster.remove_node(cluster, host_to_remove=args.remove_host,
                                           db_name=args.db_name)
    print_result(result, args.verbose)
    return 0


def restart_node_command(args):
    """Restart a node."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts or not args.node:
        print("Error: --hosts and --node required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.vcluster.restart_node(cluster, node_host=args.node, db_name=args.db_name)
    print_result(result, args.verbose)
    return 0


def add_subcluster_command(args):
    """Add a subcluster."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts or not args.name:
        print("Error: --hosts and --name required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    sc_hosts = args.sc_hosts.split(',') if args.sc_hosts else None
    result = lifecycle.vcluster.add_subcluster(cluster, subcluster_name=args.name,
                                               db_name=args.db_name, hosts=sc_hosts)
    print_result(result, args.verbose)
    return 0


def remove_subcluster_command(args):
    """Remove a subcluster."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts or not args.name:
        print("Error: --hosts and --name required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.vcluster.remove_subcluster(cluster, subcluster_name=args.name,
                                                 db_name=args.db_name)
    print_result(result, args.verbose)
    return 0


def status_command(args):
    """Show cluster and database status."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    # Get comprehensive status
    summary = lifecycle.get_status_summary(cluster, db_name=args.db_name)
    
    if summary['success']:
        data = summary['data']
        print(f"\n{'='*60}")
        print(f"Cluster: {data['cluster_name']}")
        print(f"Database: {data['database_name']}")
        print(f"{'='*60}")
        
        if data.get('database_status'):
            print(f"\nDatabase Status:")
            print(json.dumps(data['database_status'], indent=2))
        
        if data.get('nodes'):
            print(f"\nNode Status:")
            print(json.dumps(data['nodes'], indent=2))
    else:
        print(f"Error: {summary['message']}")
        return 1
    
    return 0


def list_db_command(args):
    """List all databases."""
    config = load_config(args.config)
    vcluster = get_vcluster_manager(config)
    
    result = vcluster.list_all_databases(host=args.host)
    print_result(result, args.verbose)
    return 0


def node_status_command(args):
    """Show node status."""
    config = load_config(args.config)
    vcluster = get_vcluster_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = vcluster.node_status(cluster, db_name=args.db_name)
    print_result(result, args.verbose)
    return 0


def show_cluster_command(args):
    """Show cluster configuration."""
    config = load_config(args.config)
    vcluster = get_vcluster_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = vcluster.show_cluster(cluster)
    print_result(result, args.verbose)
    return 0


def provision_command(args):
    """Provision EC2 instances."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    print(f"Provisioning {args.nodes} EC2 instances...")
    
    # This would use Pulumi or boto3 to create instances
    # For now, print instructions
    print(f"\nTo provision infrastructure, run:")
    print(f"  pulumi up --config node_count={args.nodes}")
    
    return 0


def terminate_command(args):
    """Terminate EC2 instances."""
    print("Terminating instances...")
    print("\nTo destroy infrastructure, run:")
    print("  pulumi destroy")
    return 0


def rolling_restart_command(args):
    """Rolling restart of nodes."""
    config = load_config(args.config)
    lifecycle = get_lifecycle_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts:
        print("Error: --hosts required")
        return 1
    
    hosts = args.hosts.split(',')
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = lifecycle.rolling_restart(cluster, db_name=args.db_name,
                                         batch_size=args.batch_size,
                                         wait_between_nodes=args.wait)
    print_result(result, args.verbose)
    return 0


def re_ip_command(args):
    """Reconfigure IP addresses."""
    config = load_config(args.config)
    vcluster = get_vcluster_manager(config)
    
    from modules.compute.base import ClusterBuilder
    
    if not args.hosts or not args.old_ips or not args.new_ips:
        print("Error: --hosts, --old-ips, and --new-ips required")
        return 1
    
    hosts = args.hosts.split(',')
    old_ips = args.old_ips.split(',')
    new_ips = args.new_ips.split(',')
    
    cluster = ClusterBuilder.from_ips(hosts, name=config['vertica'].get('cluster_name', 'vertica'))
    
    result = vcluster.re_ip(cluster, old_ips=old_ips, new_ips=new_ips)
    print_result(result, args.verbose)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Vertica Cluster Lifecycle CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a new database
  %(prog)s create-db --hosts 10.0.1.10,10.0.1.11,10.0.1.12 --db-name analytics

  # Add a node
  %(prog)s add-node --hosts 10.0.1.10,10.0.1.11 --new-host 10.0.1.12

  # Remove a node
  %(prog)s remove-node --hosts 10.0.1.10,10.0.1.11,10.0.1.12 --remove-host 10.0.1.12

  # Check status
  %(prog)s status --hosts 10.0.1.10,10.0.1.11,10.0.1.12

  # Rolling restart
  %(prog)s rolling-restart --hosts 10.0.1.10,10.0.1.11,10.0.1.12

  # Revive Eon Mode database
  %(prog)s revive-db --hosts 10.0.1.10,10.0.1.11 --communal-path s3://my-bucket/vertica
        """
    )
    
    parser.add_argument('--config', '-c', default='config/config.yaml',
                       help='Path to configuration file')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Global arguments for most commands
    def add_common_args(p):
        p.add_argument('--hosts', help='Comma-separated list of node hosts')
        p.add_argument('--db-name', default='analytics', help='Database name')
    
    # Database lifecycle commands
    create_db_p = subparsers.add_parser('create-db', help='Create a new database')
    add_common_args(create_db_p)
    create_db_p.add_argument('--eon-mode', action='store_true', help='Create in Eon Mode')
    create_db_p.add_argument('--shard-count', type=int, default=6, help='Number of shards')
    create_db_p.add_argument('--wait', action='store_true', help='Wait for database to be UP')
    create_db_p.set_defaults(func=create_db_command)
    
    revive_db_p = subparsers.add_parser('revive-db', help='Revive an Eon Mode database')
    add_common_args(revive_db_p)
    revive_db_p.add_argument('--communal-path', help='Communal storage path (S3 bucket)')
    revive_db_p.set_defaults(func=revive_db_command)
    
    stop_db_p = subparsers.add_parser('stop-db', help='Stop a database')
    add_common_args(stop_db_p)
    stop_db_p.set_defaults(func=stop_db_command)
    
    start_db_p = subparsers.add_parser('start-db', help='Start a stopped database')
    add_common_args(start_db_p)
    start_db_p.set_defaults(func=start_db_command)
    
    drop_db_p = subparsers.add_parser('drop-db', help='Drop a database')
    add_common_args(drop_db_p)
    drop_db_p.add_argument('--force', action='store_true', help='Force drop')
    drop_db_p.set_defaults(func=drop_db_command)
    
    # Node management commands
    add_node_p = subparsers.add_parser('add-node', help='Add a node to the database')
    add_common_args(add_node_p)
    add_node_p.add_argument('--new-host', required=True, help='New node host to add')
    add_node_p.add_argument('--subcluster', help='Subcluster to add node to')
    add_node_p.set_defaults(func=add_node_command)
    
    remove_node_p = subparsers.add_parser('remove-node', help='Remove a node')
    add_common_args(remove_node_p)
    remove_node_p.add_argument('--remove-host', required=True, help='Node to remove')
    remove_node_p.set_defaults(func=remove_node_command)
    
    restart_node_p = subparsers.add_parser('restart-node', help='Restart a node')
    add_common_args(restart_node_p)
    restart_node_p.add_argument('--node', required=True, help='Node to restart')
    restart_node_p.set_defaults(func=restart_node_command)
    
    # Subcluster commands
    add_sc_p = subparsers.add_parser('add-subcluster', help='Add a subcluster')
    add_common_args(add_sc_p)
    add_sc_p.add_argument('--name', required=True, help='Subcluster name')
    add_sc_p.add_argument('--sc-hosts', help='Comma-separated hosts for subcluster')
    add_sc_p.set_defaults(func=add_subcluster_command)
    
    remove_sc_p = subparsers.add_parser('remove-subcluster', help='Remove a subcluster')
    add_common_args(remove_sc_p)
    remove_sc_p.add_argument('--name', required=True, help='Subcluster name')
    remove_sc_p.set_defaults(func=remove_subcluster_command)
    
    # Status commands
    status_p = subparsers.add_parser('status', help='Show cluster status')
    add_common_args(status_p)
    status_p.set_defaults(func=status_command)
    
    list_db_p = subparsers.add_parser('list-db', help='List all databases')
    list_db_p.add_argument('--host', help='Host to query')
    list_db_p.set_defaults(func=list_db_command)
    
    node_status_p = subparsers.add_parser('node-status', help='Show node status')
    add_common_args(node_status_p)
    node_status_p.set_defaults(func=node_status_command)
    
    show_cluster_p = subparsers.add_parser('show-cluster', help='Show cluster config')
    add_common_args(show_cluster_p)
    show_cluster_p.set_defaults(func=show_cluster_command)
    
    # Infrastructure commands
    provision_p = subparsers.add_parser('provision', help='Provision EC2 instances')
    provision_p.add_argument('--nodes', type=int, default=3, help='Number of nodes')
    provision_p.set_defaults(func=provision_command)
    
    terminate_p = subparsers.add_parser('terminate', help='Terminate instances')
    terminate_p.set_defaults(func=terminate_command)
    
    # Maintenance commands
    rolling_restart_p = subparsers.add_parser('rolling-restart', help='Rolling restart')
    add_common_args(rolling_restart_p)
    rolling_restart_p.add_argument('--batch-size', type=int, default=1, help='Nodes per batch')
    rolling_restart_p.add_argument('--wait', type=int, default=30, help='Seconds between batches')
    rolling_restart_p.set_defaults(func=rolling_restart_command)
    
    re_ip_p = subparsers.add_parser('re-ip', help='Reconfigure IPs')
    add_common_args(re_ip_p)
    re_ip_p.add_argument('--old-ips', required=True, help='Old comma-separated IPs')
    re_ip_p.add_argument('--new-ips', required=True, help='New comma-separated IPs')
    re_ip_p.set_defaults(func=re_ip_command)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
