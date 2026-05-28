# Vertica Enterprise Edition (EE) Deployment

This directory contains Pulumi automation for deploying Vertica Enterprise Edition on AWS.

## Prerequisites

Before starting, ensure you have:

1. **Vertica RPM** - Enterprise Edition installer package (e.g., `vertica-25.4.0-6.RHEL8.x86_64.rpm`)
2. **Vertica License** - Required for Vertica 26.1+ (file ending in `.xml`)
3. **AWS Account** with appropriate IAM permissions (see IAM Policy below)
4. **AWS CLI** or IAM user credentials with access keys
5. **SSH Key Pair** created in AWS EC2 (e.g., named `pulumi`)
6. **Pulumi CLI** installed locally
7. **Python 3** with pip
8. **~/.pulumi/bin** in your PATH

### Required AWS IAM Permissions

The AWS user or role needs the following EC2 permissions. If you don't have these, the deployment will use fallback values where possible.

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeImages",
                "ec2:DescribeInstances",
                "ec2:DescribeVpcs",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeInternetGateways",
                "ec2:DescribeRouteTables",
                "ec2:DescribeVolumes",
                "ec2:CreateVpc",
                "ec2:CreateSubnet",
                "ec2:CreateInternetGateway",
                "ec2:CreateRouteTable",
                "ec2:CreateRoute",
                "ec2:CreateSecurityGroup",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:AuthorizeSecurityGroupEgress",
                "ec2:CreateTags",
                "ec2:RunInstances",
                "ec2:CreateVolume",
                "ec2:AttachVolume",
                "ec2:AssociateRouteTable",
                "ec2:AttachInternetGateway",
                "ec2:ModifyInstanceAttribute",
                "ec2:Delete*",
                "ec2:TerminateInstances",
                "ec2:DetachVolume",
                "ec2:DisassociateRouteTable"
            ],
            "Resource": "*"
        }
    ]
}
```

**Note on Missing Permissions**: If you don't have `ec2:DescribeAvailabilityZones` or `ec2:DescribeImages` permissions, the deployment will use fallback values:
- AMI: Uses hardcoded Amazon Linux 2023 AMI for your region (or specify one in config)
- Availability Zone: Uses `<region>a` (e.g., `us-east-2a`)

To avoid issues, you can specify an explicit AMI in `config.yaml`:
```yaml
compute:
  aws:
    ami: "ami-xxxxxxxxxxxxxxxxx"
```

## Quick Start

### 1. Set Up Environment

```bash
# Set AWS credentials (or use ~/.aws/credentials)
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-2"

# Ensure Pulumi is in PATH
export PATH="$HOME/.pulumi/bin:$PATH"

# Source credentials from file if available
source /path/to/awsenv.sh
```

### 2. Configure Deployment

Copy the example config and update paths to your RPM and license files:

```bash
cp config/vertica-cluster.yaml.example config/config.yaml
```

Edit `config/config.yaml` with your settings:

```yaml
compute:
  provider: aws
  aws:
    region: us-east-2
    key_name: pulumi                    # Your AWS EC2 key pair name
    instance_type: r6i.2xlarge          # Vertica recommended: memory optimized
    root_volume_size: 100
    additional_volumes:
      - size: 500
        type: gp3
        mount_point: /data

vertica:
  version: "25.4.0-6"
  cluster_name: vertica-cluster

  # License Configuration (Required for Vertica 26.1+)
  license:
    local_path: "~/Downloads/vertica_license.xml"

  # RPM Configuration
  rpm:
    local_path: "~/Downloads/vertica-25.4.0-6.RHEL8.x86_64.rpm"

  database:
    name: analytics
    admin_username: dbadmin
    # CHANGE THIS: Set a strong password. You can also set via VERTICA_ADMIN_PASSWORD env var
    admin_password: "CHANGE_ME_USE_STRONG_PASSWORD"

  nodes:
    count: 3
    data_path: /data/vertica
    catalog_path: /data/catalog
```

**Important Config Notes:**
- `key_name`: Must match an existing AWS EC2 key pair. The private key should be at `~/.ssh/{key_name}.pem`
- `license.local_path`: Path to your Vertica license XML file
- `rpm.local_path`: Path to your Vertica RPM installer
- `curl` is intentionally omitted from prerequisites (Amazon Linux 2023 ships `curl-minimal` which conflicts)

### 3. Deploy

```bash
# Initialize Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize Pulumi stack
pulumi stack init my-cluster

# Deploy everything
pulumi up
```

The deployment will:
1. Create VPC, subnet, security group, and EC2 instances
2. Install prerequisites during cloud-init (dialog, pcre, sysstat, etc.)
3. Upload RPM and license files to instances (with retry logic)
4. Install Vertica RPM
5. Run `install_vertica -Y -L <license>` to accept EULA and configure

### 4. Verify Deployment

After `pulumi up` completes, check the outputs:

```bash
pulumi stack output
```

Key outputs:
- `ssh_command`: SSH command to connect to primary node
- `instance_ips`: List of all node IPs
- `file_upload_status`: Whether RPM/license uploaded successfully
- `manual_install_command`: Fallback command if auto-install fails

Connect and verify Vertica:
```bash
# SSH to primary node (use the ssh_command output)
ssh -i ~/.ssh/pulumi.pem ec2-user@<primary-ip>

# Switch to dbadmin and check version
sudo su - dbadmin
export VERTICA_PASSWORD='CHANGE_ME_USE_STRONG_PASSWORD'
/opt/vertica/bin/vsql -U dbadmin -d analytics -w "$VERTICA_PASSWORD" -c "SELECT version();"
/opt/vertica/bin/vsql -U dbadmin -d analytics -w "$VERTICA_PASSWORD" -c "SELECT * FROM nodes;"
```

### 5. Destroy (When Done)

```bash
pulumi destroy --yes
pulumi stack rm my-cluster --yes
```

## Manual Installation (Fallback)

If automatic installation fails, use the manual script:

```bash
python scripts/install_vertica_ee.py \
  --config config/config.yaml \
  --rpm-path ~/Downloads/vertica-25.4.0-6.RHEL8.x86_64.rpm \
  --license-path ~/Downloads/vertica_license.xml \
  --ssh-key ~/.ssh/pulumi.pem
```

## Key Changes for EE

- **License Required**: Configuration must include `license.local_path`
- **EULA Acceptance**: Uses `-Y` flag with `install_vertica` to auto-accept EULA
- **License Path**: Uses `-L` flag to specify license file location
- **No Community Edition**: Removed `community_edition` option (unsupported in 26.1+)
- **No curl in prerequisites**: Amazon Linux 2023 ships `curl-minimal` which conflicts with the `curl` package. `wget` is used instead.

## Troubleshooting

### File Upload Timeout
The RPM file is large (~750MB). Upload may take several minutes. The code includes:
- SSH readiness checks (waits up to 5 minutes for instances to be ready)
- Retry logic with exponential backoff (5 attempts with 5-30s delays)
- SCP with 10-minute timeout per attempt

If upload still fails, use the manual install command from stack outputs.

### Cloud-init Failures
If cloud-init fails (check `/var/log/cloud-init-output.log`):
- The `dbadmin` user is now created before pre_install commands run
- `set -e` in bootstrap script may cause early exit on non-critical errors

### Permission Denied Errors
Ensure:
- AWS credentials have the required IAM permissions
- SSH key file exists at `~/.ssh/{key_name}.pem` with correct permissions (600)
- The EC2 key pair exists in the target AWS region

### Database Creation
After Vertica installation, create a database:
```bash
sudo su - dbadmin
/opt/vertica/bin/admintools -t create_db \
  --hosts=localhost \
  --database=analytics \
  --password='CHANGE_ME_USE_STRONG_PASSWORD' \
  --catalog_path=/data/catalog \
  --data_path=/data/vertica
```

## Files

- `config/config.yaml` - Production configuration (gitignored)
- `config/vertica-cluster.yaml.example` - Template with EE options
- `scripts/install_vertica_ee.py` - Manual EE installation script
- `modules/deployment/aws_deployment.py` - AWS infrastructure automation
- `__main__.py` - Pulumi entry point

## Architecture

```
VPC (10.0.0.0/16)
  └── Subnet (10.0.1.0/24)
      └── Security Group (Vertica ports)
          └── EC2 Instances (r6i.2xlarge)
              ├── Node 1 (Primary) + EBS 500GB
              ├── Node 2 + EBS 500GB
              └── Node 3 + EBS 500GB
```

## Support

For issues or questions:
1. Check `/var/log/vertica-bootstrap.log` on instances
2. Check Pulumi stack outputs for error messages
3. Run `pulumi logs` for detailed deployment logs
