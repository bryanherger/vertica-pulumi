# Vertica Eon Mode Deployment - Quick Start

Deploy Vertica in **Eon Mode** on AWS using Pulumi and `vcluster`. Eon Mode separates compute from storage: all persisted data lives in S3 communal storage while each node keeps a local depot cache for fast reads.

## What this deploys

```
┌─────────────────────────────────────────┐
│           S3 Communal Storage           │
│     (All data, shared across nodes)      │
└─────────────────────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
┌───▼───┐    ┌───▼───┐    ┌───▼───┐
│ Node 1│    │ Node 2│    │ Node 3│
│+ Depot│    │+ Depot│    │+ Depot│
└───────┘    └───────┘    └───────┘
```

## Prerequisites

- AWS account with permission to create VPC, EC2, EBS, IAM, and S3 resources.
- [Pulumi](https://www.pulumi.com/docs/install/) installed locally.
- A Vertica RPM file for Amazon Linux / RHEL, e.g. `vertica-26.2.0-0.RHEL8.x86_64.rpm`.
- A valid Vertica license XML file.
- An SSH key pair registered in AWS EC2. You will use the key pair **name** in YAML and the private key **path** when running the installer.

## TL;DR - deploy a 3-node Eon cluster

```bash
# 1. Clone the repo and install Python dependencies
git clone https://github.com/bryanherger/vertica-pulumi.git
cd vertica-pulumi

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Stage the Vertica RPM and license in the project directory
cp /path/to/vertica-26.2.0-0.RHEL8.x86_64.rpm ./vertica.rpm
cp /path/to/vertica_license.xml ./vertica_license.xml

# 3. Create an S3 bucket for communal storage (replace with a unique name)
export BUCKET_NAME=my-vertica-bucket
export REGION=us-east-1

aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION"
aws s3api put-public-access-block --bucket "$BUCKET_NAME" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# 4. Configure the cluster
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
# Edit config_eon.yaml:
#   - compute.aws.key_name          (key pair NAME)
#   - compute.aws.region
#   - compute.aws.connect_via_public_ip  (true if running outside the VPC)
#   - vertica.eon.communal_storage_location (use your bucket)
#   - vertica.database.admin_password
#   - vertica.rpm.local_path and vertica.license.local_path

# 5. Create/select a Pulumi stack and point it at the config
pulumi stack init eon-test
pulumi config set vertica:config_file config/config_eon.yaml

# 6. Deploy the AWS infrastructure
pulumi up

# 7. Install Vertica and create the database
python3 scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --ssh-key ~/.ssh/vertica-automation.pem

# 8. Verify
PRIMARY_IP=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/vertica-automation.pem ec2-user@"$PRIMARY_IP" \
    "sudo /opt/vertica/bin/vsql -U dbadmin -d pulumidb -c 'SELECT node_name, node_state FROM nodes;'"
```

## Design highlights

- **No node-to-node SSH**: TLS bootstrap material is generated on the Pulumi runner and uploaded to every node.
- **Public IP for SSH, private IP for Vertica**: the installer connects over the public internet, but `vcluster` uses private IPs for internal cluster traffic.
- **Database creation outside Pulumi**: `run_db_create_inline` is `false` by default; the dedicated installer is more reliable and easier to debug.

## S3 authentication

### IAM instance profile (recommended)

The Pulumi program creates and attaches an IAM instance profile automatically when you set:

```yaml
compute:
  aws:
    s3_auth_mode: iam_role
```

To use a pre-existing profile instead:

```yaml
compute:
  aws:
    s3_auth_mode: iam_role
    iam_instance_profile: my-existing-profile-name
```

### Access keys

If IAM instance profiles are not available, set `s3_auth_mode: access_keys` and provide credentials under `vertica.eon.aws_access_key_id` / `vertica.eon.aws_secret_access_key`. See `docs/DEPLOYMENT_EON.md` for details.

## Important: data persistence

Eon Mode writes to the local depot first and flushes to S3 asynchronously. The installer runs `SELECT sync_catalog();` automatically after database creation. Before destroying the cluster, run:

```sql
SELECT sync_catalog();
```

or stop the database gracefully.

## Cleanup

```bash
# Stop the database first (recommended)
PRIMARY=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/vertica-automation.pem ec2-user@"$PRIMARY" \
    "sudo /opt/vertica/bin/adminTools -t stop_db -d pulumidb -p 'CHANGE_ME_USE_STRONG_PASSWORD'"

# Destroy Pulumi-managed infrastructure
pulumi destroy
pulumi stack rm eon-test

# The S3 bucket is not managed by Pulumi; delete it separately if desired.
aws s3 rb s3://my-vertica-bucket --force
```

## Next steps

- [Full Deployment Guide](docs/DEPLOYMENT_EON.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [Configuration Reference](docs/CONFIGURATION.md)
- [Operations Guide](docs/OPERATIONS.md)
- [Vertica Eon Mode Docs](https://docs.vertica.com/26.1.x/en/eon/)
