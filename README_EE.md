# Vertica Enterprise Edition (EE) Deployment (Legacy)

> ⚠️ This guide is **legacy**. The current recommended deployment path is **Eon Mode on AWS** using `scripts/install_vertica_eon.py`.
>
> For current instructions, see:
> - **[README_EON.md](README_EON.md)** – quick start
> - **[docs/DEPLOYMENT_EON.md](docs/DEPLOYMENT_EON.md)** – full guide
> - **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** – configuration reference

The Enterprise Mode scripts (`scripts/install_vertica.py`, `scripts/install_vertica_ee.py`) and the `config/vertica-cluster.yaml.example` file remain in the repository for historical reference but are not actively maintained or tested against the latest Vertica releases.

## What changed?

The project shifted to **Eon Mode** because:

- **S3 communal storage** separates compute from storage, making the cluster easier to stop, start, and recover.
- **IAM instance profiles** remove the need to embed or manage AWS credentials.
- **Local TLS generation** on the Pulumi runner removes the node-to-node SSH requirement.
- **`vcluster`** is the modern command-line interface for cluster and database management.

## Historical EE quick start

The following steps are the original Enterprise Mode workflow. They are provided for reference only.

### Prerequisites

- Vertica Enterprise Edition RPM.
- Vertica license XML file.
- AWS account with EC2/VPC/EBS IAM permissions.
- AWS CLI and Pulumi CLI.
- SSH key pair in AWS EC2.

### Configure

```bash
cp config/vertica-cluster.yaml.example config/config.yaml
# Edit compute.aws.region, compute.aws.key_name, vertica.rpm.local_path, vertica.license.local_path, etc.
```

### Deploy infrastructure

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

pulumi stack init my-cluster
pulumi up
```

### Install Vertica

```bash
python scripts/install_vertica_ee.py \
  --config config/config.yaml \
  --rpm-path ~/Downloads/vertica-25.4.0-6.RHEL8.x86_64.rpm \
  --license-path ~/Downloads/vertica_license.xml \
  --ssh-key ~/.ssh/pulumi.pem
```

### Create and verify the database

SSH to the primary node and use `admintools` to create the database:

```bash
sudo su - dbadmin
/opt/vertica/bin/admintools -t create_db \
  --hosts=localhost \
  --database=analytics \
  --password='CHANGE_ME_USE_STRONG_PASSWORD' \
  --catalog_path=/data/catalog \
  --data_path=/data/vertica

/opt/vertica/bin/vsql -U dbadmin -d analytics -w 'CHANGE_ME_USE_STRONG_PASSWORD' -c "SELECT version();"
```

### Cleanup

```bash
pulumi destroy --yes
pulumi stack rm my-cluster --yes
```

## Files

- `config/vertica-cluster.yaml.example` – original Enterprise Mode template.
- `scripts/install_vertica.py` – legacy installer.
- `scripts/install_vertica_ee.py` – legacy Enterprise Edition installer.

---

Use the Eon Mode documentation for any new deployments.
