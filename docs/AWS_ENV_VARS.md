# AWS Environment Variables

Pulumi (via the AWS Terraform provider) automatically picks up standard AWS SDK environment variables. You need:

## Required

```bash
export AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
export AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
export AWS_DEFAULT_REGION="us-east-1"
```

## Optional

```bash
# If using temporary credentials / IAM roles
export AWS_SESSION_TOKEN="your-session-token"

# Alternative: profile from ~/.aws/credentials
export AWS_PROFILE="my-profile"

# Alternative: load from EC2 instance metadata (if running on EC2)
# No env vars needed - uses instance role automatically
```

## SSH Key Pair (for EC2 instances)

The `key_name` in the YAML config is the **name of an existing AWS EC2 key pair** (created in AWS Console or via `aws ec2 create-key-pair`). This is **not** your local SSH private key path.

```yaml
compute:
  aws:
    key_name: "my-key-pair-name"  # Must exist in AWS EC2
```

To use the key for SSH after deployment:

```bash
# Download the private key when you create the key pair
aws ec2 create-key-pair --key-name my-key-pair-name --query 'KeyMaterial' --output text > ~/.ssh/my-key.pem
chmod 600 ~/.ssh/my-key.pem

# SSH to instances after deployment
ssh -i ~/.ssh/my-key.pem ec2-user@<instance-ip>
```

## Where to Set These

1. **Local development**: Export in shell or add to `.env` file
2. **CI/CD**: Set in pipeline secrets (GitHub Actions, GitLab CI, etc.)
3. **Pulumi ESC**: Use `pulumi env` for centralized secrets management

## Full Example

```bash
# Set AWS credentials
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="us-east-1"

# Set config file location
export VERTICA_CONFIG="config/my-cluster.yaml"

# Run Pulumi
pulumi up
```
