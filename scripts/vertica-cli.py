"""
Command-line interface for Vertica cluster operations.

Provides a CLI for common cluster management tasks
that can be run after Pulumi deployment.
"""

import argparse
import yaml
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.compute import AWSComputeProvider, BareMetalProvider
from modules.vertica import VClusterManager
from modules.cluster_management import ClusterManager


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_cluster(config: dict):
    """Get cluster based on configuration"""
    provider_name = config.get('compute', {}).get('provider', 'aws')
    
    if provider_name == 'aws':
        provider = AWSComputeProvider(config.get('compute', {}))
        # Would need to get instance IDs from Pulumi outputs
        # For now, return None
        return None
    elif provider_name == 'baremetal':
        provider = BareMetalProvider(config.get('compute', {}))
        hosts = config.get('compute', {}).get('baremetal', {}).get('hosts', [])
        return provider.import_cluster(
            [h['ip'] for h in hosts],
            name=config.get('vertica', {}).get('cluster_name', 'vertica-cluster'),
        )
    
    return None


def install_command(args):
    """Handle install command"""
    print("Installing Vertica cluster...")
    
    config = load_config(args.config)
    cluster = get_cluster(config)
    
    if not cluster:
        print("Error: Could not get cluster")
        return 1
    
    vcluster = VClusterManager(config.get('vertica', {}))
    
    # Install Vertica
    success, message = vcluster.install_vertica(cluster)
    print(message)
    
    if success:
        # Create database
        success, message = vcluster.create_database(cluster)
        print(message)
    
    return 0 if success else 1


def status_command(args):
    """Handle status command"""
    print("Checking cluster status...")
    
    config = load_config(args.config)
    cluster = get_cluster(config)
    
    if not cluster:
        print("Error: Could not get cluster")
        return 1
    
    vcluster = VClusterManager(config.get('vertica', {}))
    manager = ClusterManager(vcluster, config.get('vertica', {}))
    
    summary = manager.get_cluster_summary(cluster)
    
    print(f"\nCluster: {summary['cluster_name']}")
    print(f"Provider: {summary['provider']}")
    print(f"Nodes: {summary['node_count']}")
    print("\nNodes:")
    for node in summary['nodes']:
        print(f"  - {node['name']}: {node['private_ip']} ({node['status']})")
    
    return 0


def scale_command(args):
    """Handle scale command"""
    print(f"Scaling cluster to {args.nodes} nodes...")
    
    config = load_config(args.config)
    cluster = get_cluster(config)
    
    if not cluster:
        print("Error: Could not get cluster")
        return 1
    
    vcluster = VClusterManager(config.get('vertica', {}))
    manager = ClusterManager(vcluster, config.get('vertica', {}))
    
    success, message = manager.scaler.resize_cluster(cluster, args.nodes)
    print(message)
    
    return 0 if success else 1


def health_command(args):
    """Handle health command"""
    print("Checking cluster health...")
    
    config = load_config(args.config)
    cluster = get_cluster(config)
    
    if not cluster:
        print("Error: Could not get cluster")
        return 1
    
    vcluster = VClusterManager(config.get('vertica', {}))
    manager = ClusterManager(vcluster, config.get('vertica', {}))
    
    if cluster.primary_instance:
        manager.health.connect(
            cluster.primary_instance.public_ip or cluster.primary_instance.private_ip,
            config.get('vertica', {}).get('database', {}).get('admin_username', 'dbadmin'),
            config.get('vertica', {}).get('database', {}).get('admin_password', ''),
        )
        
        health = manager.health.check_health()
        
        print(f"\nStatus: {health['status']}")
        
        if health['alerts']:
            print("\nAlerts:")
            for alert in health['alerts']:
                print(f"  - {alert}")
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Vertica Cluster Management CLI'
    )
    
    parser.add_argument(
        '--config', '-c',
        default='config/config.yaml',
        help='Path to configuration file'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Install command
    install_parser = subparsers.add_parser('install', help='Install Vertica')
    install_parser.set_defaults(func=install_command)
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Show cluster status')
    status_parser.set_defaults(func=status_command)
    
    # Scale command
    scale_parser = subparsers.add_parser('scale', help='Scale cluster')
    scale_parser.add_argument('nodes', type=int, help='Target number of nodes')
    scale_parser.set_defaults(func=scale_command)
    
    # Health command
    health_parser = subparsers.add_parser('health', help='Check cluster health')
    health_parser.set_defaults(func=health_command)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
