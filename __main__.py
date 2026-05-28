"""
Main entry point for Vertica cluster Pulumi deployment.

This script orchestrates the infrastructure provisioning and
Vertica installation across supported cloud providers.

Features:
- Automated Vertica prerequisite installation during cloud-init
- Optional automatic Vertica RPM installation via config
- License configuration support
- Post-deployment file upload via separate script (avoids Pulumi blocking)
"""

import yaml
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Optional, List

import pulumi
import pulumi_aws as aws
import pulumi_command as command

# Add modules to path
sys.path.insert(0, str(Path(__file__).parent))

from modules.compute import AWSComputeProvider, BareMetalProvider
from modules.compute.base import ComputeProvider, ComputeInstance, ComputeCluster
from modules.vertica import VClusterManager, VerticaInstaller, VerticaConfigurator
from modules.deployment import VerticaAWSDeployment


def load_config(config_path: Optional[str] = None) -> dict:
    """
    Load configuration from YAML file.
    
    Supports multiple file naming conventions:
    - config.yaml (production config - gitignored)
    - config.yml (alternative extension)
    - *.yaml.example / *.yml.example (example/template files)
    - sample-config.yaml (legacy fallback)
    
    Args:
        config_path: Path to config file
        
    Returns:
        Configuration dictionary
    """
    if config_path is None:
        env_config = os.environ.get('VERTICA_CONFIG')
        if env_config:
            config_path = env_config
        else:
            config_dir = Path(__file__).parent / 'config'
            
            possible_auto_paths = [
                config_dir / 'config.yaml',
                config_dir / 'config.yml',
                config_dir / 'sample-config.yaml',
                config_dir / 'sample-config.yml',
            ]
            
            for path in possible_auto_paths:
                if path.exists():
                    config_path = str(path)
                    break
            else:
                example_files = sorted(config_dir.glob('*.yaml.example')) + \
                               sorted(config_dir.glob('*.yml.example'))
                if example_files:
                    example_path = example_files[0]
                    pulumi.log.info(f"No production config found, using example: {example_path.name}")
                    config_path = str(example_path)
                else:
                    config_path = 'config/config.yaml'
    
    path = Path(config_path)
    
    if path.exists():
        pulumi.log.info(f"Loading configuration from: {path}")
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    
    pulumi.log.warn(f"Configuration file not found: {path}, using defaults")
    return {
        'compute': {'provider': 'aws', 'aws': {'region': 'us-east-1'}},
        'vertica': {'cluster_name': 'vertica-cluster', 'nodes': {'count': 3}},
    }


def _check_rpm_files(config: dict) -> tuple:
    """
    Check if Vertica RPM and license files are configured and exist.
    
    Returns:
        Tuple of (rpm_path_exists, license_path_exists, rpm_full_path, license_full_path)
    """
    vertica_config = config.get('vertica', {})
    rpm_config = vertica_config.get('rpm', {})
    license_config = vertica_config.get('license', {})
    
    # Check RPM
    rpm_path = rpm_config.get('local_path', '')
    rpm_exists = False
    rpm_full_path = ''
    if rpm_path:
        # Resolve relative to project root
        project_root = Path(__file__).parent
        possible_paths = [
            Path(rpm_path),
            project_root / rpm_path,
            Path.home() / rpm_path.lstrip('~/'),
        ]
        for path in possible_paths:
            if path.exists():
                rpm_exists = True
                rpm_full_path = str(path.absolute())
                break
    
    # Check license
    license_path = license_config.get('local_path', '')
    license_exists = False
    license_full_path = ''
    if license_path:
        project_root = Path(__file__).parent
        possible_paths = [
            Path(license_path),
            project_root / license_path,
            Path.home() / license_path.lstrip('~/'),
        ]
        for path in possible_paths:
            if path.exists():
                license_exists = True
                license_full_path = str(path.absolute())
                break
    
    return rpm_exists, license_exists, rpm_full_path, license_full_path


def main():
    """Main Pulumi entry point"""
    
    # Load configuration
    config = load_config()
    
    # Get Pulumi configuration
    pulumi_config = pulumi.Config()
    
    # Override with Pulumi config if set
    compute_provider_name = pulumi_config.get('compute.provider') or \
                            config.get('compute', {}).get('provider', 'aws')
    
    cluster_name = pulumi_config.get('cluster_name') or \
                   config.get('vertica', {}).get('cluster_name', 'vertica-cluster')
    
    node_count = int(pulumi_config.get('node_count') or \
                     config.get('vertica', {}).get('nodes', {}).get('count', 3))
    
    # Create compute provider
    compute_config = config.get('compute', {})
    compute_config['provider'] = compute_provider_name
    
    # Export configuration (these are plain strings, safe to export directly)
    pulumi.export('compute_provider', compute_provider_name)
    pulumi.export('cluster_name', cluster_name)
    pulumi.export('node_count', node_count)
    
    # Get SSH key info
    key_name = compute_config.get('aws', {}).get('key_name', 'pulumi')
    ssh_key_path = f"~/.ssh/{key_name}.pem"
    ssh_key_full = os.path.expanduser(ssh_key_path)
    
    # Check for RPM configuration
    rpm_exists, license_exists, rpm_full_path, license_full_path = _check_rpm_files(config)
    
    # Handle different providers
    if compute_provider_name == 'aws':
        # Create AWS infrastructure using the deployment module
        deployment = VerticaAWSDeployment(cluster_name, config)
        outputs = deployment.deploy()
        
        # Export outputs (some are Outputs, need special handling)
        pulumi.export('vpc_id', outputs['vpc_id'])
        pulumi.export('subnet_id', outputs['subnet_id'])
        pulumi.export('security_group_id', outputs['security_group_id'])
        pulumi.export('instance_ids', outputs['instance_ids'])
        pulumi.export('instance_ips', outputs['instance_ips'])
        
        # Export SSH connection info using .apply() for Output values
        instance_ips = outputs['instance_ips']
        if instance_ips and len(instance_ips) > 0:
            primary_ip = instance_ips[0]
            
            # Fix: Use .apply() to concatenate Output[T] with strings
            ssh_command = primary_ip.apply(lambda ip: f"ssh -i {ssh_key_path} ec2-user@{ip}")
            pulumi.export('ssh_command', ssh_command)
            pulumi.export('primary_node_ip', primary_ip)
            
            # Determine next steps based on configuration
            vertica_config = config.get('vertica', {})
            rpm_config = vertica_config.get('rpm', {})
            license_config = vertica_config.get('license', {})
            
            if rpm_exists:
                pulumi.export('vertica_status', 'RPM configured - run install script after deployment')
                pulumi.export('rpm_file', os.path.basename(rpm_full_path))
                
                # Export a status and a helper command, but DON'T upload during Pulumi
                pulumi.export('file_upload_status', 
                    'Run the install script to upload RPM and license to instances')
                
                # Provide the install command with correct config file
                if license_exists:
                    install_cmd = f"python scripts/install_vertica_eon.py --config config/config.yaml --rpm-path {rpm_full_path} --license-path {license_full_path}"
                else:
                    install_cmd = f"python scripts/install_vertica_eon.py --config config/config.yaml --rpm-path {rpm_full_path}"
                pulumi.export('install_command', install_cmd)
                    
            elif rpm_config.get('download_url', ''):
                pulumi.export('vertica_status', 'RPM will be downloaded from URL during bootstrap')
                pulumi.export('download_url', rpm_config['download_url'])
            else:
                pulumi.export('vertica_status', 'No RPM configured - manual installation required')
                pulumi.export('install_command',
                    f"python scripts/install_vertica_eon.py --config config/config.yaml")
        
        # Export database creation info if configured
        db_config = config.get('vertica', {}).get('database', {})
        if db_config.get('auto_create', False) and rpm_exists:
            pulumi.export('database_auto_create', 'Database will be created automatically after Vertica installation')
            pulumi.export('database_name', db_config.get('name', 'analytics'))
        
        # Export Eon mode info if configured
        eon_config = config.get('vertica', {}).get('eon', {})
        if eon_config:
            pulumi.export('eon_mode', 'true')
            pulumi.export('communal_storage', eon_config.get('communal_storage_location', ''))
            pulumi.export('shard_count', str(eon_config.get('shard_count', 3)))
        
        pulumi.export('status', 'AWS infrastructure deployed')
        
    elif compute_provider_name == 'baremetal':
        # Import existing infrastructure
        baremetal_provider = BareMetalProvider(compute_config)
        
        # Get host list from config
        hosts = compute_config.get('baremetal', {}).get('hosts', [])
        
        if not hosts:
            pulumi.log.error("No hosts configured for bare metal import")
            return
        
        # Import cluster
        cluster = baremetal_provider.import_cluster(
            [h['ip'] for h in hosts],
            name=cluster_name,
        )
        
        pulumi.export('cluster_nodes', [h['hostname'] for h in hosts])
        pulumi.export('status', 'Bare metal cluster imported')
    
    else:
        pulumi.log.error(f"Unknown compute provider: {compute_provider_name}")
        return
    
    # Export deployment status
    pulumi.export('deployment_status', 'Infrastructure ready')
    
    # Export notes about manual steps
    if not rpm_exists and not config.get('vertica', {}).get('rpm', {}).get('download_url', ''):
        pulumi.export('next_steps', 
            '1. Obtain Vertica RPM and update config.yaml with the local_path\n'
            '2. Run: python scripts/install_vertica_eon.py --config config/config.yaml\n'
            '3. Or with URL: update config.yaml with download_url and run pulumi up'
        )


if __name__ == '__main__':
    main()
