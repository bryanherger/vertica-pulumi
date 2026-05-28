# Vertica Cluster Infrastructure

A cloud-agnostic Infrastructure-as-Code (IaC) module for deploying and managing Vertica database clusters on AWS.

## Overview

This project provides Pulumi-based infrastructure deployment for Vertica database clusters with support for both **Enterprise Edition (EE)** and **Eon Mode** deployments.

### Modes

| Mode | Use Case | Storage |
|------|----------|---------|
| **Enterprise (EE)** | Traditional on-prem style | Local EBS volumes |
| **Eon** | Cloud-optimized, separate compute/storage | S3 communal storage + local depot cache |

## Prerequisites

Before starting, ensure you have:

1. **Pulumi CLI** installed and in your PATH (`export PATH="$HOME/.pulumi/bin:$PATH"`)
2. **Python 3** with pip and virtualenv
3. **AWS CLI** or IAM user credentials with the required permissions
4. **SSH key pair** created in AWS EC2 (e.g., `pulumi`)
5. **Vertica Enterprise Edition RPM** (e.g., `vertica-25.4.0-6.RHEL8.x86_64.rpm`)
6. **Vertica License** - Required for Vertica 26.1+ (file ending in `.xml`)

### Required AWS IAM Permissions

Your AWS user or role needs EC2 permissions. See `README_EE.md` for the complete IAM policy.

If you lack `ec2:DescribeAvailabilityZones` or `ec2:DescribeImages` permissions, the deployment will use fallback values (hardcoded AMI, default AZ). You can also specify an explicit AMI in `config.yaml`.

## Quick Start

### 1. Set Up Environment

```bash
# Set AWS credentials (or use ~/.aws/credentials)
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-2"

# Ensure Pulumi is in PATH
export PATH="$HOME/.pulumi/bin:$PATH"

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config/vertica-cluster.yaml.example config/config.yaml
```

Edit `config/config.yaml` with your settings:
- `compute.aws.region` - Your AWS region
- `compute.aws.key_name` - Your EC2 key pair name (private key at `~/.ssh/{name}.pem`)
- `vertica.rpm.local_path` - Path to your Vertica RPM file
- `vertica.license.local_path` - Path to your Vertica license XML file
- `vertica.database.admin_password` - **Change this to a strong password!**

### 3. Deploy

```bash
pulumi stack init my-cluster
pulumi up
```

The deployment will:
1. Create VPC, subnet, security group, and EC2 instances
2. Install prerequisites during cloud-init (dialog, pcre, sysstat, etc.)
3. Upload RPM and license files to instances (with retry logic)
4. Install Vertica RPM
5. Run `install_vertica -Y -L <license>` to accept EULA and configure

### 4. Verify

```bash
# Check outputs
pulumi stack output

# SSH to primary node (use ssh_command output)
ssh -i ~/.ssh/pulumi.pem ec2-user@<primary-ip>

# Check Vertica version (use your configured password)
export VERTICA_PASSWORD="$(pulumi stack output admin_password 2>/dev/null || echo 'your-password')"
/opt/vertica/bin/vsql -U dbadmin -d analytics -w "$VERTICA_PASSWORD" -c "SELECT version();"
/opt/vertica/bin/vsql -U dbadmin -d analytics -w "$VERTICA_PASSWORD" -c "SELECT * FROM nodes;"
```

### 5. Destroy

```bash
pulumi destroy --yes
pulumi stack rm my-cluster --yes
```

## Deployment Modes

### Enterprise Mode

```bash
cp config/vertica-cluster.yaml.example config/config.yaml
# Edit config.yaml with your settings
pulumi stack init ee-cluster
pulumi up
python scripts/install_vertica_ee.py --config config.yaml --rpm-path /path/to/vertica.rpm --license-path /path/to/license.xml
```

For detailed EE instructions: **[README_EE.md](README_EE.md)**

### Eon Mode

```bash
# Generate configuration interactively
python scripts/generate_eon_config.py --interactive --output config/config_eon.yaml

# Or copy and edit the example
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
# Edit config_eon.yaml - IMPORTANT: Change the default password and use IAM roles if possible

# Deploy infrastructure
pulumi stack init eon-cluster
pulumi up

# Install Vertica with Eon Mode automation
python scripts/install_vertica_eon.py --config config/config_eon.yaml --rpm-path /path/to/vertica.rpm --license-path /path/to/license.xml
```

**Security Note:** Eon Mode requires S3 access. Always use IAM instance profiles instead of embedding AWS credentials in configuration files. See `README_EON.md` for IAM setup instructions.

For detailed Eon instructions: **[README_EON.md](README_EON.md)**

## Documentation

- **[Architecture Overview](docs/ARCHITECTURE.md)** - System design and components
- **[Configuration Reference](docs/CONFIGURATION.md)** - All configuration options
- **[Enterprise Deployment Guide](docs/DEPLOYMENT.md)** - Detailed EE deployment
- **[Eon Mode Deployment Guide](docs/DEPLOYMENT_EON.md)** - Detailed Eon deployment with S3 setup
- **[Project Summary](SUMMARY.md)** - Repository structure and quick reference

## Security Best Practices

1. **Passwords**: Always change the default password in configuration files
2. **AWS Credentials**: Use IAM instance profiles instead of access keys
3. **S3 Buckets**: Block public access, enable versioning, use lifecycle policies
4. **SSH Keys**: Use strong key pairs, restrict security group CIDR ranges
5. **Certificates**: Let the scripts auto-generate or use proper CA-signed certs
6. **Git**: Never commit credentials, certificates, or production configs (see `.gitignore`)

## Project Structure

```
pulumi-vertica-cluster/
├── Pulumi.yaml                 # Project configuration
├── __main__.py                 # Pulumi entry point
├── requirements.txt            # Python dependencies
├── config/                     # Configuration templates
│   ├── vertica-cluster.yaml.example        # EE mode config
│   └── vertica-cluster-eon.yaml.example  # Eon mode config
├── modules/                    # Infrastructure modules
│   ├── compute/                # Compute abstraction layer
│   ├── deployment/             # AWS deployment orchestration
│   └── vertica/                # Vertica management utilities
├── scripts/                    # Deployment and utility scripts
│   ├── install_vertica_ee.py   # EE mode installer
│   ├── install_vertica_eon.py  # Eon mode installer
│   ├── generate_eon_config.py  # Interactive config generator
│   └── generate_nma_certs.py   # Certificate generation
└── docs/                       # Documentation
    ├── ARCHITECTURE.md         # System design
    ├── DEPLOYMENT.md           # EE deployment guide
    ├── DEPLOYMENT_EON.md       # Eon deployment guide
    └── s3-lifecycle.json       # S3 bucket lifecycle config
```

## Troubleshooting

### SSH Permission Denied
- Verify the SSH key pair name in AWS matches your `.pem` file
- Ensure key file permissions are `600`: `chmod 600 ~/.ssh/your-key.pem`

### File Upload Timeout
The RPM file is large (~750MB). Upload may take several minutes. The code includes retry logic with exponential backoff. If upload fails:
- Use the manual install command from stack outputs
- For Eon Mode, consider uploading RPM to S3 first, then downloading on instances

### Installation Fails
- Check `/var/log/cloud-init-output.log` for bootstrap errors
- Check `/var/log/vertica-bootstrap.log` for Vertica-specific errors
- Verify instances have internet access (for package downloads)

### Database Creation Fails
- Ensure all nodes can communicate (check security groups)
- Verify `/data` directories exist and are writable by dbadmin
- Check Vertica logs: `/opt/vertica/log/`
- Verify license is installed: `/opt/vertica/config/licensing/license.xml`

## Configuration

See `config/vertica-cluster.yaml.example` for all available options.

## Supported Compute Providers

- [x] AWS EC2
- [ ] Azure Virtual Machines (planned)
- [ ] Google Compute Engine (planned)
- [x] Existing/Bare Metal (import)

## License

MIT
