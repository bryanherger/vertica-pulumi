#!/usr/bin/env python3
"""
Generate a config/config_eon.yaml file for the Vertica Eon Mode deployment.

This script produces the YAML structure expected by __main__.py and
scripts/install_vertica_eon.py. The generated config supports:

  - compute.aws.connect_via_public_ip: true (recommended from outside VPC)
  - compute.aws.run_db_create_inline: false (database created by installer)
  - compute.aws.s3_auth_mode: iam_role (recommended; Pulumi creates instance profile)
  - vertica.security.generate_nma_certs: true (TLS generated locally, no node-to-node SSH)

Run interactively for a guided prompt, or non-interactively with CLI flags.
"""

import argparse
import os
import sys


def get_input(prompt: str, default: str = "", required: bool = False,
              validator=None, error_msg="Invalid input."):
    """Prompt for input with optional default and validation."""
    while True:
        full_prompt = prompt
        if default:
            full_prompt += f" [{default}]"
        full_prompt += ": "
        value = input(full_prompt).strip()
        if not value:
            if required and not default:
                print("This field is required.")
                continue
            value = default
        if validator and value and not validator(value):
            print(error_msg)
            continue
        return value


def generate_config_interactive() -> dict:
    """Generate configuration by prompting the user."""
    config = {
        'compute': {
            'provider': 'aws',
            'aws': {}
        },
        'vertica': {
            'mode': 'eon',
            'eon': {},
            'database': {},
            'nodes': {},
            'security': {}
        },
        'bootstrap': {
            'prerequisites': [
                'dialog',
                'pcre',
                'pcre2',
                'sysstat',
                'libxcrypt-compat',
            ],
            'packages': [
                'vim',
                'htop',
                'tmux',
                'wget',
                'net-tools',
                'psmisc',
                'lsof',
                'aws-cli',
            ],
            'pre_install': [
                'sysctl -w vm.max_map_count=262144',
                "echo 'vm.max_map_count=262144' >> /etc/sysctl.conf",
                "echo 'vm.swappiness=1' >> /etc/sysctl.conf",
                'sysctl -p',
                'fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile',
                "echo '/swapfile none swap sw 0 0' >> /etc/fstab",
                "echo 'dbadmin soft nofile 65536' >> /etc/security/limits.conf",
                "echo 'dbadmin hard nofile 65536' >> /etc/security/limits.conf",
                'mkdir -p /data/depot /data/vertica /data/catalog',
            ],
            'post_install': [
                "echo 'Bootstrap complete' >> /var/log/vertica-bootstrap.log",
            ],
        }
    }

    print("=" * 60)
    print("Vertica Eon Mode Cluster Configuration")
    print("=" * 60)

    # Compute / AWS
    print()
    print("AWS Infrastructure")
    print("-" * 40)
    config['compute']['aws']['region'] = get_input(
        "AWS Region", "us-east-1", required=True
    )
    config['compute']['aws']['key_name'] = get_input(
        "AWS EC2 Key Pair Name", required=True,
        error_msg="Key pair name is required."
    )
    config['compute']['aws']['instance_type'] = get_input(
        "Instance Type", "r6i.2xlarge", required=True
    )
    config['compute']['aws']['root_volume_size'] = int(get_input(
        "Root Volume Size (GB)", "100", required=True,
        validator=lambda x: x.isdigit() and int(x) > 0,
        error_msg="Please enter a positive number."
    ))

    connect_public = get_input(
        "Connect via public IP? (required when outside VPC)", "y"
    ).lower()
    config['compute']['aws']['connect_via_public_ip'] = connect_public.startswith('y')

    inline_db = get_input(
        "Create database inline in Pulumi? (not recommended)", "n"
    ).lower()
    config['compute']['aws']['run_db_create_inline'] = inline_db.startswith('y')

    s3_auth_mode = get_input(
        "S3 auth mode (iam_role/access_keys)", "iam_role", required=True,
        validator=lambda x: x in ('iam_role', 'access_keys'),
        error_msg="Must be 'iam_role' or 'access_keys'."
    )
    config['compute']['aws']['s3_auth_mode'] = s3_auth_mode

    if s3_auth_mode == 'iam_role':
        existing_profile = get_input(
            "Existing IAM instance profile name (leave blank to create one)", ""
        )
        if existing_profile:
            config['compute']['aws']['iam_instance_profile'] = existing_profile

    volume_size = int(get_input(
        "Data Volume Size (GB)", "500", required=True,
        validator=lambda x: x.isdigit() and int(x) > 0,
        error_msg="Please enter a positive number."
    ))
    config['compute']['aws']['additional_volumes'] = [
        {
            'size': volume_size,
            'type': 'gp3',
            'mount_point': '/data'
        }
    ]

    config['compute']['aws']['security_group_rules'] = [
        {'protocol': 'tcp', 'port': 22, 'cidr': '0.0.0.0/0'},
        {'protocol': 'tcp', 'port': 5433, 'cidr': '0.0.0.0/0'},
        {'protocol': 'tcp', 'port': 5444, 'cidr': '0.0.0.0/0'},
        {'protocol': 'tcp', 'port': 5554, 'cidr': '0.0.0.0/0'},
        {'protocol': 'tcp', 'port': 8443, 'cidr': '0.0.0.0/0'},
        {'protocol': 'tcp', 'from_port': 5434, 'to_port': 5444, 'cidr': '0.0.0.0/0'},
        {'protocol': 'tcp', 'from_port': 4803, 'to_port': 4803, 'cidr': '10.0.0.0/16'},
        {'protocol': 'tcp', 'from_port': 6543, 'to_port': 6543, 'cidr': '10.0.0.0/16'},
    ]

    config['compute']['aws']['tags'] = {
        'Environment': 'dev',
        'Project': 'vertica-eon'
    }

    # Vertica
    print()
    print("Vertica Software")
    print("-" * 40)
    config['vertica']['version'] = get_input(
        "Vertica Version", "26.2.0-0", required=True
    )
    config['vertica']['cluster_name'] = get_input(
        "Cluster Name", "vertica-eon-cluster", required=True
    )

    print()
    print("Vertica RPM and License")
    print("-" * 40)
    config['vertica']['rpm'] = {
        'local_path': get_input(
            "Path to Vertica RPM", required=True,
            error_msg="RPM path is required."
        )
    }
    license_path = get_input(
        "Path to Vertica license XML (leave blank if none)", ""
    )
    if license_path:
        config['vertica']['license'] = {'local_path': license_path}

    print()
    print("Database")
    print("-" * 40)
    config['vertica']['database']['name'] = get_input(
        "Database Name", "pulumidb", required=True
    )
    config['vertica']['database']['admin_username'] = get_input(
        "Admin Username", "dbadmin", required=True
    )
    config['vertica']['database']['admin_password'] = get_input(
        "Admin Password", required=True,
        error_msg="Admin password is required."
    )

    print()
    print("Eon Mode Storage")
    print("-" * 40)
    config['vertica']['eon']['communal_storage_location'] = get_input(
        "S3 Communal Storage Location (e.g. s3://bucket/dbname)", required=True,
        error_msg="Communal storage location is required."
    )
    shard_count = int(get_input(
        "Shard Count", "3", required=True,
        validator=lambda x: x.isdigit() and int(x) > 0,
        error_msg="Please enter a positive number."
    ))
    config['vertica']['eon']['shard_count'] = shard_count
    config['vertica']['eon']['depot_path'] = "/data/depot"
    depot_percent = int(get_input(
        "Depot Size (% of /data volume)", "80", required=True,
        validator=lambda x: x.isdigit() and 0 < int(x) <= 100,
        error_msg="Please enter a number between 1 and 100."
    ))
    config['vertica']['eon']['depot_size'] = f"{depot_percent}%"
    config['vertica']['eon']['aws_region'] = config['compute']['aws']['region']
    config['vertica']['eon']['aws_enable_https'] = True
    config['vertica']['eon']['enable_s3_encryption'] = True
    config['vertica']['eon']['dbinit'] = "Create"

    if s3_auth_mode == 'access_keys':
        print()
        print("AWS Credentials for S3 (required with access_keys)")
        print("-" * 40)
        config['vertica']['eon']['aws_access_key_id'] = get_input(
            "AWS Access Key ID", required=True
        )
        config['vertica']['eon']['aws_secret_access_key'] = get_input(
            "AWS Secret Access Key", required=True
        )

    print()
    print("Nodes")
    print("-" * 40)
    node_count = int(get_input(
        "Node Count", "3", required=True,
        validator=lambda x: x.isdigit() and int(x) > 0,
        error_msg="Please enter a positive number."
    ))
    config['vertica']['nodes']['count'] = node_count
    config['vertica']['nodes']['data_path'] = "/data/vertica"
    config['vertica']['nodes']['catalog_path'] = "/data/catalog"

    print()
    print("TLS / Security")
    print("-" * 40)
    config['vertica']['security']['generate_nma_certs'] = True
    config['vertica']['security']['cert_validity_days'] = 365
    config['vertica']['security']['cert_country'] = get_input(
        "Certificate Country", "US"
    )
    config['vertica']['security']['cert_state'] = get_input(
        "Certificate State", "California"
    )
    config['vertica']['security']['cert_locality'] = get_input(
        "Certificate Locality", "San Francisco"
    )
    config['vertica']['security']['cert_org'] = get_input(
        "Certificate Organization", "MyOrganization"
    )
    config['vertica']['security']['cert_ou'] = get_input(
        "Certificate Organizational Unit", "DataPlatform"
    )
    config['vertica']['security']['cert_cn'] = get_input(
        "Certificate Common Name", "vertica-nma"
    )

    return config


def generate_config_from_args(args) -> dict:
    """Generate configuration from CLI arguments."""
    config = {
        'compute': {
            'provider': 'aws',
            'aws': {
                'region': args.region,
                'key_name': args.key_name,
                'instance_type': args.instance_type,
                'root_volume_size': args.root_volume_size,
                'connect_via_public_ip': args.connect_via_public_ip,
                'run_db_create_inline': args.run_db_create_inline,
                's3_auth_mode': args.s3_auth_mode,
                'additional_volumes': [
                    {
                        'size': args.depot_size,
                        'type': 'gp3',
                        'mount_point': '/data'
                    }
                ],
                'security_group_rules': [
                    {'protocol': 'tcp', 'port': 22, 'cidr': '0.0.0.0/0'},
                    {'protocol': 'tcp', 'port': 5433, 'cidr': '0.0.0.0/0'},
                    {'protocol': 'tcp', 'port': 5444, 'cidr': '0.0.0.0/0'},
                    {'protocol': 'tcp', 'port': 5554, 'cidr': '0.0.0.0/0'},
                    {'protocol': 'tcp', 'port': 8443, 'cidr': '0.0.0.0/0'},
                    {'protocol': 'tcp', 'from_port': 5434, 'to_port': 5444, 'cidr': '0.0.0.0/0'},
                    {'protocol': 'tcp', 'from_port': 4803, 'to_port': 4803, 'cidr': '10.0.0.0/16'},
                    {'protocol': 'tcp', 'from_port': 6543, 'to_port': 6543, 'cidr': '10.0.0.0/16'},
                ],
                'tags': {
                    'Environment': 'dev',
                    'Project': 'vertica-eon'
                }
            }
        },
        'vertica': {
            'version': args.version,
            'cluster_name': args.cluster_name,
            'mode': 'eon',
            'database': {
                'name': args.db_name,
                'admin_username': args.admin_username,
                'admin_password': args.admin_password
            },
            'rpm': {
                'local_path': args.rpm_path
            },
            'eon': {
                'dbinit': args.dbinit,
                'communal_storage_location': args.communal_storage,
                'shard_count': args.shard_count,
                'depot_path': '/data/depot',
                'depot_size': f"{args.depot_percent}%",
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
                'cert_country': args.cert_country,
                'cert_state': args.cert_state,
                'cert_locality': args.cert_locality,
                'cert_org': args.org,
                'cert_ou': args.cert_ou,
                'cert_cn': args.cert_cn
            }
        },
        'bootstrap': {
            'prerequisites': [
                'dialog', 'pcre', 'pcre2', 'sysstat', 'libxcrypt-compat'
            ],
            'packages': [
                'vim', 'htop', 'tmux', 'wget', 'net-tools', 'psmisc', 'lsof', 'aws-cli'
            ],
            'pre_install': [
                'sysctl -w vm.max_map_count=262144',
                "echo 'vm.max_map_count=262144' >> /etc/sysctl.conf",
                "echo 'vm.swappiness=1' >> /etc/sysctl.conf",
                'sysctl -p',
                'fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile',
                "echo '/swapfile none swap sw 0 0' >> /etc/fstab",
                "echo 'dbadmin soft nofile 65536' >> /etc/security/limits.conf",
                "echo 'dbadmin hard nofile 65536' >> /etc/security/limits.conf",
                'mkdir -p /data/depot /data/vertica /data/catalog',
            ],
            'post_install': [
                "echo 'Bootstrap complete' >> /var/log/vertica-bootstrap.log",
            ],
        }
    }

    if args.iam_instance_profile:
        config['compute']['aws']['iam_instance_profile'] = args.iam_instance_profile

    if args.license_path:
        config['vertica']['license'] = {'local_path': args.license_path}

    if args.s3_auth_mode == 'access_keys':
        if not args.aws_access_key or not args.aws_secret_key:
            print("ERROR: --aws-access-key and --aws-secret-key are required with s3_auth_mode=access_keys")
            sys.exit(1)
        config['vertica']['eon']['aws_access_key_id'] = args.aws_access_key
        config['vertica']['eon']['aws_secret_access_key'] = args.aws_secret_key

    return config


def write_config(config: dict, output_path: str):
    """Write configuration to YAML file."""
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
    """Recursively write dictionary as YAML."""
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
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--key-name", default="vertica-automation")
    parser.add_argument("--instance-type", default="r6i.2xlarge")
    parser.add_argument("--root-volume-size", type=int, default=100)
    parser.add_argument("--connect-via-public-ip", action="store_true",
                        help="Use public IPs for SSH (recommended outside VPC)")
    parser.add_argument("--run-db-create-inline", action="store_true",
                        help="Run database creation inline in Pulumi (not recommended)")
    parser.add_argument("--s3-auth-mode", default="iam_role",
                        choices=["iam_role", "access_keys"],
                        help="S3 authentication mode")
    parser.add_argument("--iam-instance-profile", default="",
                        help="Existing IAM instance profile name (blank to create one)")
    parser.add_argument("--depot-size", type=int, default=500,
                        help="Data/depot EBS volume size in GB")
    parser.add_argument("--cluster-name", default="vertica-eon-cluster")
    parser.add_argument("--db-name", default="pulumidb")
    parser.add_argument("--admin-username", default="dbadmin")
    parser.add_argument("--admin-password", required=False)
    parser.add_argument("--rpm-path", required=False)
    parser.add_argument("--license-path", default="")
    parser.add_argument("--communal-storage", required=False)
    parser.add_argument("--dbinit", default="Create",
                        help="Database initialization action: Create or Revive")
    parser.add_argument("--shard-count", type=int, default=3)
    parser.add_argument("--depot-percent", type=int, default=80)
    parser.add_argument("--node-count", type=int, default=3)
    parser.add_argument("--version", default="26.2.0-0")
    parser.add_argument("--org", default="MyOrganization")
    parser.add_argument("--cert-country", default="US")
    parser.add_argument("--cert-state", default="California")
    parser.add_argument("--cert-locality", default="San Francisco")
    parser.add_argument("--cert-ou", default="DataPlatform")
    parser.add_argument("--cert-cn", default="vertica-nma")
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
    print("2. Create/select a Pulumi stack and point it at the config:")
    print("     pulumi stack init eon-test")
    print(f"     pulumi config set vertica:config_file {output_path}")
    print("3. Deploy infrastructure: pulumi up")
    print("4. Install Vertica and create the database:")
    print(f"     python scripts/install_vertica_eon.py --config {output_path} --ssh-key ~/.ssh/{config['compute']['aws']['key_name']}.pem")
    print()


if __name__ == "__main__":
    main()
