#!/usr/bin/env python3
"""
Vertica Eon Mode Configuration Generator.

Generates the complete Eon Mode configuration file from user inputs
with validation and smart defaults.

Usage:
    python scripts/generate_eon_config.py --output config/config_eon.yaml
    
    # Interactive mode
    python scripts/generate_eon_config.py --interactive

This script creates a properly formatted configuration file for
Eon Mode deployment with all required settings.
"""

import argparse
import os
import re
import sys
from pathlib import Path


def validate_s3_path(path: str) -> bool:
    """Validate S3 communal storage path format"""
    pattern = r'^s3://[a-z0-9.-]+(/.*)?$'
    return bool(re.match(pattern, path))


def validate_file_path(path: str) -> bool:
    """Check if file exists"""
    return os.path.exists(os.path.expanduser(path))


def get_input(prompt: str, default: str = "", required: bool = False,
              validator=None, error_msg: str = "") -> str:
    """Get user input with validation"""
    while True:
        if default:
            full_prompt = f"{prompt} [{default}]: "
        else:
            full_prompt = f"{prompt}: "
        
        value = input(full_prompt).strip()
        
        if not value:
            if required and not default:
                print(f"  This field is required. {error_msg}")
                continue
            value = default
        
        if validator and value:
            if not validator(value):
                print(f"  Invalid input. {error_msg}")
                continue
        
        return value


def generate_config_interactive() -> dict:
    """Generate configuration through interactive prompts"""
    print("=" * 60)
    print("Vertica Eon Mode Configuration Generator")
    print("=" * 60)
    print()
    
    config = {
        'compute': {
            'provider': 'aws',
            'aws': {}
        },
        'vertica': {
            'mode': 'eon',
            'version': '25.4.0-6',
            'nodes': {}
        }
    }
    
    # AWS Settings
    print("AWS Infrastructure Settings")
    print("-" * 40)
    
    config['compute']['aws']['region'] = get_input(
        "AWS Region", "us-east-2", required=True
    )
    config['compute']['aws']['key_name'] = get_input(
        "SSH Key Pair Name", "pulumi", required=True
    )
    config['compute']['aws']['instance_type'] = get_input(
        "Instance Type", "r6i.2xlarge", required=True
    )
    
    # Storage
    print()
    print("Storage Settings")
    print("-" * 40)
    
    depot_size = get_input(
        "Depot Volume Size (GB)", "500", required=True,
        validator=lambda x: x.isdigit() and int(x) > 0,
        error_msg="Please enter a positive number."
    )
    
    config['compute']['aws']['additional_volumes'] = [
        {
            'size': int(depot_size),
            'type': 'gp3',
            'mount_point': '/data'
        }
    ]
    
    # Vertica Settings
    print()
    print("Vertica Settings")
    print("-" * 40)
    
    config['vertica']['cluster_name'] = get_input(
        "Cluster Name", "vertica-eon-cluster", required=True
    )
    config['vertica']['database'] = {}
    config['vertica']['database']['name'] = get_input(
        "Database Name", "analytics", required=True
    )
    config['vertica']['database']['admin_username'] = get_input(
        "Admin Username", "dbadmin", required=True
    )
    
    password = get_input(
        "Admin Password", required=True,
        error_msg="Password is required for database creation."
    )
    config['vertica']['database']['admin_password'] = password
    
    # Paths
    print()
    print("File Paths")
    print("-" * 40)
    
    config['vertica']['rpm'] = {}
    rpm_path = get_input(
        "Vertica RPM Path", required=True,
        validator=validate_file_path,
        error_msg="File not found. Please provide a valid path."
    )
    config['vertica']['rpm']['local_path'] = rpm_path
    
    config['vertica']['license'] = {}
    license_path = get_input(
        "License File Path", "",
        validator=lambda x: not x or validate_file_path(x),
        error_msg="File not found. Leave empty if no license."
    )
    if license_path:
        config['vertica']['license']['local_path'] = license_path
    
    # Eon Mode Settings
    print()
    print("Eon Mode Settings")
    print("-" * 40)
    
    config['vertica']['eon'] = {}
    communal = get_input(
        "Communal Storage Location (s3://bucket/path)", required=True,
        validator=validate_s3_path,
        error_msg="Format: s3://bucket-name/path"
    )
    config['vertica']['eon']['communal_storage_location'] = communal
    
    shard_count = get_input(
        "Shard Count", "3", required=True,
        validator=lambda x: x.isdigit() and int(x) > 0,
        error_msg="Please enter a positive number."
    )
    config['vertica']['eon']['shard_count'] = int(shard_count)
    
    config['vertica']['eon']['depot_path'] = "/data/depot"
    config['vertica']['eon']['depot_size'] = "80%"
    config['vertica']['eon']['aws_region'] = config['compute']['aws']['region']
    config['vertica']['eon']['aws_enable_https'] = True
    config['vertica']['eon']['enable_s3_encryption'] = True
    
    # AWS Credentials
    print()
    print("AWS Credentials for S3 (optional if using IAM role)")
    print("-" * 40)
    
    use_keys = get_input(
        "Use explicit AWS credentials? (y/n)", "n"
    ).lower() == 'y'
    
    if use_keys:
        config['vertica']['eon']['aws_access_key_id'] = get_input(
            "AWS Access Key ID", ""
        )
        config['vertica']['eon']['aws_secret_access_key'] = get_input(
            "AWS Secret Access Key", ""
        )
    
    # Security
    print()
    print("Security Settings")
    print("-" * 40)
    
    config['vertica']['security'] = {}
    config['vertica']['security']['generate_nma_certs'] = True
    config['vertica']['security']['cert_validity_days'] = 365
    config['vertica']['security']['cert_country'] = "US"
    config['vertica']['security']['cert_org'] = get_input(
        "Organization Name", "MyOrganization"
    )
    
    return config


def generate_config_from_args(args) -> dict:
    """Generate configuration from command line arguments"""
    config = {
        'compute': {
            'provider': 'aws',
            'aws': {
                'region': args.region,
                'key_name': args.key_name,
                'instance_type': args.instance_type,
                'additional_volumes': [
                    {
                        'size': args.depot_size,
                        'type': 'gp3',
                        'mount_point': '/data'
                    }
                ]
            }
        },
        'vertica': {
            'mode': 'eon',
            'version': args.version or '25.4.0-6',
            'cluster_name': args.cluster_name,
            'database': {
                'name': args.db_name,
                'admin_username': args.admin_username,
                'admin_password': args.admin_password
            },
            'rpm': {
                'local_path': args.rpm_path
            },
            'eon': {
                'communal_storage_location': args.communal_storage,
                'shard_count': args.shard_count,
                'depot_path': '/data/depot',
                'depot_size': f"{args.depot_percent}%" if args.depot_percent else '80%',
                'aws_region': args.region,
                'aws_enable_https': True,
                'enable_s3_encryption': True
            },
            'nodes': {
                'count': args.node_count,
                'data_path': '/data/vertica',
                'catalog_path': '/data/catalog'
            },
            'security': {
                'generate_nma_certs': True,
                'cert_validity_days': 365,
                'cert_country': 'US',
                'cert_org': args.org or 'MyOrganization'
            }
        }
    }
    
    if args.license_path:
        config['vertica']['license'] = {'local_path': args.license_path}
    
    if args.aws_access_key:
        config['vertica']['eon']['aws_access_key_id'] = args.aws_access_key
        config['vertica']['eon']['aws_secret_access_key'] = args.aws_secret_key or ''
    
    return config


def write_config(config: dict, output_path: str):
    """Write configuration to YAML file"""
    try:
        import yaml
        with open(output_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    except ImportError:
        # Fallback to manual YAML writing
        with open(output_path, 'w') as f:
            f.write("# Vertica Eon Mode Cluster Configuration\n")
            f.write("# Generated automatically\n\n")
            _write_dict_yaml(f, config, 0)


def _write_dict_yaml(f, data, indent):
    """Recursively write dictionary as YAML"""
    prefix = "  " * indent
    
    for key, value in data.items():
        if isinstance(value, dict):
            f.write(f"{prefix}{key}:\n")
            _write_dict_yaml(f, value, indent + 1)
        elif isinstance(value, list):
            f.write(f"{prefix}{key}:\n")
            for item in value:
                if isinstance(item, dict):
                    f.write(f"{prefix}-\n")
                    _write_dict_yaml(f, item, indent + 1)
                else:
                    f.write(f"{prefix}- {item}\n")
        else:
            if isinstance(value, str):
                # Quote strings that need it
                if any(c in value for c in [':', '#', '{', '}', '[', ']', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '\\']):
                    value = f'"{value}"'
            f.write(f"{prefix}{key}: {value}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate Eon Mode configuration file"
    )
    parser.add_argument(
        "--output",
        default="config/config_eon.yaml",
        help="Output configuration file path"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive mode (prompt for all values)"
    )
    
    # Non-interactive options
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--key-name", default="pulumi")
    parser.add_argument("--instance-type", default="r6i.2xlarge")
    parser.add_argument("--depot-size", type=int, default=500)
    parser.add_argument("--cluster-name", default="vertica-eon-cluster")
    parser.add_argument("--db-name", default="analytics")
    parser.add_argument("--admin-username", default="dbadmin")
    parser.add_argument("--admin-password", required=False)
    parser.add_argument("--rpm-path", required=False)
    parser.add_argument("--license-path", default="")
    parser.add_argument("--communal-storage", required=False)
    parser.add_argument("--shard-count", type=int, default=3)
    parser.add_argument("--depot-percent", type=int, default=80)
    parser.add_argument("--node-count", type=int, default=3)
    parser.add_argument("--version", default="25.4.0-6")
    parser.add_argument("--org", default="MyOrganization")
    parser.add_argument("--aws-access-key", default="")
    parser.add_argument("--aws-secret-key", default="")
    
    args = parser.parse_args()
    
    if args.interactive:
        config = generate_config_interactive()
    else:
        # Validate required args in non-interactive mode
        if not args.admin_password:
            print("ERROR: --admin-password is required in non-interactive mode")
            sys.exit(1)
        if not args.rpm_path:
            print("ERROR: --rpm-path is required in non-interactive mode")
            sys.exit(1)
        if not args.communal_storage:
            print("ERROR: --communal-storage is required in non-interactive mode")
            sys.exit(1)
        
        config = generate_config_from_args(args)
    
    # Write configuration
    output_path = os.path.expanduser(args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    write_config(config, output_path)
    
    print()
    print("=" * 60)
    print(f"Configuration written to: {output_path}")
    print("=" * 60)
    print()
    print("Next steps:")
    print("1. Review the configuration file")
    print("2. Deploy infrastructure: pulumi up")
    print("3. Install Vertica:")
    print(f"   python scripts/install_vertica_eon.py --config {output_path} --rpm-path {config['vertica']['rpm']['local_path']}")
    print()


if __name__ == "__main__":
    main()
