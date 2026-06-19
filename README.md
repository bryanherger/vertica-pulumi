# Pulumi Vertica Cluster

Infrastructure-as-code deployment and lifecycle management for **Vertica Eon Mode** clusters on AWS, using Pulumi and the `vcluster` CLI.

## What this project does

- **Provision AWS infrastructure** with Pulumi: VPC, subnets, security groups, EC2 instances, EBS volumes, IAM instance profile, and S3 bucket.
- **Install Vertica** and create an Eon Mode database with `scripts/install_vertica_eon.py`.
- **Avoid node-to-node SSH** by generating TLS bootstrap material on the Pulumi runner and uploading it to all nodes.
- **Use IAM instance profiles** so nodes can read/write S3 communal storage without storing long-term credentials.

For a complete walkthrough, see **[README_EON.md](README_EON.md)** or the longer **[docs/DEPLOYMENT_EON.md](docs/DEPLOYMENT_EON.md)**.

## Quick start (Eon Mode on AWS)

```bash
# 1. Clone the repository and install dependencies
git clone https://github.com/bryanherger/vertica-pulumi.git
cd vertica-pulumi

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Stage the Vertica RPM and license in the project directory
cp /path/to/vertica-26.2.0-0.RHEL8.x86_64.rpm ./vertica.rpm
cp /path/to/vertica_license.xml ./vertica_license.xml

# 3. Create an S3 bucket for communal storage
export BUCKET_NAME=my-vertica-bucket
export REGION=us-east-1
aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION"

# 4. Create your cluster config from the Eon example
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
# Edit the file: key_name, region, connect_via_public_ip, communal_storage_location,
# admin_password, rpm.local_path, license.local_path

# 5. Deploy the infrastructure
pulumi stack init eon-test
pulumi config set vertica:config_file config/config_eon.yaml
pulumi up

# 6. Install Vertica and create the database
python3 scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --ssh-key ~/.ssh/vertica-automation.pem

# 7. Verify
PRIMARY_IP=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/vertica-automation.pem ec2-user@"$PRIMARY_IP" \
    "sudo /opt/vertica/bin/vsql -U dbadmin -d pulumidb -c 'SELECT node_name, node_state FROM nodes;'"
```

## Project layout

```
pulumi-vertica-cluster/
├── __main__.py                          # Main Pulumi program
├── Pulumi.yaml                          # Pulumi project config
├── requirements.txt                     # Python dependencies
├── config/
│   ├── config.yaml                      # Legacy/default config
│   ├── config_eon.yaml                  # Example Eon config used during development
│   ├── vertica-cluster-eon.yaml.example # Current Eon example (recommended starting point)
│   └── vertica-cluster.yaml.example     # Legacy Enterprise example
├── modules/
│   ├── __init__.py
│   ├── pulumi_resources.py              # AWS network/compute builders
│   ├── pulumi_vertica_resources.py      # Vertica-specific Pulumi resources
│   └── ...
├── scripts/
│   ├── install_vertica_eon.py         # End-to-end Eon installer (current)
│   ├── generate_eon_config.py         # Helper to generate config_eon.yaml
│   ├── generate_nma_certs.py          # Legacy NMA-only cert helper
│   ├── install_vertica.py               # Legacy Enterprise installer
│   ├── install_vertica_ee.py           # Legacy Enterprise installer
│   └── vertica-cli.py                  # vcluster command wrapper
├── docs/
│   ├── DEPLOYMENT_EON.md              # Complete deployment guide
│   ├── CONFIGURATION.md               # Full YAML reference
│   ├── ARCHITECTURE.md                # Design and architecture
│   ├── OPERATIONS.md                  # Day-two operations
│   └── AWS_ENV_VARS.md                # AWS credentials and IAM guidance
└── tests/
    └── test_compute.py                # Unit tests
```

## Current workflow

The maintained end-to-end path is **Eon Mode on AWS**:

1. Pulumi creates infrastructure from `config/config_eon.yaml`.
2. `scripts/install_vertica_eon.py` installs Vertica, generates TLS material locally, and runs `vcluster create_db`.
3. The database is created with private-IP internal communication and IAM-profile S3 access.

`scripts/install_vertica.py`, `scripts/install_vertica_ee.py`, and the `vertica-cluster.yaml.example` config are **legacy Enterprise Mode** artifacts and are not part of the current recommended flow.

## Key design decisions

| Decision | Why |
|----------|-----|
| Public IP for SSH, private IP for Vertica | Allows running Pulumi from outside the target VPC while keeping internal Vertica traffic private. |
| No node-to-node SSH | TLS certs generated locally and uploaded to all nodes, simplifying security and automation. |
| IAM instance profile for S3 | No AWS secrets stored on nodes or in config files. |
| Database creation outside Pulumi | More reliable, easier to debug, and handles RPM/license/NMA/TLS setup. |

## Configuration

Configuration is YAML-driven. Pulumi reads the file named by the stack key `vertica:config_file` or the `VERTICA_CONFIG_FILE` environment variable.

Start from the annotated example:

```bash
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
```

All keys are documented in **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Operations

After the cluster is running, see **[docs/OPERATIONS.md](docs/OPERATIONS.md)** for start, stop, scale, revive, and troubleshooting guidance.

## Testing

```bash
# Run unit tests
python -m pytest tests/ -v
```

## Enterprise Mode (legacy)

The original Enterprise Mode scripts and documentation remain in the repository for reference but are not actively maintained:

- `scripts/install_vertica.py`
- `scripts/install_vertica_ee.py`
- `config/vertica-cluster.yaml.example`
- `README_EE.md`

For new deployments, use the Eon Mode workflow documented above.

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Add tests for new behavior.
4. Submit a pull request.

## License

MIT
