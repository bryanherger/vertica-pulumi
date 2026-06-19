# End-to-End Vertica Deployment Guide

This guide has been superseded by the newer Eon Mode workflow.

For a complete, up-to-date walkthrough of deploying a 3-node Vertica Eon Mode cluster on AWS using Pulumi and `vcluster`, see:

**[README_EON.md](README_EON.md)**

For the full details, including Pulumi installation, S3 setup, all configuration options, verification, and troubleshooting, see:

**[docs/DEPLOYMENT_EON.md](docs/DEPLOYMENT_EON.md)**

## What changed?

- The supported deployment mode is now **Eon Mode** on AWS.
- Database creation is handled by `scripts/install_vertica_eon.py` after `pulumi up`, not inline within Pulumi.
- TLS bootstrap material is generated locally on the Pulumi runner and uploaded to all nodes, removing the need for passwordless SSH between nodes.
- EC2 instances use an IAM instance profile to access S3 communal storage instead of embedded access keys.
- `connect_via_public_ip: true` is required when running Pulumi from outside the target VPC.

The Enterprise Edition steps below are kept for historical context only.

---

## Historical Enterprise Mode guide (legacy)

> ⚠️ The following steps were written for an older Enterprise Mode workflow and are no longer maintained or recommended. Use the Eon Mode guides linked above.

### Prerequisites

1. **AWS Account** with IAM permissions for EC2, VPC, EBS.
2. **Pulumi** installed and configured.
3. **SSH Key Pair** created in AWS EC2.
4. **Vertica Enterprise Edition RPM**.
5. **Vertica License** - Required for Vertica 26.1+.

### Configure environment

```bash
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-2"
```

### Edit configuration

```bash
cp config/vertica-cluster.yaml.example config/config.yaml
# Edit compute.aws.region, compute.aws.key_name, vertica.rpm.local_path, vertica.license.local_path, etc.
```

### Deploy infrastructure

```bash
pulumi stack init my-cluster
pulumi up
```

### Upload Vertica files and install

```bash
python scripts/install_vertica_ee.py \
  --config config/config.yaml \
  --rpm-path /path/to/vertica.rpm \
  --license-path /path/to/license.xml
```

### Create and verify the database

SSH to the primary node and use `admintools` to create the database. See the legacy `scripts/install_vertica.py` and `scripts/install_vertica_ee.py` for details.

### Troubleshooting

- Check `cloud-init-output.log` for bootstrap errors.
- Verify security groups allow ports 22, 5433, 5434, 5444, 4803/4804, 6543.
- Verify SSH key pair name matches the private key file.

---

For current instructions, please use **[README_EON.md](README_EON.md)** or **[docs/DEPLOYMENT_EON.md](docs/DEPLOYMENT_EON.md)**.
