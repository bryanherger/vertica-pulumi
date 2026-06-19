# Deployment Guide

The recommended deployment path for this repository is **Eon Mode on AWS**. See the dedicated guides for full details:

- **[README_EON.md](../README_EON.md)** – quick start
- **[docs/DEPLOYMENT_EON.md](DEPLOYMENT_EON.md)** – complete step-by-step guide
- **[docs/CONFIGURATION.md](CONFIGURATION.md)** – all YAML configuration options
- **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** – design overview

The page below provides a short generic checklist. For production use, follow `DEPLOYMENT_EON.md`.

---

## Prerequisites

- [Pulumi](https://www.pulumi.com/docs/install/) installed locally.
- AWS CLI configured with credentials and region (`aws configure`).
- Vertica RPM and license XML file.
- SSH key pair registered in AWS EC2.

## Installation

```bash
curl -fsSL https://get.pulumi.com | sh

git clone https://github.com/bryanherger/vertica-pulumi.git
cd vertica-pulumi

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

1. Copy the Eon Mode example:

   ```bash
   cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
   ```

2. Edit `config/config_eon.yaml`:

   - `compute.aws.region`
   - `compute.aws.key_name`
   - `compute.aws.connect_via_public_ip` (use `true` outside the VPC)
   - `compute.aws.s3_auth_mode` (`iam_role` recommended)
   - `vertica.rpm.local_path`
   - `vertica.license.local_path`
   - `vertica.database.admin_password`
   - `vertica.eon.communal_storage_location`

3. Point Pulumi at the config file:

   ```bash
   pulumi stack init eon-test
   pulumi config set vertica:config_file config/config_eon.yaml
   ```

## Deploy

```bash
pulumi preview
pulumi up
```

## Install Vertica and create the database

```bash
python scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --ssh-key ~/.ssh/vertica-automation.pem
```

## Verify

```bash
PRIMARY_IP=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/vertica-automation.pem ec2-user@"$PRIMARY_IP" \
    "sudo /opt/vertica/bin/vsql -U dbadmin -d pulumidb -c 'SELECT node_name, node_state FROM nodes;'"
```

## Cleanup

```bash
pulumi destroy
pulumi stack rm eon-test
```

The S3 bucket is not managed by Pulumi; delete it separately if needed.

## Legacy deployment modes

- **Enterprise Mode** on AWS is no longer maintained. See `README_EE.md` for historical notes.
- **Bare Metal** support exists in code but is not part of the current tested workflow.
