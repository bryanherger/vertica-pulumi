# Vertica Eon Mode Deployment - Quick Start

Deploy Vertica in **Eon Mode** on AWS using vcluster with automated certificate management.

## What's Eon Mode?

Eon Mode separates compute from storage:
- **Communal Storage**: S3 bucket holds all data (durable, shared)
- **Depot**: Local SSD cache per node for hot data (fast queries)
- **Shards**: Data divided for parallel processing
- **Scale compute independently** from storage

## Prerequisites

- **AWS Account** with EC2 and S3 access
- **Vertica RPM** file (`vertica-25.4.0-6.RHEL8.x86_64.rpm` or similar)
- **Vertica License** file (required for 26.1+)
- **S3 Bucket** created for communal storage (see S3 Setup below)
- **SSH Key Pair** in AWS EC2 (e.g., `pulumi`)
- **Python 3.6+** with PyYAML: `pip install pyyaml`

### S3 Bucket Setup

Create a dedicated S3 bucket for Vertica communal storage:

```bash
# Create bucket in the same region as your cluster
aws s3api create-bucket \
    --bucket my-vertica-communal-storage \
    --region us-east-2 \
    --create-bucket-configuration LocationConstraint=us-east-2

# Enable versioning (recommended for production)
aws s3api put-bucket-versioning \
    --bucket my-vertica-communal-storage \
    --versioning-configuration Status=Enabled

# Configure lifecycle rules for old versions
aws s3api put-bucket-lifecycle-configuration \
    --bucket my-vertica-communal-storage \
    --lifecycle-configuration file://docs/s3-lifecycle.json
```

### AWS Authentication (Choose One)

#### Option A: IAM Instance Profile (Recommended)

Create an IAM role with S3 access and attach it to the EC2 instances. This is the most secure approach as credentials are automatically rotated and never stored on disk.

**Required IAM Permissions:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:ListBucket",
                "s3:GetBucketLocation",
                "s3:PutObject",
                "s3:GetObject",
                "s3:DeleteObject",
                "s3:AbortMultipartUpload",
                "s3:ListMultipartUploadParts"
            ],
            "Resource": [
                "arn:aws:s3:::my-vertica-communal-storage",
                "arn:aws:s3:::my-vertica-communal-storage/*"
            ]
        }
    ]
}
```

**Why use IAM roles?**
- No credentials stored in configuration files
- Automatic credential rotation
- No risk of accidental credential exposure in logs or git

#### Option B: AWS CLI Credentials (Use Only If IAM Role Not Available)

If you cannot use IAM instance profiles, configure AWS CLI credentials on each node:

```bash
# On each EC2 instance after deployment
sudo -u dbadmin aws configure
# Enter your AWS Access Key ID and Secret Access Key
```

**Security Warning:** If you must use access keys, set them via environment variables or AWS CLI configuration files. Never commit credentials to version control.

## Quick Deploy (5 Steps)

### 1. Generate Configuration

Interactive mode (recommended first time):

```bash
python scripts/generate_eon_config.py --interactive --output config/config_eon.yaml
```

Or non-interactive:

```bash
python scripts/generate_eon_config.py \
    --output config/config_eon.yaml \
    --region us-east-2 \
    --key-name pulumi \
    --rpm-path ~/Downloads/vertica-25.4.0-6.RHEL8.x86_64.rpm \
    --license-path ~/Downloads/vertica_license.xml \
    --communal-storage s3://my-vertica-bucket/analytics \
    --shard-count 3 \
    --admin-password '$(openssl rand -base64 24)'
```

### 2. Deploy AWS Infrastructure

```bash
pulumi stack init eon-cluster
pulumi config set aws:region us-east-2
pulumi up
```

### 3. Install Vertica (Eon Mode)

```bash
python scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --rpm-path ~/Downloads/vertica-25.4.0-6.RHEL8.x86_64.rpm \
    --license-path ~/Downloads/vertica_license.xml
```

This single command:
1. ✅ Uploads RPM + license to all nodes
2. ✅ Installs Vertica on all nodes
3. ✅ Generates RSA key pairs + certificates
4. ✅ Deploys certificates to all nodes
5. ✅ Starts NMA + HTTPS services
6. ✅ Creates Eon Mode database with vcluster

### 4. Verify Database

```bash
# Get primary node IP
pulumi stack output instance_ips

# Connect to database (use your configured password)
export VERTICA_PASSWORD="$(pulumi stack output admin_password)"
vsql -U dbadmin -d analytics -h <primary-ip> -w "$VERTICA_PASSWORD"

# Run verification queries
SELECT version();
SELECT * FROM nodes;
SELECT * FROM communal_storage;

# ⚠️ IMPORTANT: After inserting data, sync to S3 to ensure persistence
SELECT sync_catalog();
```

### ⚠️ Data Persistence Warning

**Always call `SELECT sync_catalog();` after inserting data!**

Eon Mode writes to local depot first, then flushes to S3 asynchronously. If you destroy the cluster before data is synced, it will be lost. The installation script automatically syncs after database creation, but manual operations require explicit sync.

### 5. (Optional) Destroy When Done

```bash
pulumi destroy
pulumi stack rm eon-cluster
```

## Key Files

| File | Purpose |
|------|---------|
| `config/vertica-cluster-eon.yaml.example` | Example Eon Mode config with all options documented |
| `config/config_eon.yaml` | Your generated configuration |
| `scripts/generate_eon_config.py` | Interactive config generator |
| `scripts/install_vertica_eon.py` | Full automated installation |
| `scripts/generate_nma_certs.py` | Certificate generation + deployment |
| `docs/DEPLOYMENT_EON.md` | Detailed deployment guide |

## Eon Mode Configuration Highlights

### Required Settings

```yaml
vertica:
  mode: eon
  eon:
    communal_storage_location: "s3://bucket-name/db-path"
    shard_count: 3           # Match or exceed node count
    depot_path: /data/depot  # Local cache directory
    depot_size: "80%"        # Or "200G" for absolute size
```

### Certificate Management

Auto-generated (default):
```yaml
vertica:
  security:
    generate_nma_certs: true
    cert_validity_days: 365
```

Or provide existing certificates:
```yaml
vertica:
  security:
    generate_nma_certs: false
    cert_file: "/path/to/cert.pem"
    key_file: "/path/to/key.pem"
```

### S3 Authentication

**Recommended**: Use EC2 instance profile (IAM role) - no credentials in config:
```yaml
vertica:
  eon:
    aws_access_key_id: ""      # Leave empty for IAM role
    aws_secret_access_key: ""  # Leave empty for IAM role
```

Or explicit credentials:
```yaml
vertica:
  eon:
    aws_access_key_id: "AKIA..."
    aws_secret_access_key: "secret..."
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| NMA won't start | Check cert files in `/opt/vertica/config/share/`, restart with `systemctl restart vertica-nma` |
| S3 access denied | Verify IAM role or credentials, test with `aws s3 ls s3://bucket/` |
| Database creation fails | Ensure communal storage path is empty, check NMA health with `systemctl status vertica-nma` |
| SCP upload fails | Verify SSH key path, ensure port 22 is open in security group |

## Manual Certificate Deployment

If you need to redeploy certificates separately:

```bash
python scripts/generate_nma_certs.py \
    --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
    --ssh-key ~/.ssh/pulumi.pem \
    --output-dir ./certs \
    --country US \
    --organization MyOrg \
    --validity-days 365
```

## Additional Resources

- [Full Deployment Guide](docs/DEPLOYMENT_EON.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [Vertica Eon Mode Docs](https://docs.vertica.com/26.1.x/en/eon/)
