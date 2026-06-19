# Project Summary

This repository deploys Vertica database clusters on AWS using Pulumi. The maintained and tested workflow is **Eon Mode on AWS**.

## Modes Supported

| Mode | Use Case | Key Files |
|------|----------|-----------|
| **Eon** (current) | Cloud-optimized, separate compute/storage | `README_EON.md`, `scripts/install_vertica_eon.py`, `config/vertica-cluster-eon.yaml.example` |
| **Enterprise (EE)** (legacy) | Traditional data-local mode | `README_EE.md`, `scripts/install_vertica_ee.py`, `config/vertica-cluster.yaml.example` |

## Quick Links

- **[Eon Mode Quick Start](README_EON.md)**
- **[Eon Mode Deployment Guide](docs/DEPLOYMENT_EON.md)**
- **[Configuration Reference](docs/CONFIGURATION.md)**
- **[Architecture Overview](docs/ARCHITECTURE.md)**
- **[Operations Guide](docs/OPERATIONS.md)**
- **[Enterprise Mode Notes (legacy)](README_EE.md)**

## Repository Structure

```
├── config/                              # Configuration templates
│   ├── vertica-cluster-eon.yaml.example # Current Eon config example
│   ├── vertica-cluster.yaml.example     # Legacy Enterprise config example
│   ├── config.yaml                      # Default config (Eon shape)
│   └── config_eon_test.yaml             # Test fixture
├── docs/                                # Documentation
│   ├── DEPLOYMENT_EON.md              # Complete Eon deployment guide
│   ├── DEPLOYMENT.md                  # Generic deployment pointers
│   ├── CONFIGURATION.md               # All YAML options
│   ├── ARCHITECTURE.md                # Design overview
│   ├── OPERATIONS.md                  # Day-two operations
│   └── AWS_ENV_VARS.md                # AWS setup and IAM
├── modules/                             # Python modules
│   ├── pulumi_resources.py              # AWS resource builders
│   ├── pulumi_vertica_resources.py      # Vertica-specific resources
│   ├── compute/                         # Compute provider logic (legacy/optional)
│   ├── deployment/                      # Pulumi deployment logic (legacy/optional)
│   └── vertica/                         # Vertica management helpers (legacy/optional)
├── scripts/                             # Deployment and helper scripts
│   ├── install_vertica_eon.py         # Main Eon installer
│   ├── generate_eon_config.py         # Eon config generator
│   ├── generate_nma_certs.py          # Legacy NMA-only cert generator
│   ├── install_vertica.py             # Legacy Enterprise installer
│   ├── install_vertica_ee.py          # Legacy Enterprise installer
│   └── vertica-cli.py                   # Optional management CLI
├── __main__.py                          # Main Pulumi program
├── README.md                            # Project overview
├── README_EON.md                        # Eon quick start
├── README_EE.md                         # Legacy Enterprise notes
└── requirements.txt                     # Python dependencies
```

## Key Features

### Infrastructure (Pulumi)

- VPC, subnet, internet gateway, route table, and security group.
- Multi-node EC2 deployment on Amazon Linux 2023.
- Dedicated EBS volume mounted at `/data` for depot, catalog, and data.
- IAM instance profile for S3 communal storage access (no embedded keys).
- Public-IP SSH support for external Pulumi runners.
- Private-IP internal Vertica communication.
- Cloud-init bootstrap with prerequisite installation.

### Eon Mode Workflow

- S3 communal storage.
- Local depot with configurable size and path.
- TLS bootstrap material generated on the runner and deployed to all nodes (no node-to-node SSH).
- Node Management Agent (NMA) and HTTPS service startup.
- `vcluster create_db` / `vcluster revive_db` database creation.
- Post-creation catalog sync and verification.

## Scripts Reference

| Script | Purpose | Usage |
|--------|---------|-------|
| `install_vertica_eon.py` | Full Eon installation and database creation | `python scripts/install_vertica_eon.py --config config/config_eon.yaml --ssh-key ~/.ssh/key.pem` |
| `generate_eon_config.py` | Interactive/config-driven Eon config generator | `python scripts/generate_eon_config.py --interactive` |
| `install_vertica_ee.py` | Legacy Enterprise installer (not maintained) | `python scripts/install_vertica_ee.py --config config/config.yaml --rpm-path ...` |
| `generate_nma_certs.py` | Legacy NMA-only cert generator | Not needed for current Eon flow |
| `vertica-cli.py` | Optional cluster management CLI | `python scripts/vertica-cli.py --help` |

## Requirements

- Python 3.8+
- Pulumi CLI
- AWS CLI configured with credentials
- Vertica RPM file
- Vertica license XML (26.1+)
- `pip install -r requirements.txt`

## Getting Started

1. Read [README_EON.md](README_EON.md).
2. Copy `config/vertica-cluster-eon.yaml.example` to `config/config_eon.yaml` and edit it.
3. Deploy infrastructure with `pulumi up`.
4. Install Vertica and create the database with `scripts/install_vertica_eon.py`.
5. Verify database connectivity.

The Enterprise Mode scripts and documentation remain in the repository for historical reference but are no longer the recommended path.
