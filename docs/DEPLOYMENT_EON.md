# Vertica Eon Mode Deployment Guide

This guide walks through deploying a **3-node Vertica Eon Mode cluster on AWS** using Pulumi. It covers everything from installing Pulumi to running a database on S3 communal storage.

For day-to-day operations after the cluster is running, see `docs/OPERATIONS.md`.

---

## What you will build

- A dedicated VPC, subnet, and security group in AWS.
- Three EC2 instances (`r6i.2xlarge` or larger) running Amazon Linux 2023.
- A dedicated S3 bucket used as Vertica **communal storage**.
- An IAM instance profile that lets the EC2 instances read and write the communal bucket automatically.
- A Vertica Eon Mode database spread across the three nodes.

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
│ (Cache)│    │ (Cache)│    │ (Cache)│
└────────┘    └────────┘    └────────┘
```

---

## Prerequisites

- An AWS account and an IAM user or role with permissions to create VPC, EC2, EBS, IAM, and S3 resources. The easiest option for a first test is a user with **PowerUserAccess + IAMFullAccess**.
- A Vertica RPM file for RHEL/Amazon Linux, e.g. `vertica-25.4.0-6.RHEL8.x86_64.rpm`.
- A valid Vertica license XML file (`vertica_license.xml`).
- A local SSH key pair registered in AWS EC2 under **Key pairs**. The key name (not the file path) goes in the Pulumi config.

---

## 1. Install Pulumi and AWS CLI

### Install Pulumi

**Linux / WSL**

```bash
curl -fsSL https://get.pulumi.com | sh
# Add to your PATH; the installer prints the exact line for your shell
echo 'export PATH=$PATH:$HOME/.pulumi/bin' >> ~/.bashrc
source ~/.bashrc
pulumi version
```

**macOS**

```bash
brew install pulumi
pulumi version
```

**Windows**

```powershell
 winget install Pulumi.Pulumi
 pulumi version
```

### Install AWS CLI

```bash
# Amazon Linux / RHEL / Fedora
sudo dnf install awscli

# macOS
brew install awscli

# Verify
aws --version
aws configure
```

When you run `aws configure`, provide:

- `AWS Access Key ID`
- `AWS Secret Access Key`
- `Default region name` (use the same region you will put in the Pulumi config, e.g. `us-east-2`)
- `Default output format` (`json` or `table`)

Pulumi will automatically pick up these credentials when `pulumi up` runs.

---

## 2. Clone the repository and install Python dependencies

```bash
git clone https://github.com/bryanherger/vertica-pulumi.git
cd vertica-pulumi

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> The `Pulumi.yaml` file tells Pulumi to use the `venv` virtual environment automatically.

---

## 3. Create the S3 communal storage bucket

Create a dedicated bucket. Replace `my-vertica-eon-bucket` with a globally unique name and `us-east-2` with your region.

```bash
BUCKET_NAME="my-vertica-eon-bucket"
REGION="us-east-2"

aws s3api create-bucket \
    --bucket "$BUCKET_NAME" \
    --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"

aws s3api put-public-access-block \
    --bucket "$BUCKET_NAME" \
    --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-versioning \
    --bucket "$BUCKET_NAME" \
    --versioning-configuration Status=Enabled
```

Make the bucket **empty** inside. Vertica will create the database sub-path automatically.

---

## 4. Stage the Vertica RPM and license

The simplest way is to place the files in the project directory. They are copied to each EC2 instance by the installation script.

```bash
# Copy your local files into the project
cp /path/to/vertica-25.4.0-6.RHEL8.x86_64.rpm ./vertica.rpm
cp /path/to/vertica_license.xml ./vertica_license.xml
```

> Do not commit the RPM or license to Git. They are already ignored by `.gitignore`, but double-check with `git status`.

---

## 5. Create the cluster configuration

Copy the Eon Mode example and edit it for your environment.

```bash
cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml
```

Minimum changes in `config/config_eon.yaml`:

```yaml
compute:
  provider: aws
  aws:
    region: us-east-2
    key_name: pulumi                 # Name of your AWS EC2 key pair
    instance_type: r6i.2xlarge       # 3-node test cluster
    root_volume_size: 100
    s3_auth_mode: iam_role           # Pulumi creates the IAM instance profile
    additional_volumes:
      - size: 500
        type: gp3
        mount_point: /data
    security_group_rules:
      - protocol: tcp
        port: 5433
        cidr: 0.0.0.0/0
      - protocol: tcp
        port: 22
        cidr: 0.0.0.0/0
      - protocol: tcp
        port: 5444
        cidr: 0.0.0.0/0
      - protocol: tcp
        port: 8443
        cidr: 0.0.0.0/0
      - protocol: tcp
        port: 5554
        cidr: 0.0.0.0/0

vertica:
  version: "25.4.0-6"
  cluster_name: vertica-eon-test
  mode: eon

  license:
    local_path: "./vertica_license.xml"

  rpm:
    local_path: "./vertica.rpm"

  database:
    name: eon_test_db
    admin_username: dbadmin
    # Set a strong password. Prefer Pulumi secrets for real environments.
    admin_password: "CHANGE_ME_USE_STRONG_PASSWORD"

  eon:
    communal_storage_location: "s3://my-vertica-eon-bucket/eon_test_db"
    shard_count: 3
    depot_path: /data/depot
    depot_size: "80%"
    aws_region: "us-east-2"
    aws_enable_https: true
    enable_s3_encryption: true
    dbinit: Create          # Create a new database; use Revive to start an existing one

  nodes:
    count: 3
    data_path: /data/vertica
    catalog_path: /data/catalog

  network:
    port: 5433
    rest_api_port: 5444

bootstrap:
  prerequisites:
    - dialog
    - pcre
    - pcre2
    - sysstat
    - libxcrypt-compat
  packages:
    - vim
    - htop
    - tmux
    - wget
    - net-tools
    - psmisc
    - lsof
    - aws-cli
  pre_install:
    - "sysctl -w vm.max_map_count=262144"
    - "echo 'vm.max_map_count=262144' >> /etc/sysctl.conf"
    - "echo 'vm.swappiness=1' >> /etc/sysctl.conf"
    - "sysctl -p"
    - "fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile"
    - "echo '/swapfile none swap sw 0 0' >> /etc/fstab"
    - "echo 'dbadmin soft nofile 65536' >> /etc/security/limits.conf"
    - "echo 'dbadmin hard nofile 65536' >> /etc/security/limits.conf"
    - "mkdir -p /data/depot"
  post_install:
    - "echo 'Bootstrap complete' >> /var/log/vertica-bootstrap.log"
```

### S3 authentication modes

- **`s3_auth_mode: iam_role`** (recommended): Pulumi creates an IAM role and instance profile with least-privilege access to the communal bucket, then attaches the profile to every EC2 instance. No AWS keys are stored in config files.
- **`s3_auth_mode: access_keys`**: Pulumi does **not** create an IAM role. You must configure AWS credentials on each node manually after deployment. Use this only when IAM instance profiles are not available.

For tomorrow’s test, use `iam_role`.

---

## 6. Deploy the infrastructure with Pulumi

```bash
# Activate the virtual environment again if you opened a new shell
source venv/bin/activate

# Create a Pulumi stack for this deployment
pulumi stack init eon-test

# Tell Pulumi which config file to use (optional but recommended)
export VERTICA_CONFIG=config/config_eon.yaml

# Preview what will be created
pulumi preview

# Deploy
pulumi up
```

`pulumi up` will create:

- VPC, subnet, internet gateway, route table
- Security group with Vertica, SSH, and NMA ports
- Three EC2 instances with 500 GB gp3 depot volumes
- IAM role, policy, and instance profile for S3 access

After it finishes, export useful values:

```bash
pulumi stack output instance_ips
pulumi stack output iam_instance_profile
```

---

## 7. Install Vertica and create the Eon database

Run the Eon installer script from your local machine. It copies the RPM and license to every node, installs Vertica, generates NMA certificates, and creates the database.

```bash
source venv/bin/activate

python3 scripts/install_vertica_eon.py \
    --config config/config_eon.yaml \
    --ssh-key ~/.ssh/pulumi.pem
```

> Replace `~/.ssh/pulumi.pem` with the private key file that matches the AWS key pair name you used.

The script does the following:

1. Uploads the RPM and license XML to all nodes.
2. Installs the Vertica RPM.
3. Generates and deploys NMA TLS certificates.
4. Starts the Vertica Node Management Agent on each node.
5. Creates the Eon Mode database on S3 communal storage with `vcluster create_db`.
6. Calls `sync_catalog()` so the initial metadata is flushed to S3.

The whole process typically takes 10–20 minutes.

---

## 8. Verify the cluster

```bash
# SSH to the primary node
PRIMARY_IP=$(pulumi stack output instance_ips | head -1)
ssh -i ~/.ssh/pulumi.pem ec2-user@"$PRIMARY_IP"

# Check node status
sudo /opt/vertica/bin/vcluster list_all_nodes \
    --config /opt/vertica/config/vertica_cluster.yaml

# Connect to the database
/opt/vertica/bin/vsql -U dbadmin -w 'CHANGE_ME_USE_STRONG_PASSWORD' -c "SELECT node_name, node_state FROM nodes;"

# Test S3 access from a node (IAM role path)
aws s3 ls s3://my-vertica-eon-bucket/
```

---

## 9. Important: data persistence

Eon Mode writes data to the **local depot** first and flushes to S3 asynchronously. To avoid losing data:

- The installer runs `sync_catalog()` automatically after database creation.
- Before you destroy the cluster, stop the database gracefully or run `SELECT sync_catalog();` in vsql.
- Do not terminate instances immediately after large data loads without syncing.

---

## Cleanup

When you are done testing, destroy the Pulumi-managed infrastructure.

```bash
# Stop the database first (recommended)
ssh -i ~/.ssh/pulumi.pem ec2-user@$(pulumi stack output instance_ips | head -1) \
    "sudo /opt/vertica/bin/adminTools -t stop_db -d eon_test_db -p 'CHANGE_ME_USE_STRONG_PASSWORD'"

# Destroy the infrastructure
pulumi destroy

# Optional: remove the stack
pulumi stack rm eon-test
```

The S3 bucket is **not** managed by Pulumi, so it will keep your data. Delete it separately if you no longer need it:

```bash
aws s3 rb s3://my-vertica-eon-bucket --force
```

---

## Troubleshooting

### `pulumi up` fails with an IAM permission error

The user running Pulumi needs permission to create IAM roles and instance profiles. For a least-privilege policy, see `docs/AWS_ENV_VARS.md`. For a first test, attach `PowerUserAccess` and `IAMFullAccess` to the user or CI role.

### EC2 instances cannot reach S3

From any node:

```bash
aws sts get-caller-identity
aws s3 ls s3://my-vertica-eon-bucket/
```

If the first command works but the second fails, the IAM policy does not cover the bucket path. Check `pulumi stack output iam_instance_profile_arn` and the policy in the AWS console.

### Database creation fails

1. Verify NMA is healthy on every node:

   ```bash
   sudo systemctl status vertica-nma
   sudo journalctl -u vertica-nma -n 50
   ```

2. Verify the communal path is empty before `Create`:

   ```bash
   aws s3 ls s3://my-vertica-eon-bucket/eon_test_db/
   ```

3. Retry with force cleanup:

   ```bash
   python3 scripts/install_vertica_eon.py \
       --config config/config_eon.yaml \
       --ssh-key ~/.ssh/pulumi.pem \
       --force-cleanup-on-failure
   ```

---

## Files you will touch

| File | Purpose |
|------|---------|
| `config/vertica-cluster-eon.yaml.example` | Copy this to `config/config_eon.yaml` as a starting point |
| `config/config_eon.yaml` | Your actual deployment config (gitignored) |
| `scripts/install_vertica_eon.py` | Post-Pulumi Vertica install and database creation |
| `scripts/generate_nma_certs.py` | Certificate generation if you prefer to run it manually |

---

## Next steps

- `docs/OPERATIONS.md` – start, stop, scale, and revive the cluster.
- `docs/CONFIGURATION.md` – all available YAML settings.
- Vertica docs: [Eon Mode](https://docs.vertica.com/26.1.x/en/eon/) and [vcluster commands](https://docs.vertica.com/25.1.x/en/admin/vcluster/vcluster-commands/)
