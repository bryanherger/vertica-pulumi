# Vertica Eon Mode Deployment Guide

This guide covers deploying Vertica in **Eon Mode** using vcluster, which is the recommended approach for cloud deployments. Eon Mode separates compute from storage, using S3 for communal storage and local disks for depot caching.

## Overview

Eon Mode differs from Enterprise Mode:
- **Communal Storage**: All data stored in S3, shared across all nodes
- **Depot**: Local SSD/NVMe cache for hot data on each node
- **Shards**: Data divided into shards for parallel processing
- **Separation of compute and storage**: Scale compute independently

## Architecture

```
┌─────────────────────────────────────────┐
│           S3 Communal Storage            │
│     (All data, shared across nodes)      │
└─────────────────────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
┌───▼───┐    ┌───▼───┐    ┌───▼───┐
│ Node 1 │    │ Node 2 │    │ Node 3 │
│+ Depot │    │+ Depot │    │+ Depot │
│ (Cache)│    │ (Cache)│    │ (Cache)│
└────────┘    └────────┘    └────────┘
```

## Prerequisites

1. **AWS Infrastructure**: Deployed via Pulumi (3+ EC2 instances)
2. **Vertica RPM**: Downloaded and available locally
3. **License**: Valid Vertica license file
4. **S3 Bucket**: For communal storage (see S3 Setup below)
5. **AWS Credentials**: Either IAM instance profile (recommended) or access keys

### S3 Bucket Setup

Before deploying, create a dedicated S3 bucket for Vertica communal storage:

```bash
# Create bucket (replace with your bucket name)
BUCKET_NAME="my-vertica-communal-storage"
REGION="us-east-2"

aws s3api create-bucket \
    --bucket $BUCKET_NAME \
    --region $REGION \
    --create-bucket-configuration LocationConstraint=$REGION

# Enable versioning (recommended)
aws s3api put-bucket-versioning \
    --bucket $BUCKET_NAME \
    --versioning-configuration Status=Enabled

# Block public access
aws s3api put-public-access-block \
    --bucket $BUCKET_NAME \
    --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### AWS Authentication Methods

#### Method 1: IAM Instance Profile (Recommended)

Create an IAM role with the following trust policy for EC2:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Attach this S3 policy:

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

**Why this is preferred:**
- No credentials in configuration files
- Automatic rotation by AWS
- No risk of credential exposure in logs or git history

#### Method 2: AWS CLI Credentials (Use Only If Instance Profile Not Available)

Configure credentials on each node after deployment:

```bash
# On each EC2 instance
sudo -u dbadmin aws configure
# Enter your AWS Access Key ID and Secret Access Key
```

**Security Warning:** Never commit access keys to version control. If you must use access keys, consider using AWS Secrets Manager or Parameter Store instead of hardcoding.

## Quick Start

### 1. Create Configuration File

Copy the example configuration:

```bash
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
```

Edit `config/config_eon.yaml` and update:
- `compute.aws.key_name`: Your AWS key pair name
- `vertica.rpm.local_path`: Path to Vertica RPM
- `vertica.license.local_path`: Path to license file
- `vertica.eon.communal_storage_location`: Your S3 bucket path
- `vertica.eon.aws_access_key_id` and `aws_secret_access_key`: **Only if NOT using IAM role** (leave empty for IAM instance profile)

### 2. Deploy Infrastructure

```bash
cd pulumi-vertica-cluster
pulumi stack init eon-cluster
pulumi config set aws:region us-east-2
pulumi up
```

### 3. Install Vertica (Eon Mode)

```bash
python3 scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --rpm-path /path/to/vertica.rpm \
    --license-path /path/to/license.xml
```

This script will:
1. Upload RPM and license to all nodes
2. Install Vertica
3. Generate RSA keys and certificates
4. Deploy certificates to all nodes
5. Start NMA services
6. Create the Eon Mode database

## Detailed Steps

### Step 1: Infrastructure Deployment

The Pulumi configuration for Eon Mode adds:
- Additional volume for depot (500GB gp3)
- Security group rules for NMA HTTPS (port 8443)
- S3 access via IAM instance profile (recommended)

### Step 2: Certificate Generation

NMA requires HTTPS certificates for secure communication:

```bash
python3 scripts/generate_nma_certs.py \
    --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
    --ssh-key ~/.ssh/pulumi.pem \
    --output-dir ./certs
```

This generates:
- `nma_key.pem` - RSA private key (2048-bit)
- `nma_cert.pem` - Self-signed certificate

And deploys them to `/opt/vertica/config/share/` on all nodes.

### Step 3: NMA Service Startup

Start Node Management Agent on all nodes:

```bash
# On each node
sudo systemctl enable vertica-nma
sudo systemctl start vertica-nma

# Verify
sudo systemctl status vertica-nma
```

### Step 4: Database Creation

Create the Eon Mode database using vcluster:

```bash
vcluster create_db \
    --db-name analytics \
    --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
    --catalog-path /data/catalog \
    --data-path /data/vertica \
    --communal-storage-location s3://my-bucket/analytics \
    --shard-count 3 \
    --depot-path /data/depot \
    --depot-size 80% \
    --config-param awsauth=KEY:SECRET,awsregion=us-east-2,awsenablehttps=1 \
    --cert-file /opt/vertica/config/share/nma_cert.pem \
    --key-file /opt/vertica/config/share/nma_key.pem \
    --username dbadmin \
    --password 'YourPassword' \
    --skip-package-install
```

## Configuration Reference

### Eon Mode Specific Settings

| Setting | Description | Example |
|---------|-------------|---------|
| `communal_storage_location` | S3 path for data | `s3://bucket/db` |
| `shard_count` | Number of shards | `3` (match node count) |
| `depot_path` | Local cache directory | `/data/depot` |
| `depot_size` | Cache size | `80%` or `200G` |
| `aws_region` | S3 region | `us-east-2` |
| `enable_s3_encryption` | Server-side encryption | `true` |

### Certificate Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `generate_nma_certs` | Auto-generate certs | `true` |
| `cert_validity_days` | Certificate lifetime | `365` |
| `cert_country` | Certificate country | `US` |
| `cert_org` | Organization name | `Vertica` |

## Troubleshooting

### NMA Service Won't Start

```bash
# Check logs
sudo journalctl -u vertica-nma -n 50

# Check if certificates exist
ls -la /opt/vertica/config/share/nma_*.pem

# Manual start with debug
sudo /opt/vertica/sbin/vertica-nma -v
```

### S3 Access Issues

```bash
# Test S3 access from a node
aws s3 ls s3://your-bucket/

# If using instance profile, verify
aws sts get-caller-identity
```

### Database Creation Fails

```bash
# Check NMA health on all nodes
vcluster list_all_nodes --config /opt/vertica/config/vertica_cluster.yaml

# Verify communal storage is empty
aws s3 ls s3://your-bucket/analytics/

# Try with force cleanup
vcluster create_db ... --force-cleanup-on-failure
```

## Scaling

### Add Nodes

```bash
vcluster add_node \
    --db-name analytics \
    --hosts new-ip \
    --config /opt/vertica/config/vertica_cluster.yaml
```

### Adjust Shard Count

Shard count is set at creation time. For different shard counts, create a new database.

## Files Reference

| File | Purpose |
|------|---------|
| `config/vertica-cluster-eon.yaml.example` | Example Eon Mode configuration |
| `scripts/install_vertica_eon.py` | Automated Eon Mode installation |
| `scripts/generate_nma_certs.py` | Certificate generation and deployment |
| `docs/ARCHITECTURE.md` | System architecture overview |

## Additional Resources

- [Vertica Eon Mode Documentation](https://docs.vertica.com/26.1.x/en/eon/)
- [vcluster Commands Reference](https://docs.vertica.com/25.1.x/en/admin/vcluster/vcluster-commands/)
- [Shard Count Best Practices](https://www.vertica.com/kb/SSIScg/Content/BestPractices/Best-Practices-Eon.htm)
