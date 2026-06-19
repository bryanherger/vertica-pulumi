# Vertica Eon Mode Deployment Guide

This guide walks through deploying a **3-node Vertica Eon Mode cluster on AWS** using Pulumi. The Pulumi program creates the network, compute, storage, and IAM resources; a separate installer script then installs Vertica and creates the database.

For day-to-day operations after the cluster is running, see [`docs/OPERATIONS.md`](OPERATIONS.md).

---

## What you will build

- A dedicated VPC, subnet, internet gateway, and route table in AWS.
- A security group that exposes the ports Vertica, NMA, and SSH need.
- Three EC2 instances (`r6i.2xlarge` or larger) running **Amazon Linux 2023**.
- A 500 GB gp3 EBS volume mounted at `/data` on every instance (depot + catalog + data).
- A dedicated S3 bucket used as Vertica **communal storage**.
- An IAM instance profile that lets the EC2 instances read and write the communal bucket automatically.
- A Vertica Eon Mode database spread across the three nodes, using the private network for internal traffic.

```
                    S3 Communal Storage
     (All persisted data, shared across all nodes)
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼────┐      ┌────▼────┐      ┌────▼────┐
   │ Node 1  │      │ Node 2  │      │ Node 3  │
   │ + Depot │      │ + Depot │      │ + Depot │
   │ (Cache) │      │ (Cache) │      │ (Cache) │
   └─────────┘      └─────────┘      └─────────┘
```

---

## Important design choices

| Choice | Rationale |
|--------|-----------|
| **No node-to-node SSH** | TLS bootstrap material is generated on the Pulumi runner and uploaded to every node. This removes the need for passwordless root SSH between nodes. |
| **Public IP for SSH, private IP for Vertica** | The Pulumi runner connects to the nodes over the public internet. Vertica's internal communication uses private IPs and security-group rules. |
| **IAM instance profile for S3** | `s3_auth_mode: iam_role` is the default. No AWS access keys are stored in config files or on nodes. |
| **Database creation outside Pulumi** | `run_db_create_inline: false` by default. Pulumi only builds infrastructure. `scripts/install_vertica_eon.py` installs Vertica and creates the database. |

---

## Prerequisites

- An AWS account and an IAM user or role with permission to create VPC, EC2, EBS, IAM, and S3 resources. For a first run, attach **PowerUserAccess + IAMFullAccess**; for production, use the least-privilege policy in [`docs/AWS_ENV_VARS.md`](AWS_ENV_VARS.md).
- The AWS CLI configured locally (`aws configure`) with the region you plan to use.
- A Vertica RPM file for RHEL/Amazon Linux, e.g. `vertica-26.2.0-0.RHEL8.x86_64.rpm`.
- A valid Vertica license XML file (`vertica_license.xml`).
- A local SSH key pair registered in AWS EC2 under **Key pairs**. You will use the key pair **name** in the Pulumi config and the private key **path** when running the installer.

---

## 1. Install Pulumi, AWS CLI, and Python dependencies

### Install Pulumi

```bash
# Linux / WSL
curl -fsSL https://get.pulumi.com | sh
echo 'export PATH=$PATH:$HOME/.pulumi/bin' >> ~/.bashrc
source ~/.bashrc
pulumi version

# macOS
brew install pulumi
pulumi version
```

### Install AWS CLI

```bash
# Amazon Linux / RHEL / Fedora
sudo dnf install awscli

aws --version
aws configure
```

Provide:

- `AWS Access Key ID`
- `AWS Secret Access Key`
- `Default region name` (e.g. `us-east-1`)
- `Default output format` (`json`)

### Clone the repository and install Python dependencies

```bash
git clone https://github.com/bryanherger/vertica-pulumi.git
cd vertica-pulumi

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> `Pulumi.yaml` is configured to use the `venv` virtual environment automatically.

---

## 2. Create the S3 communal storage bucket

Create a dedicated bucket. The example uses `us-east-1`; change the region and bucket name as needed.

```bash
BUCKET_NAME="my-vertica-bucket"
REGION="us-east-1"

aws s3api create-bucket \
    --bucket "$BUCKET_NAME" \
    --region "$REGION"

aws s3api put-public-access-block \
    --bucket "$BUCKET_NAME" \
    --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-versioning \
    --bucket "$BUCKET_NAME" \
    --versioning-configuration Status=Enabled

# Optional: server-side encryption
aws s3api put-bucket-encryption \
    --bucket "$BUCKET_NAME" \
    --server-side-encryption-configuration '{
        "Rules": [{
            "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
            "BucketKeyEnabled": true
        }]
    }'
```

The database sub-path (e.g. `s3://my-vertica-bucket/pulumidb`) should be **empty** before creating the database. Vertica creates the metadata and files automatically.

---

## 3. Stage the Vertica RPM and license

Place the files where the installer can find them. Relative paths are resolved from the project directory.

```bash
cp /path/to/vertica-26.2.0-0.RHEL8.x86_64.rpm ./vertica.rpm
cp /path/to/vertica_license.xml ./vertica_license.xml
```

> Do not commit the RPM or license to Git. They are listed in `.gitignore`; verify with `git status`.

---

## 4. Create the cluster configuration

Copy the Eon example and edit it.

```bash
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
```

Minimum edits in `config/config_eon.yaml`:

```yaml
compute:
  provider: aws
  aws:
    region: us-east-1
    key_name: vertica-automation      # AWS key pair NAME
    instance_type: r6i.2xlarge
    root_volume_size: 100
    connect_via_public_ip: true       # Required from outside the VPC
    run_db_create_inline: false       # Keep false
    s3_auth_mode: iam_role            # Recommended
    additional_volumes:
      - size: 500
        type: gp3
        mount_point: /data

vertica:
  version: "26.2.0-0"
  cluster_name: vertica-eon-cluster
  mode: eon

  license:
    local_path: "./vertica_license.xml"

  rpm:
    local_path: "./vertica.rpm"

  database:
    name: pulumidb
    admin_username: dbadmin
    admin_password: "CHANGE_ME_USE_STRONG_PASSWORD"

  eon:
    communal_storage_location: "s3://my-vertica-bucket/pulumidb"
    shard_count: 3
    depot_path: /data/depot
    depot_size: "80%"
    aws_region: us-east-1
    aws_enable_https: true
    enable_s3_encryption: true

  nodes:
    count: 3
    data_path: /data/vertica
    catalog_path: /data/catalog
```

All keys are documented in [`docs/CONFIGURATION.md`](CONFIGURATION.md).

### S3 authentication modes

- **`s3_auth_mode: iam_role`** (recommended): Pulumi creates an IAM role and instance profile with least-privilege access to the communal bucket, then attaches the profile to every EC2 instance. No AWS keys are stored in config files or on nodes.
- **`s3_auth_mode: access_keys`**: Pulumi does **not** create an IAM role. You must configure AWS credentials on each node or pass `aws_access_key_id` / `aws_secret_access_key` under `vertica.eon`. The installer will use `--get-aws-credentials-from-env-vars` with vcluster in this mode.

---

## 5. Deploy the infrastructure with Pulumi

```bash
source venv/bin/activate

# Create/select a stack
pulumi stack init eon-test

# Point the stack at your config file
pulumi config set vertica:config_file config/config_eon.yaml

# Preview and deploy
pulumi preview
pulumi up
```

`pulumi up` creates:

- VPC, subnet, internet gateway, route table
- Security group with the required ports
- Three EC2 instances with the `/data` volume
- IAM role, policy, and instance profile (when `s3_auth_mode: iam_role`)

After it finishes, export useful values:

```bash
pulumi stack output instance_ips          # public IPs if connect_via_public_ip is true
pulumi stack output instance_private_ips  # private IPs used by Vertica internally
pulumi stack output iam_instance_profile
pulumi stack output db_name
pulumi stack output s3_auth_mode
```

---

## 6. Install Vertica and create the Eon database

Run the installer script from your local machine. It copies the RPM and license to every node, installs Vertica, generates the bootstrap TLS material, and creates the database.

```bash
source venv/bin/activate

python3 scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --ssh-key ~/.ssh/vertica-automation.pem
```

> Replace `~/.ssh/vertica-automation.pem` with the private key that matches your AWS key pair name.

The script performs the following steps:

1. Waits for SSH on all nodes.
2. Uploads the Vertica RPM and license XML.
3. Installs prerequisites and the Vertica RPM.
4. Creates the `dbadmin` user, `/data` directories, and `vertica` system limits.
5. Generates TLS bootstrap certificates **locally** on the runner and deploys them to `/opt/vertica/config/https_certs/` on every node.
6. Starts the Vertica Node Management Agent (NMA) on every node and waits for it to be healthy.
7. Runs `vcluster create_db` on the primary node using **private IPs** for `--hosts` and **public IPs** for SSH.
8. Runs `SELECT sync_catalog();` so the initial metadata is flushed to S3.
9. Verifies the database with `SELECT version();` and `SELECT * FROM nodes;`.

The whole process typically takes **10–25 minutes**.

When it completes you should see:

```text
Installation Complete!
Database: pulumidb
Action: CREATE
Primary Node: <public_ip>
Communal Storage: s3://my-vertica-bucket/pulumidb
Shards: 3
Nodes: 3
```

---

## 7. Verify the cluster

```bash
PRIMARY_IP=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/vertica-automation.pem ec2-user@"$PRIMARY_IP"

# On the primary node
sudo /opt/vertica/bin/vcluster list_all_nodes --config /opt/vertica/config/vertica_cluster.yaml

/opt/vertica/bin/vsql -U dbadmin -d pulumidb -w 'CHANGE_ME_USE_STRONG_PASSWORD' -c "SELECT node_name, node_state FROM nodes;"

# Test S3 access from the node (IAM role path)
aws s3 ls s3://my-vertica-bucket/
```

---

## 8. Important: data persistence

Eon Mode writes data to the **local depot** first and flushes to S3 asynchronously. To avoid losing data:

- The installer runs `sync_catalog()` automatically after database creation.
- Before you destroy the cluster, stop the database gracefully or run `SELECT sync_catalog();` in vsql.
- Do not terminate instances immediately after large data loads without syncing.

---

## Cleanup

When you are done testing:

```bash
# Stop the database first (recommended)
PRIMARY=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/vertica-automation.pem ec2-user@"$PRIMARY" \
    "sudo /opt/vertica/bin/adminTools -t stop_db -d pulumidb -p 'CHANGE_ME_USE_STRONG_PASSWORD'"

# Destroy the Pulumi-managed infrastructure
pulumi destroy

# Optional: remove the stack
pulumi stack rm eon-test
```

The S3 bucket is **not** managed by Pulumi, so it will keep your data. Delete it separately if you no longer need it:

```bash
aws s3 rb s3://my-vertica-bucket --force
```

---

## Troubleshooting

### `pulumi up` fails with an IAM permission error

The Pulumi runner needs IAM write permissions to create roles and instance profiles. For first tests, use `PowerUserAccess + IAMFullAccess`. For least-privilege, see [`docs/AWS_ENV_VARS.md`](AWS_ENV_VARS.md).

### EC2 instances cannot reach S3

From any node:

```bash
aws sts get-caller-identity
aws s3 ls s3://my-vertica-bucket/
```

If the first command works but the second fails, the IAM policy does not cover the bucket path. Check `pulumi stack output iam_instance_profile_arn` and the policy in the AWS console.

### `install_vertica_eon.py` hangs during post-creation vsql

If you are on an older copy of the script, the post-creation verification may use `su - dbadmin -c`, which prompts for a disabled password. The current script uses `sudo -u dbadmin`, which works with the passwordless sudo configuration set up by the bootstrap scripts.

### Database creation fails

1. Verify the NMA is healthy on every node:

   ```bash
   sudo /opt/vertica/bin/manage_node_agent.sh status node_management_agent
   sudo curl -k https://localhost:5554/v1/health
   ```

2. Verify the communal path is empty before a `Create`:

   ```bash
   aws s3 ls s3://my-vertica-bucket/pulumidb/
   ```

3. Retry with force cleanup (this deletes the existing database path):

   ```bash
   python3 scripts/install_vertica_eon.py \
       --config config/config_eon.yaml \
       --ssh-key ~/.ssh/vertica-automation.pem \
       --force-cleanup-on-failure
   ```

---

## Files you will touch

| File | Purpose |
|------|---------|
| `config/vertica-cluster-eon.yaml.example` | Copy to `config/config_eon.yaml` as a starting point |
| `config/config_eon.yaml` | Your actual deployment config (gitignored) |
| `scripts/install_vertica_eon.py` | Post-Pulumi Vertica install and database creation |
| `docs/CONFIGURATION.md` | Complete reference for all YAML keys |
| `docs/OPERATIONS.md` | Start, stop, scale, and revive the cluster |

---

## Next steps

- [`docs/OPERATIONS.md`](OPERATIONS.md) – day-two operations.
- [`docs/CONFIGURATION.md`](CONFIGURATION.md) – all available YAML settings.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) – how the pieces fit together.
- Vertica docs: [Eon Mode](https://docs.vertica.com/26.1.x/en/eon/) and [vcluster commands](https://docs.vertica.com/26.1.x/en/admin/vcluster/vcluster-commands/)
