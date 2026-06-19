# AWS Environment Variables and Credentials

Pulumi (via the AWS Terraform provider) automatically picks up standard AWS SDK environment variables.

## Required for AWS CLI / Pulumi

```bash
export AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
export AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
export AWS_DEFAULT_REGION="us-east-1"
```

## Optional

```bash
# Temporary credentials / IAM role session
export AWS_SESSION_TOKEN="your-session-token"

# Use a named profile from ~/.aws/credentials
export AWS_PROFILE="my-profile"

# When running Pulumi itself on an EC2 instance with an instance profile
# no env vars are needed; the instance profile is used automatically
```

## SSH Key Pair (for EC2 instances)

The `key_name` in the YAML config is the **name of an existing AWS EC2 key pair** created in the target region. This is **not** your local SSH private key path. The private key path is passed to `scripts/install_vertica_eon.py` via `--ssh-key`.

```yaml
compute:
  aws:
    key_name: "vertica-automation"  # Must exist in AWS EC2
```

Create and use a key pair:

```bash
aws ec2 create-key-pair \
    --key-name vertica-automation \
    --query 'KeyMaterial' \
    --output text > ~/.ssh/vertica-automation.pem
chmod 600 ~/.ssh/vertica-automation.pem
```

SSH after deployment:

```bash
ssh -i ~/.ssh/vertica-automation.pem ec2-user@$(pulumi stack output instance_ips | head -1)
```

## Vertica S3 access on EC2 instances

The recommended approach is **IAM instance profile**:

```yaml
compute:
  aws:
    s3_auth_mode: iam_role
```

Pulumi creates an IAM role and instance profile with a least-privilege policy that allows read/write access to the configured communal bucket and path. The profile is attached to every EC2 instance, so no AWS credentials need to be stored on the nodes or in config files.

If IAM instance profiles are not available, set:

```yaml
compute:
  aws:
    s3_auth_mode: access_keys
vertica:
  eon:
    aws_access_key_id: "AKIA..."
    aws_secret_access_key: "..."
```

> Store secrets via Pulumi secrets (`pulumi config set --secret ...`) or environment variables, not plain YAML in production.

## Pulumi IAM permissions

For a first deployment, the Pulumi runner needs permissions to create VPC, EC2, EBS, IAM, and S3 resources. The simplest policy for testing is:

- `PowerUserAccess`
- `IAMFullAccess`

For a least-privilege policy, restrict actions to the required EC2, VPC, EBS, IAM, and S3 operations and scope resources to the project prefix.

## Where to set these

1. **Local development**: export in the shell or use `~/.aws/credentials`.
2. **CI/CD**: set in pipeline secrets (GitHub Actions, GitLab CI, etc.).
3. **Pulumi ESC**: use `pulumi env` for centralized secrets management.

## Config file location

Pulumi looks for the config file in this order:

1. `pulumi config get vertica:config_file`
2. `VERTICA_CONFIG_FILE` environment variable
3. Default: `config/config.yaml`

```bash
pulumi stack init eon-test
pulumi config set vertica:config_file config/config_eon.yaml
```

## Full example

```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="us-east-1"

pulumi stack init eon-test
pulumi config set vertica:config_file config/config_eon.yaml
pulumi up
```
