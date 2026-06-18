# Vertica Eon Mode Deployment - Quick Start

Deploy Vertica in **Eon Mode** on AWS using Pulumi and `vcluster`. Eon Mode separates compute from storage: all data lives in S3 communal storage while each node keeps a local depot cache.

## What this deploys

```
┌─────────────────────────────────────────┐
│           S3 Communal Storage          │
│     (All data, shared across nodes)      │
└─────────────────────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
┌───▼───┐    ┌───▼───┐    ┌───▼───┐
│ Node 1 │    │ Node 2 │    │ Node 3 │
│+ Depot │    │+ Depot │    │+ Depot │
└────────┘    └────────┘    └────────┘
```

## Prerequisites

- AWS account with permission to create VPC, EC2, EBS, IAM, and S3 resources
- Vertica RPM file (e.g. `vertica-25.4.0-6.RHEL8.x86_64.rpm`)
- Valid Vertica license XML file
- SSH key pair registered in AWS EC2
- Pulumi installed locally (see full guide for install steps)

## TL;DR - deploy a 3-node Eon cluster

```bash
# 1. Install Pulumi and clone the repo
curl -fsSL https://get.pulumi.com | sh
git clone https://github.com/bryanherger/vertica-pulumi.git
cd vertica-pulumi

# 2. Install Python dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Stage Vertica RPM and license in the project directory
cp /path/to/vertica-25.4.0-6.RHEL8.x86_64.rpm ./vertica.rpm
cp /path/to/vertica_license.xml ./vertica_license.xml

# 4. Create an S3 bucket for communal storage (replace with a unique name)
aws s3api create-bucket \
    --bucket my-vertica-eon-bucket \
    --region us-east-2 \
    --create-bucket-configuration LocationConstraint=us-east-2

# 5. Configure the cluster
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
# Edit config_eon.yaml:
#   - compute.aws.key_name
#   - compute.aws.region
#   - vertica.eon.communal_storage_location (use your bucket name)
#   - vertica.database.admin_password
#   - vertica.rpm.local_path and vertica.license.local_path

# 6. Deploy the AWS infrastructure
export VERTICA_CONFIG=config/config_eon.yaml
pulumi stack init eon-test
pulumi up

# 7. Install Vertica and create the database
python3 scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --ssh-key ~/.ssh/pulumi.pem

# 8. Verify
PRIMARY_IP=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/pulumi.pem ec2-user@"$PRIMARY_IP" \
    "/opt/vertica/bin/vsql -U dbadmin -d eon_test_db -c 'SELECT node_name, node_state FROM nodes;'"
```

## IAM instance profile (recommended)

The Pulumi program can create and attach an IAM instance profile automatically. In `config_eon.yaml`:

```yaml
compute:
  aws:
    s3_auth_mode: iam_role   # Pulumi creates the role + instance profile
```

If you prefer to use a pre-existing profile:

```yaml
compute:
  aws:
    s3_auth_mode: iam_role
    iam_instance_profile: my-existing-profile-name
```

If IAM instance profiles are not available in your environment, set `s3_auth_mode: access_keys` and configure AWS credentials on each node manually. See `docs/DEPLOYMENT_EON.md` for details.

## Full guide

For detailed step-by-step instructions, including Pulumi installation on Linux/macOS/Windows, AWS credential setup, all configuration options, verification commands, and troubleshooting, see:

**[docs/DEPLOYMENT_EON.md](docs/DEPLOYMENT_EON.md)**

## Important: data persistence

Eon Mode writes to the local depot first and flushes to S3 asynchronously. Always call `SELECT sync_catalog();` before destroying the cluster to make sure all data is persisted in S3.

## Cleanup

```bash
pulumi destroy
pulumi stack rm eon-test
# The S3 bucket is not managed by Pulumi; delete it separately if desired.
aws s3 rb s3://my-vertica-eon-bucket --force
```

## Other resources

- [Architecture Overview](docs/ARCHITECTURE.md)
- [Configuration Reference](docs/CONFIGURATION.md)
- [Operations Guide](docs/OPERATIONS.md)
- [Vertica Eon Mode Docs](https://docs.vertica.com/26.1.x/en/eon/)
