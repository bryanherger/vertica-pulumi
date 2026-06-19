# Implementation Summary (historical)

This document was originally written when the project added a Python `vcluster` wrapper and Pulumi dynamic resources for database lifecycle management. The current recommended workflow has evolved, so this summary is now historical context.

## Current recommended workflow

The supported end-to-end path is **Eon Mode on AWS** using:

- Pulumi to create VPC, EC2, EBS, IAM, and S3 resources.
- `scripts/install_vertica_eon.py` to install Vertica and create the database.
- `vcluster` commands run from the primary node via the generated `/opt/vertica/config/vertica_cluster.yaml`.

See the current documentation:

- [README_EON.md](README_EON.md) – quick start
- [docs/DEPLOYMENT_EON.md](docs/DEPLOYMENT_EON.md) – full deployment guide
- [docs/OPERATIONS.md](docs/OPERATIONS.md) – day-two operations
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) – configuration reference
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – design overview

## What the original implementation added

1. **`modules/vertica/vcluster.py`** – Python wrapper around the `vcluster` CLI.
2. **`modules/cluster_management.py`** – `ClusterLifecycleManager` combining infrastructure and database operations.
3. **`modules/pulumi_resources.py`** – Pulumi dynamic resources for `VerticaDatabase`, `VerticaNodePool`, and `VerticaSubcluster`.
4. **`modules/compute/aws.py`** – AWS compute provider with start/stop/terminate support.
5. **`scripts/vertica-cli.py`** – CLI tool for database, node, subcluster, and infrastructure operations.
6. **`__main__.py`** – Pulumi program with YAML-driven infrastructure creation.

## Important changes since this summary

- **Inline database creation in Pulumi is disabled by default.** `run_db_create_inline: false` is recommended. Use `scripts/install_vertica_eon.py` instead.
- **No node-to-node SSH.** TLS bootstrap material is generated locally on the Pulumi runner and deployed to all nodes.
- **IAM instance profile for S3.** `s3_auth_mode: iam_role` is the default; no AWS credentials are stored in config files or on nodes.
- **Public IP for SSH, private IP for Vertica.** The runner connects over the public internet; Vertica internal traffic uses private IPs.
- **Amazon Linux 2023** is the tested operating system.
- **Certificate generation** is handled by `install_vertica_eon.py` rather than `scripts/generate_nma_certs.py`.

## Files that are still relevant

| File | Status |
|------|--------|
| `modules/vertica/vcluster.py` | Reusable wrapper; not required by the Eon installer |
| `modules/cluster_management.py` | Reusable manager; not required by the Eon installer |
| `modules/pulumi_resources.py` | Dynamic resources; optional |
| `scripts/vertica-cli.py` | CLI helper; optional |
| `scripts/install_vertica_eon.py` | **Primary installer for the current workflow** |
| `__main__.py` | **Primary Pulumi program** |

## Next steps for new users

1. Copy `config/vertica-cluster-eon.yaml.example` to `config/config_eon.yaml`.
2. Customize the file for your AWS region, key pair, bucket, RPM, license, and password.
3. Deploy with `pulumi up`.
4. Install Vertica and create the database with `scripts/install_vertica_eon.py`.

See the guides above for the complete, up-to-date instructions.
