# End-to-End Vertica Deployment Guide

## Overview

This guide walks through deploying a complete Vertica cluster on AWS using Pulumi.

## Prerequisites

1. **AWS Account** with IAM permissions for EC2, VPC, EBS
2. **Pulumi** installed and configured
3. **SSH Key Pair** created in AWS EC2
4. **Vertica Enterprise Edition RPM** - You need the Vertica installer package
5. **Vertica License** - Required for Vertica 26.1+ (community edition no longer supported)

## Step-by-Step Deployment

### 1. Configure Environment

```bash
# Set AWS credentials (or use ~/.aws/credentials)
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="***"
export AWS_DEFAULT_REGION="us-east-2"
```

Or load from file:
```bash
source /home/bryan/awsenv.sh
```

### 2. Edit Configuration

```bash
# Copy example config
cp config/vertica-cluster.yaml.example config/config.yaml

# Edit with your settings
vim config/config.yaml
```

Key settings:
- `compute.aws.region` - AWS region (e.g., `us-east-2`)
- `compute.aws.key_name` - Your EC2 key pair name
- `compute.aws.instance_type` - Instance size (default: `r6i.2xlarge`)
- `vertica.nodes.count` - Number of nodes (default: 3)
- `vertica.rpm.local_path` - Path to Vertica RPM
- `vertica.license.local_path` - Path to Vertica license (required)

### 3. Deploy Infrastructure

```bash
# Initialize Pulumi stack
pulumi stack init my-cluster

# Deploy
pulumi up

# Or with auto-approval
pulumi up --yes
```

Expected output:
```
Outputs:
    cluster_name     : "vertica-cluster"
    compute_provider : "aws"
    instance_ids     : ["i-xxxxx", "i-yyyyy", "i-zzzzz"]
    instance_ips     : ["3.x.x.x", "16.x.x.x", "18.x.x.x"]
    primary_node_ip  : "3.x.x.x"
    ssh_command      : "ssh -i ~/.ssh/pulumi.pem ec2-user@3.x.x.x"
```

### 4. Verify SSH Access

```bash
# Get SSH command
pulumi stack output ssh_command

# Test connection
ssh -i ~/.ssh/pulumi.pem ec2-user@$(pulumi stack output primary_node_ip)
```

Expected: `SSH SUCCESS - Instance Ready`

### 5. Upload Vertica Files

**Note**: Due to timing issues with SCP during Pulumi deployment, you may need to manually upload the RPM and license after instances are ready:

```bash
# Get the primary node IP
PRIMARY_IP=$(pulumi stack output primary_node_ip)

# Upload RPM
scp -i ~/.ssh/pulumi.pem -o StrictHostKeyChecking=no \
  /path/to/vertica-xx.x.x-x.x86_64.rpm \
  ec2-user@$PRIMARY_IP:/tmp/vertica.rpm

# Upload license
scp -i ~/.ssh/pulumi.pem -o StrictHostKeyChecking=no \
  /path/to/vertica_license.xml \
  ec2-user@$PRIMARY_IP:/tmp/vertica_license.xml
```

### 6. Install Vertica

**Option A: Automated via install_vertica_ee.py**

```bash
python scripts/install_vertica_ee.py \
  --config config/config.yaml \
  --rpm-path /path/to/vertica.rpm \
  --license-path /path/to/license.xml
```

**Option B: Manual Installation**

SSH to each node and install:

```bash
# SSH to primary node
ssh -i ~/.ssh/pulumi.pem ec2-user@$(pulumi stack output primary_node_ip)

# Install prerequisites
sudo dnf install -y dialog pcre pcre2 sysstat libxcrypt-compat

# Install Vertica RPM
sudo rpm -ivh /tmp/vertica.rpm

# Install license
sudo mkdir -p /opt/vertica/config/licensing
sudo cp /tmp/vertica_license.xml /opt/vertica/config/licensing/license.xml
sudo chown -R dbadmin:verticadba /opt/vertica/config/licensing

# Run install_vertica with EULA acceptance (-Y) and license (-L)
sudo /opt/vertica/sbin/install_vertica \
  --hosts 'ip-10-0-1-88,ip-10-0-1-105,ip-10-0-1-247' \
  --dba-user dbadmin \
  --data-dir /data/vertica \
  --license /opt/vertica/config/licensing/license.xml \
  --accept-eula \
  --failure-threshold WARN \
  --ssh-identity /home/ec2-user/.ssh/vertica_key \
  -Y -L /opt/vertica/config/licensing/license.xml \
  -T
```

### 7. Create Database

After installation, SSH to the primary node and create the database:

```bash
# SSH to primary node
ssh -i ~/.ssh/pulumi.pem ec2-user@$(pulumi stack output primary_node_ip)

# Switch to dbadmin user
sudo su - dbadmin

# Create database using admintools
/opt/vertica/bin/admintools -t create_db \
  -d analytics \
  -s $(hostname -s) \
  -c /data/catalog \
  -D /data/vertica
```

### 8. Verify Database

```bash
# Connect to database
/opt/vertica/bin/vsql -U dbadmin -w your_password -c "SELECT version();"

# Check cluster status
/opt/vertica/bin/admintools -t db_status
```

## Architecture

### What Gets Created

1. **Network Layer**
   - VPC with CIDR `10.0.0.0/16`
   - Internet Gateway
   - Public Subnet (`10.0.1.0/24`)
   - Route Table with internet access

2. **Security**
   - Security Group with Vertica ports:
     - 22 (SSH)
     - 5433 (Vertica client)
     - 5434 (Vertica spread)
     - 5444 (Vertica REST API)
     - 4803, 4804 (Vertica spread)
     - 6543 (Vertica agent)

3. **Compute**
   - EC2 instances (configurable type and count)
   - Cloud-init bootstrap for system prep
   - Users: `ec2-user`, `dbadmin`, `vertica`
   - Groups: `verticadba`

4. **Storage**
   - Root volume (configurable size, default 100GB)
   - Additional EBS data volume (configurable size, default 500GB)

### Data Flow

```
Your Machine
    │
    │ SSH (port 22)
    ▼
EC2 Instance (Amazon Linux 2023)
    │
    ├── /data/vertica  (Database files)
    ├── /data/catalog  (Catalog files)
    └── /data/depot    (Depot files)
    │
    │ Vertica Client (port 5433)
    ▼
Vertica Database Cluster
```

## Troubleshooting

### SSH Permission Denied
- **Check key file permissions**: `chmod 600 ~/.ssh/your-key.pem`
- **Verify key pair name** in AWS matches your `.pem` file
- **Check security group** allows port 22 from your IP

### Installation Fails
- **Check cloud-init logs**: `sudo tail -50 /var/log/cloud-init-output.log`
- **Verify internet access**: `curl -I https://google.com`
- **Check disk space**: `df -h`
- **Ensure prerequisites installed**: `rpm -q dialog pcre pcre2`

### Database Creation Fails
- **Verify node communication**: `ping vertica-node-2` (from node-1)
- **Check Vertica logs**: `sudo tail -100 /opt/vertica/log/adminTools.log`
- **Verify permissions**: `ls -la /data/`
- **Ensure license installed**: `ls -la /opt/vertica/config/licensing/`

### EULA Acceptance Issues
- For Vertica 26.1+, EULA must be accepted
- Use `-Y` flag with `install_vertica` to auto-accept
- Or use `expect` script for interactive acceptance

## Enterprise Edition Notes

- **License Required**: Starting with Vertica 26.1, a valid license is required
- **EULA Acceptance**: Use `-Y` flag with `install_vertica`
- **License Path**: Use `-L` flag to specify license file
- **No Community Edition**: Community edition is no longer supported

## Cleanup

```bash
# Destroy all AWS resources
pulumi destroy

# Remove stack
pulumi stack rm my-cluster
```

## Files Reference

| File | Purpose |
|------|---------|
| `config/config.yaml` | Your cluster configuration |
| `__main__.py` | Pulumi entry point |
| `modules/deployment/aws_deployment.py` | AWS infrastructure automation |
| `scripts/install_vertica_ee.py` | Post-deployment EE installer |
| `scripts/bootstrap.sh` | Instance bootstrap script |
