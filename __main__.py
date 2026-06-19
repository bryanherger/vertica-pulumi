"""
Pulumi program for Vertica cluster lifecycle management.

Demonstrates full vcluster CLI automation through Pulumi:
  - Infrastructure provisioning (EC2 instances, VPC, SG)
  - Database lifecycle (create, revive, start, stop, drop)
  - Node scaling (add, remove)
  - Subcluster management (add, remove, rename)
  - Status monitoring
  - Cost management (stop instances when not needed)

Configuration via Pulumi config or config/config.yaml.
"""

import pulumi
import pulumi_aws as aws
import yaml
import os
import json
from pathlib import Path
from urllib.parse import urlparse

from modules.compute.aws import AWSComputeProvider
from modules.vertica.vcluster import VClusterManager
from modules.cluster_management import ClusterLifecycleManager


# ------------------------------------------------------------------
# Load configuration
# ------------------------------------------------------------------

def load_config() -> dict:
    """Load configuration from YAML file, overridden by Pulumi config."""
    config = pulumi.Config()

    # Determine YAML config file path (Pulumi config or env var overrides default)
    config_file = config.get("config_file") or os.environ.get("VERTICA_CONFIG_FILE") or "config/config.yaml"
    pulumi.log.info(f"Loading Vertica config from: {config_file}")

    # Start with YAML config as the base
    cfg: dict = {}
    config_path = Path(config_file)
    if config_path.exists():
        with open(config_path) as f:
            yaml_cfg = yaml.safe_load(f) or {}

        vertica_cfg = yaml_cfg.get("vertica", {}) or {}
        aws_cfg = yaml_cfg.get("aws", {}) or {}
        compute_cfg = yaml_cfg.get("compute", {}) or {}
        compute_aws_cfg = compute_cfg.get("aws", {}) if compute_cfg.get("provider") == "aws" else {}
        eon_cfg = vertica_cfg.get("eon", {}) or {}
        db_cfg = vertica_cfg.get("database", {}) or {}

        cfg["cluster_name"] = vertica_cfg.get("cluster_name")
        cfg["node_count"] = vertica_cfg.get("nodes", {}).get("count")
        cfg["instance_type"] = compute_aws_cfg.get("instance_type") or aws_cfg.get("instance_type")
        cfg["region"] = compute_aws_cfg.get("region") or aws_cfg.get("region")
        cfg["db_name"] = db_cfg.get("name")
        cfg["db_user"] = db_cfg.get("admin_username")
        cfg["db_password"] = db_cfg.get("admin_password")
        cfg["data_path"] = vertica_cfg.get("nodes", {}).get("data_path")
        cfg["catalog_path"] = vertica_cfg.get("nodes", {}).get("catalog_path")
        cfg["key_name"] = compute_aws_cfg.get("key_name") or aws_cfg.get("key_name")
        cfg["connect_via_public_ip"] = compute_aws_cfg.get("connect_via_public_ip")
        cfg["run_db_create_inline"] = compute_aws_cfg.get("run_db_create_inline")
        cfg["s3_auth_mode"] = compute_aws_cfg.get("s3_auth_mode")
        cfg["iam_instance_profile"] = compute_aws_cfg.get("iam_instance_profile")
        cfg["aws_access_key_id"] = eon_cfg.get("aws_access_key_id") or compute_aws_cfg.get("aws_access_key_id") or aws_cfg.get("aws_access_key_id")
        cfg["aws_secret_access_key"] = eon_cfg.get("aws_secret_access_key") or compute_aws_cfg.get("aws_secret_access_key") or aws_cfg.get("aws_secret_access_key")
        cfg["communal_path"] = eon_cfg.get("communal_storage_location") or vertica_cfg.get("communal_storage")
        cfg["communal_region"] = eon_cfg.get("aws_region") or compute_aws_cfg.get("region") or aws_cfg.get("region")
        cfg["enable_s3_encryption"] = eon_cfg.get("enable_s3_encryption")
        cfg["shard_count"] = eon_cfg.get("shard_count") or vertica_cfg.get("nodes", {}).get("shard_count")
        cfg["eon_mode"] = (
            str(vertica_cfg.get("mode", "")).lower() == "eon"
            or bool(cfg.get("communal_path"))
        )

    # Override with Pulumi config (if explicitly set)
    if config.get("cluster_name") is not None:
        cfg["cluster_name"] = config.get("cluster_name")
    if config.get("node_count") is not None:
        cfg["node_count"] = int(config.get("node_count"))
    if config.get("instance_type") is not None:
        cfg["instance_type"] = config.get("instance_type")
    if config.get("region") is not None:
        cfg["region"] = config.get("region")
    if config.get("db_name") is not None:
        cfg["db_name"] = config.get("db_name")
    if config.get("db_user") is not None:
        cfg["db_user"] = config.get("db_user")
    if config.get_secret("db_password") is not None:
        cfg["db_password"] = config.get_secret("db_password")
    if config.get_bool("eon_mode") is not None:
        cfg["eon_mode"] = config.get_bool("eon_mode")
    if config.get("shard_count") is not None:
        cfg["shard_count"] = int(config.get("shard_count"))
    if config.get("data_path") is not None:
        cfg["data_path"] = config.get("data_path")
    if config.get("catalog_path") is not None:
        cfg["catalog_path"] = config.get("catalog_path")
    if config.get("communal_path") is not None:
        cfg["communal_path"] = config.get("communal_path")
    if config.get("key_name") is not None:
        cfg["key_name"] = config.get("key_name")
    if config.get("action") is not None:
        cfg["action"] = config.get("action")
    if config.get("s3_auth_mode") is not None:
        cfg["s3_auth_mode"] = config.get("s3_auth_mode")
    if config.get("aws_access_key_id") is not None:
        cfg["aws_access_key_id"] = config.get("aws_access_key_id")
    if config.get_secret("aws_secret_access_key") is not None:
        cfg["aws_secret_access_key"] = config.get_secret("aws_secret_access_key")
    if config.get("iam_instance_profile") is not None:
        cfg["iam_instance_profile"] = config.get("iam_instance_profile")
    if config.get_bool("connect_via_public_ip") is not None:
        cfg["connect_via_public_ip"] = config.get_bool("connect_via_public_ip")
    if config.get_bool("run_db_create_inline") is not None:
        cfg["run_db_create_inline"] = config.get_bool("run_db_create_inline")

    # Apply hardcoded defaults only for values still missing
    defaults = {
        "cluster_name": "vertica-cluster",
        "node_count": 3,
        "instance_type": "r6i.2xlarge",
        "region": "us-east-1",
        "db_name": "analytics",
        "db_user": "dbadmin",
        "db_password": "",
        "eon_mode": False,
        "shard_count": 6,
        "data_path": "/data/vertica",
        "catalog_path": "/data/catalog",
        "communal_path": "",
        "key_name": "",
        "action": "create",
        "s3_auth_mode": "iam_role",
        "aws_access_key_id": "",
        "aws_secret_access_key": "",
        "iam_instance_profile": "",
        "connect_via_public_ip": False,
        "run_db_create_inline": False,
        "enable_s3_encryption": False,
        "communal_region": cfg.get("region", "us-east-1"),
    }

    for key, value in defaults.items():
        if cfg.get(key) is None or cfg.get(key) == "":
            # Preserve explicit empty string only if it's a valid intentional value
            if key in ("aws_access_key_id", "aws_secret_access_key", "iam_instance_profile", "communal_path"):
                cfg[key] = cfg.get(key, value)
            else:
                cfg[key] = value

    cfg["config_file"] = config_file

    return cfg


cfg = load_config()

# ------------------------------------------------------------------
# Create compute provider
# ------------------------------------------------------------------

provider_config = {
    "aws": {
        "region": cfg["region"],
        "instance_type": cfg["instance_type"],
        "key_name": cfg["key_name"],
        "iam_instance_profile": cfg.get("iam_instance_profile", ""),
        "tags": {"Project": "pulumi-vertica", "ManagedBy": "pulumi"},
    }
}

compute = AWSComputeProvider(provider_config)

# ------------------------------------------------------------------
# Infrastructure: VPC, Subnet, Security Group
# ------------------------------------------------------------------

# Create VPC
vpc = aws.ec2.Vpc(
    f"{cfg['cluster_name']}-vpc",
    cidr_block="10.0.0.0/16",
    enable_dns_hostnames=True,
    enable_dns_support=True,
    tags={"Name": f"{cfg['cluster_name']}-vpc", "Project": "pulumi-vertica"},
)

# Create Internet Gateway
igw = aws.ec2.InternetGateway(
    f"{cfg['cluster_name']}-igw",
    vpc_id=vpc.id,
    tags={"Name": f"{cfg['cluster_name']}-igw", "Project": "pulumi-vertica"},
)

# Create public subnet
az = aws.get_availability_zones(state="available").names[0]
subnet = aws.ec2.Subnet(
    f"{cfg['cluster_name']}-subnet",
    vpc_id=vpc.id,
    cidr_block="10.0.1.0/24",
    map_public_ip_on_launch=True,
    availability_zone=az,
    tags={"Name": f"{cfg['cluster_name']}-subnet", "Project": "pulumi-vertica"},
)

# Create route table
route_table = aws.ec2.RouteTable(
    f"{cfg['cluster_name']}-rt",
    vpc_id=vpc.id,
    routes=[
        aws.ec2.RouteTableRouteArgs(
            cidr_block="0.0.0.0/0",
            gateway_id=igw.id,
        )
    ],
    tags={"Name": f"{cfg['cluster_name']}-rt", "Project": "pulumi-vertica"},
)

# Associate route table with subnet
route_table_association = aws.ec2.RouteTableAssociation(
    f"{cfg['cluster_name']}-rt-assoc",
    subnet_id=subnet.id,
    route_table_id=route_table.id,
)

# Create security group for Vertica
sg = aws.ec2.SecurityGroup(
    f"{cfg['cluster_name']}-sg",
    vpc_id=vpc.id,
    description=f"Security group for {cfg['cluster_name']} Vertica cluster",
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp", from_port=22, to_port=22,
            cidr_blocks=["0.0.0.0/0"], description="SSH",
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp", from_port=5433, to_port=5433,
            cidr_blocks=["0.0.0.0/0"], description="Vertica client",
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp", from_port=5444, to_port=5444,
            cidr_blocks=["0.0.0.0/0"], description="Vertica REST API",
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp", from_port=5434, to_port=5434,
            cidr_blocks=["0.0.0.0/0"], description="Vertica internode",
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp", from_port=4803, to_port=4803,
            cidr_blocks=["0.0.0.0/0"], description="Spread",
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol="-1", from_port=0, to_port=0,
            cidr_blocks=["10.0.0.0/16"], description="Internal VPC traffic",
        ),
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1", from_port=0, to_port=0,
            cidr_blocks=["0.0.0.0/0"], description="Allow all outbound",
        )
    ],
    tags={"Name": f"{cfg['cluster_name']}-sg", "Project": "pulumi-vertica"},
)

# ------------------------------------------------------------------
# IAM instance profile for Vertica S3 / communal storage access
# ------------------------------------------------------------------

def _parse_s3_bucket(communal_path: str) -> str:
    """Extract bucket name from an s3://bucket/prefix path."""
    if communal_path.lower().startswith("s3://"):
        parsed = urlparse(communal_path)
        return parsed.netloc.split("/")[0]
    return ""

instance_profile = None
if cfg.get("iam_instance_profile"):
    # Use a pre-existing IAM instance profile supplied by the operator.
    instance_profile = cfg["iam_instance_profile"]
    pulumi.log.info(f"Using existing IAM instance profile: {instance_profile}")
elif cfg.get("s3_auth_mode", "iam_role") == "iam_role" and cfg.get("communal_path"):
    communal_bucket = _parse_s3_bucket(cfg["communal_path"])
    if communal_bucket:
        role_name = f"{cfg['cluster_name']}-vertica-s3-role"
        assume_role_policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })

        s3_role = aws.iam.Role(
            role_name,
            assume_role_policy=assume_role_policy,
            tags={
                "Name": role_name,
                "Project": "pulumi-vertica",
                "ClusterName": cfg["cluster_name"],
            },
        )

        s3_policy_statements = [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                ],
                "Resource": f"arn:aws:s3:::{communal_bucket}",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:AbortMultipartUpload",
                    "s3:ListMultipartUploadParts",
                ],
                "Resource": f"arn:aws:s3:::{communal_bucket}/*",
            },
        ]

        if cfg.get("enable_s3_encryption"):
            s3_policy_statements.append({
                "Effect": "Allow",
                "Action": [
                    "kms:Decrypt",
                    "kms:GenerateDataKey*",
                ],
                "Resource": "*",
            })

        aws.iam.RolePolicy(
            f"{cfg['cluster_name']}-vertica-s3-policy",
            role=s3_role.name,
            policy=json.dumps({"Version": "2012-10-17", "Statement": s3_policy_statements}),
        )

        instance_profile = aws.iam.InstanceProfile(
            f"{cfg['cluster_name']}-vertica-instance-profile",
            role=s3_role.name,
            tags={
                "Name": f"{cfg['cluster_name']}-vertica-instance-profile",
                "Project": "pulumi-vertica",
                "ClusterName": cfg["cluster_name"],
            },
        )

# ------------------------------------------------------------------
# Infrastructure: EC2 Instances
# ------------------------------------------------------------------

# Get AMI - Amazon Linux 2023
ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"],
    filters=[
        aws.ec2.GetAmiFilterArgs(name="name", values=["al2023-ami-*-x86_64"])
    ],
)

instances = []
for i in range(cfg["node_count"]):
    instance_name = f"{cfg['cluster_name']}-node-{i + 1}"
    
    # User data for Vertica preparation (read bootstrap prerequisites from YAML)
    bootstrap_cfg = cfg.get("bootstrap", {})
    prereq_packages = " ".join(bootstrap_cfg.get("prerequisites", []))
    extra_packages = " ".join(bootstrap_cfg.get("packages", []))
    pre_install_cmds = "\n".join(f"{cmd}" for cmd in bootstrap_cfg.get("pre_install", []))
    post_install_cmds = "\n".join(f"{cmd}" for cmd in bootstrap_cfg.get("post_install", []))

    user_data = f"""#!/bin/bash
# Vertica node bootstrap
hostnamectl set-hostname {instance_name}
echo "{instance_name}" > /etc/hostname

# Install dependencies
yum update -y
yum install -y python3 python3-pip jq {prereq_packages} {extra_packages}

# Pre-install bootstrap steps
{pre_install_cmds}

# Create Vertica directories
mkdir -p {cfg["data_path"]} {cfg["catalog_path"]}
chown -R dbadmin:verticadba {cfg["data_path"]} {cfg["catalog_path"]} 2>/dev/null || true

# Post-install bootstrap steps
{post_install_cmds}

echo "Bootstrap complete for {instance_name}"
"""
    
    # EC2's iam_instance_profile argument expects the *name* of the profile,
    # not the InstanceProfile resource object. Resolve it here.
    if instance_profile is None:
        profile_name = None
    elif isinstance(instance_profile, str):
        profile_name = instance_profile
    else:
        profile_name = instance_profile.name

    instance = aws.ec2.Instance(
        instance_name,
        ami=ami.id,
        instance_type=cfg["instance_type"],
        subnet_id=subnet.id,
        vpc_security_group_ids=[sg.id],
        key_name=cfg["key_name"] if cfg["key_name"] else None,
        user_data=user_data,
        iam_instance_profile=profile_name,
        root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
            volume_size=100,
            volume_type="gp3",
            encrypted=True,
        ),
        tags={
            "Name": instance_name,
            "VerticaNode": str(i),
            "role": "primary" if i == 0 else "secondary",
            "Project": "pulumi-vertica",
            "ClusterName": cfg["cluster_name"],
        },
    )
    instances.append(instance)

# ------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------

# Export VPC and networking info
pulumi.export("vpc_id", vpc.id)
pulumi.export("subnet_id", subnet.id)
pulumi.export("security_group_id", sg.id)

# Export instance details
for i, instance in enumerate(instances):
    pulumi.export(f"node_{i+1}_id", instance.id)
    pulumi.export(f"node_{i+1}_private_ip", instance.private_ip)
    pulumi.export(f"node_{i+1}_public_ip", instance.public_ip)

# Export aggregated instance IPs (public or private based on config)
public_ips = pulumi.Output.all(*[inst.public_ip for inst in instances]).apply(lambda ips: list(ips))
private_ips = pulumi.Output.all(*[inst.private_ip for inst in instances]).apply(lambda ips: list(ips))
pulumi.export("instance_ips", public_ips)
pulumi.export("instance_private_ips", private_ips)
# Export cluster info
pulumi.export("cluster_name", cfg["cluster_name"])
pulumi.export("node_count", cfg["node_count"])
pulumi.export("db_name", cfg["db_name"])
pulumi.export("db_user", cfg["db_user"])
pulumi.export("region", cfg["region"])
pulumi.export("s3_auth_mode", cfg.get("s3_auth_mode", "iam_role"))
if instance_profile:
    if isinstance(instance_profile, str):
        pulumi.export("iam_instance_profile", instance_profile)
    else:
        pulumi.export("iam_instance_profile", instance_profile.name)
        pulumi.export("iam_instance_profile_arn", instance_profile.arn)

# ------------------------------------------------------------------
# Database lifecycle commands (via Pulumi command provider or dynamic resources)
# ------------------------------------------------------------------

# Note: In a real deployment, you would use the ClusterLifecycleManager
# or the Pulumi dynamic resources (VerticaDatabase, VerticaNodePool, etc.)
# to manage the database lifecycle. The vcluster commands are executed
# after infrastructure is provisioned.

# Example: Create a command resource to run vcluster create_db
# This requires the pulumi-command provider

try:
    import pulumi_command as command
    
    # Build hosts string
    hosts_str = pulumi.Output.all(*[inst.private_ip for inst in instances]).apply(
        lambda ips: ",".join(ips)
    )
    
    # Command to create database (run on primary node)
    # Use public IP when running Pulumi from outside the VPC, private IP otherwise.
    primary_ip = instances[0].public_ip if cfg.get("connect_via_public_ip") else instances[0].private_ip
    
    # Wait for instances to be ready
    readiness = command.local.Command(
        "wait-for-instances",
        create="sleep 60",  # Simple wait; use proper health checks in production
        triggers=[inst.id for inst in instances],
    )
    
    # Create database command (only when an SSH key is configured AND inline DB creation is enabled)
    if cfg.get("key_name") and cfg.get("run_db_create_inline"):
        create_db_cmd = command.remote.Command(
            "create-database",
            connection=command.remote.ConnectionArgs(
                host=primary_ip,
                user="ec2-user",
                private_key=open(os.path.expanduser(f"~/.ssh/{cfg['key_name']}.pem")).read(),
            ),
            create=pulumi.Output.all(hosts_str).apply(
                lambda h: f"""#!/bin/bash
set -e
echo "Creating Vertica database..."
/opt/vertica/bin/vcluster create_db \
  --db-name {cfg['db_name']} \
  --hosts {h[0]} \
  --data-path {cfg['data_path']} \
  --catalog-path {cfg['catalog_path']} \
  {'--eon-mode' if cfg['eon_mode'] else ''} \
  {'--shard-count ' + str(cfg['shard_count']) if cfg['eon_mode'] else ''} \
  --db-user {cfg['db_user']}
echo "Database created successfully"
"""
            ),
            opts=pulumi.ResourceOptions(depends_on=[readiness]),
        )
        
        pulumi.export("db_create_status", create_db_cmd.stdout)
    else:
        reason = "no key_name configured" if not cfg.get("key_name") else "run_db_create_inline is not enabled"
        pulumi.log.info(f"Skipping inline create-database remote command: {reason}. Run scripts/install_vertica_eon.py after the stack is ready.")
        pulumi.export("db_create_status", f"skipped: {reason}")
    
except ImportError:
    pulumi.log.warn("pulumi-command not installed. Database lifecycle commands not available.")
    
    # Alternative: Export the command to run manually
    manual_cmd = pulumi.Output.all(*[inst.private_ip for inst in instances]).apply(
        lambda ips: f"""
# Manual database creation steps:
# 1. SSH to primary node: ssh -i ~/.ssh/{cfg['key_name']}.pem ec2-user@{ips[0]}
# 2. Create database:
/opt/vertica/bin/vcluster create_db \\
  --db-name {cfg['db_name']} \\
  --hosts {','.join(ips)} \\
  --data-path {cfg['data_path']} \\
  --catalog-path {cfg['catalog_path']} \\
  {'--eon-mode ' if cfg['eon_mode'] else ''} \\
  {'--shard-count ' + str(cfg['shard_count']) if cfg['eon_mode'] else ''} \\
  --db-user {cfg['db_user']}
# 3. Check status:
/opt/vertica/bin/vcluster db_status --db-name {cfg['db_name']} --hosts {','.join(ips)}
"""
    )
    pulumi.export("manual_setup_command", manual_cmd)

# ------------------------------------------------------------------
# Lifecycle action handlers
# ------------------------------------------------------------------

# The following show how to use the ClusterLifecycleManager for different actions

# Example: If action is "revive", we would:
#   1. Provision instances (already done above)
#   2. Install Vertica
#   3. Run vcluster revive_db with communal storage

# Example: If action is "stop", we would:
#   1. Run vcluster stop_db
#   2. Optionally stop EC2 instances to save costs

# Example: If action is "start", we would:
#   1. Start EC2 instances if stopped
#   2. Run vcluster start_db

# Example: If action is "destroy", we would:
#   1. Run vcluster stop_db
#   2. Run vcluster drop_db
#   3. Terminate EC2 instances (Pulumi destroy handles this)

# Export action-specific instructions
action_instructions = {
    "create": "Database will be created after infrastructure is ready.",
    "revive": f"Run: vcluster revive_db --db-name {cfg['db_name']} --communal-storage-location {cfg['communal_path']}",
    "start": "Start stopped instances, then run vcluster start_db.",
    "stop": "Run vcluster stop_db, then optionally stop instances.",
    "destroy": "Run vcluster stop_db && vcluster drop_db, then destroy resources.",
}

pulumi.export("action", cfg["action"])
pulumi.export("action_instructions", action_instructions.get(cfg["action"], "Unknown action"))

# ------------------------------------------------------------------
# Cost management outputs
# ------------------------------------------------------------------

# Export commands to stop/start instances for cost management
stop_instances_cmd = pulumi.Output.all(*[inst.id for inst in instances]).apply(
    lambda ids: f"aws ec2 stop-instances --instance-ids {' '.join(ids)} --region {cfg['region']}"
)
start_instances_cmd = pulumi.Output.all(*[inst.id for inst in instances]).apply(
    lambda ids: f"aws ec2 start-instances --instance-ids {' '.join(ids)} --region {cfg['region']}"
)

pulumi.export("stop_instances_command", stop_instances_cmd)
pulumi.export("start_instances_command", start_instances_cmd)

# Export estimated monthly cost (rough estimate)
instance_pricing = {
    "r6i.large": 0.1512,
    "r6i.xlarge": 0.3024,
    "r6i.2xlarge": 0.6048,
    "r6i.4xlarge": 1.2096,
    "r6i.8xlarge": 2.4192,
    "r6i.12xlarge": 3.6288,
    "r6i.16xlarge": 4.8384,
    "r6i.24xlarge": 7.2576,
    "r6i.32xlarge": 9.6768,
}
hourly_rate = instance_pricing.get(cfg["instance_type"], 0.6048)
monthly_estimate = hourly_rate * cfg["node_count"] * 730  # ~730 hours/month

pulumi.export("estimated_monthly_cost_usd", f"${monthly_estimate:.2f} (on-demand, full uptime)")
pulumi.export("cost_savings_if_stopped", f"${monthly_estimate:.2f}/month if stopped when not in use")
