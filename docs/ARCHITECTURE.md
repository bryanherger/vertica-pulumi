# Technical Architecture

## Overview

This project deploys a Vertica Eon Mode cluster on AWS using **Pulumi** for infrastructure and **vcluster** for database provisioning. Configuration is YAML-driven, and the workflow is intentionally split into two phases:

1. **Pulumi phase** – builds VPC, EC2, EBS, security groups, IAM, and S3 resources.
2. **Installer phase** – runs `scripts/install_vertica_eon.py` from the Pulumi runner to install Vertica and create the database.

## Architecture Layers

```
┌─────────────────────────────────────────┐
│         User Configuration               │
│    (YAML config + Pulumi secrets)        │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│      Pulumi Infrastructure Layer         │
│  (VPC, EC2, EBS, IAM, Security Groups)  │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│      Bootstrap / Cloud-Init Layer        │
│  (Amazon Linux 2023 tuning + packages)   │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│    install_vertica_eon.py (runner)       │
│  RPM install · TLS generation · NMA     │
│  vcluster create_db · verification      │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│      Vertica Eon Mode Database           │
│  3 nodes · S3 communal · depot cache     │
└─────────────────────────────────────────┘
```

## Key design decisions

### Public IP for SSH, private IP for Vertica

The Pulumi runner is usually outside the target VPC, so it needs a public IP to reach the EC2 instances. Vertica internal traffic, however, is configured to use **private IPs**:

- `connect_via_public_ip: true` tells Pulumi to export public IPs and the installer to SSH via public IPs.
- `vcluster create_db` receives `--hosts <private_ip_1>,<private_ip_2>,<private_ip_3>`.

This avoids the need to open internal Vertica ports (such as 5434/4803/6543) to the public internet.

### No node-to-node SSH

Traditional Vertica multi-node bootstrap relies on passwordless root SSH between nodes to generate and copy TLS certificates. This project removes that requirement:

- `install_vertica_eon.py` generates the full bootstrap TLS material **locally** on the Pulumi runner:
  - `rootca.pem`, `rootca.key`
  - `vertica_https.pem`, `vertica_https.key`
  - `dbadmin.pem`, `dbadmin.key`
  - `httpstls.json`
- The entire `/opt/vertica/config/https_certs/` directory is uploaded to every node before `vcluster create_db` runs.
- `vcluster` uses its default certificate locations, so the generated material is picked up automatically.

Benefits:

- Reduces the security surface area (no root SSH keys between nodes).
- Works in environments where the Pulumi runner has no direct control over intra-node SSH.
- Simplifies reproducibility; the same TLS bundle is deployed identically to every node.

### IAM instance profile for S3 access

`scripts/install_vertica_eon.py` and `vcluster` use Vertica's IAM-role support when `s3_auth_mode: iam_role`:

- Pulumi creates an IAM role and instance profile with a least-privilege policy that allows read/write access only to the configured communal bucket and path.
- The instance profile is attached to every EC2 instance.
- The installer does **not** pass `--get-aws-credentials-from-env-vars` to vcluster.
- No long-term AWS credentials are stored in the YAML file or on disk on the nodes.

If IAM instance profiles are not available, `s3_auth_mode: access_keys` can be used with explicit credentials under `vertica.eon.aws_access_key_id` and `vertica.eon.aws_secret_access_key`.

### Database creation outside Pulumi

The original Pulumi program attempted to run `vcluster create_db` inline via `pulumi_command`. That command is kept for backwards compatibility but is **disabled by default** (`run_db_create_inline: false`) because:

- It does not install the Vertica RPM or license automatically.
- It does not handle TLS bootstrap material.
- It does not start or verify the NMA.
- It is hard to debug inside Pulumi's asynchronous execution model.

The supported workflow is to run `pulumi up`, then run `scripts/install_vertica_eon.py` separately.

## Component roles

| Component | Responsibility |
|-----------|----------------|
| `__main__.py` | Reads YAML config, merges with Pulumi stack config, builds AWS resources, exports IPs and profile name. |
| `modules/pulumi_resources.py`, `modules/pulumi_vertica_resources.py` | Reusable Pulumi resource builders for network, compute, and IAM. |
| `scripts/install_vertica_eon.py` | End-to-end Vertica installation: RPM, license, OS tuning, TLS, NMA, vcluster create_db, verification. |
| `scripts/generate_nma_certs.py` | Legacy helper for NMA-only certificate generation. Superseded by `install_vertica_eon.py`'s local TLS generator. |
| `scripts/generate_eon_config.py` | Interactive/config-generator helper for `config/config_eon.yaml`. |
| `scripts/install_vertica.py`, `scripts/install_vertica_ee.py` | Legacy Enterprise Mode installers. Not used by the current Eon workflow. |

## Configuration priority

Pulumi merges configuration from multiple sources. Highest priority wins:

1. Pulumi stack config (`pulumi config set ...`)
2. Environment variables (`VERTICA_CONFIG_FILE`)
3. YAML config file (`config/config_eon.yaml`)
4. Hardcoded defaults in `__main__.py`

## Data persistence

Eon Mode separates compute from storage:

- **S3 communal storage** is the source of truth for all data and metadata.
- **Local depot** (`/data/depot`) caches hot data on each node for fast reads.
- **Catalog path** (`/data/catalog`) holds the node-local catalog.

After database creation, the installer runs `SELECT sync_catalog();` to flush the initial catalog to S3. Before destroying the infrastructure, stop the database gracefully or run `sync_catalog()` again to ensure consistency.

## Security

- EC2 instances use an AWS key pair for SSH; only the private key holder can access nodes.
- Security groups restrict ingress by CIDR (default `0.0.0.0/0` in examples; narrow this for production).
- Internal Vertica ports use private IPs and the VPC security group.
- TLS bootstrap certs are generated per deployment and never shared between clusters.
- Database passwords and AWS secrets should be stored in Pulumi secrets for production.

## Monitoring

- The Node Management Agent (NMA) exposes `https://localhost:5554/v1/health` on every node; the installer verifies it before database creation.
- `vcluster list_all_nodes` and `SELECT * FROM nodes;` are used for cluster verification.
- CloudWatch metrics are available for EC2 and EBS resources if configured.

## Future directions

- Add support for `access_keys` mode without manual node configuration.
- Add automated stop/revive flow when `dbinit: Revive` is selected.
- Add least-privilege IAM policy examples that do not require `IAMFullAccess`.
- Parameterize the Vertica version more tightly with the RPM filename.
