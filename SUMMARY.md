# Project Summary

This repository contains Pulumi infrastructure code and deployment scripts for Vertica database clusters on AWS.

## Modes Supported

| Mode | Use Case | Key Files |
|------|----------|-----------|
| **Enterprise (EE)** | Traditional, data-local mode | `README_EE.md`, `scripts/install_vertica_ee.py`, `config/vertica-cluster.yaml.example` |
| **Eon** | Cloud-optimized, separate compute/storage | `README_EON.md`, `scripts/install_vertica_eon.py`, `config/vertica-cluster-eon.yaml.example` |

## Quick Links

- [Enterprise Mode Quick Start](README_EE.md)
- [Eon Mode Quick Start](README_EON.md)
- [Architecture Documentation](docs/ARCHITECTURE.md)
- [Enterprise Deployment Guide](docs/DEPLOYMENT.md)
- [Eon Deployment Guide](docs/DEPLOYMENT_EON.md)
- [Configuration Reference](docs/CONFIGURATION.md)

## Repository Structure

```
├── config/                          # Configuration templates
│   ├── vertica-cluster.yaml.example        # EE mode config
│   └── vertica-cluster-eon.yaml.example  # Eon mode config
├── docs/                            # Documentation
│   ├── ARCHITECTURE.md              # System design
│   ├── DEPLOYMENT.md                # EE deployment guide
│   ├── DEPLOYMENT_EON.md            # Eon deployment guide
│   ├── CONFIGURATION.md             # Config reference
│   └── AWS_ENV_VARS.md              # AWS setup
├── modules/                         # Python modules
│   ├── compute/                     # AWS/bare metal compute
│   ├── deployment/                  # Pulumi deployment logic
│   └── vertica/                     # Vertica management
│       ├── install.py               # Installation helpers
│       ├── configure.py             # Configuration
│       ├── rest_api.py              # REST API client
│       └── vcluster.py              # vcluster wrapper
├── scripts/                         # Deployment scripts
│   ├── install_vertica_ee.py        # EE mode installer
│   ├── install_vertica_eon.py       # Eon mode installer
│   ├── generate_nma_certs.py      # Certificate generator
│   ├── generate_eon_config.py     # Eon config generator
│   └── vertica-cli.py               # Management CLI
├── __main__.py                      # Pulumi entry point
├── README.md                        # Main readme
├── README_EE.md                     # EE quick start
├── README_EON.md                    # Eon quick start
└── requirements.txt                 # Python dependencies
```

## Key Features

### Infrastructure (Pulumi)
- Multi-node EC2 deployment with configurable instance types
- VPC, subnet, and security group management
- SSH key-based access
- Additional EBS volumes for data/catalog/depot
- Cloud-init bootstrap with prerequisite installation

### Enterprise Mode
- Full automated Vertica EE installation
- RPM and license distribution to all nodes
- Database creation with admintools
- Cluster status verification

### Eon Mode
- S3 communal storage configuration
- Depot location and size management
- Shard count configuration
- Automatic RSA key/certificate generation
- NMA (Node Management Agent) certificate deployment
- NMA and HTTPS service startup
- vcluster-based database creation

## Scripts Reference

| Script | Purpose | Usage |
|--------|---------|-------|
| `install_vertica_ee.py` | EE mode full installation | `python scripts/install_vertica_ee.py --config config.yaml --rpm-path ...` |
| `install_vertica_eon.py` | Eon mode full installation | `python scripts/install_vertica_eon.py --config config_eon.yaml --rpm-path ...` |
| `generate_nma_certs.py` | Generate + deploy NMA certs | `python scripts/generate_nma_certs.py --hosts ... --ssh-key ...` |
| `generate_eon_config.py` | Interactive config generator | `python scripts/generate_eon_config.py --interactive` |
| `vertica-cli.py` | Cluster management CLI | `python scripts/vertica-cli.py --help` |

## Requirements

- Python 3.6+
- Pulumi CLI
- AWS CLI (configured)
- Vertica RPM file
- Vertica license (26.1+)
- PyYAML: `pip install pyyaml`
- Pulumi packages: `pip install -r requirements.txt`

## Getting Started

1. Choose your mode (EE or Eon)
2. Read the corresponding quick start guide
3. Generate or edit configuration
4. Deploy infrastructure with Pulumi
5. Run the installation script
6. Verify database connectivity
