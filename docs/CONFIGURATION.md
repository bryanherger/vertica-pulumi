# Configuration Guide

## Overview

Configuration is loaded from YAML files with support for multiple naming conventions and environment-based overrides.

## Configuration File Locations

The system searches for configuration files in this priority order:

### 1. Environment Variable (Highest Priority)

```bash
export VERTICA_CONFIG="/path/to/your/config.yaml"
pulumi up
```

### 2. Auto-Discovery (Default Behavior)

If `VERTICA_CONFIG` is not set, the system auto-discovers config files:

#### Production Config Files (checked first)
- `config/config.yaml`
- `config/config.yml`

#### Example/Template Files (fallback)
- `config/*.yaml.example`
- `config/*.yml.example`

#### Legacy Fallback
- `config/sample-config.yaml`
- `config/sample-config.yml`

### 3. Built-in Defaults (Last Resort)

If no config file is found, sensible defaults are used.

## File Naming Conventions

### Production Config Files

These are your actual deployment configurations. **They should not be committed to git** (they're gitignored).

```bash
config/config.yaml          # Primary production config
config/config.yml           # Alternative extension
config/prod.yaml            # Custom name (use VERTICA_CONFIG)
```

### Example/Template Files

These are templates showing all available options. **They should be committed to git** as examples.

```bash
config/vertica-cluster.yaml.example     # Full example with all options
config/minimal.yaml.example             # Minimal example
```

**Usage pattern:**
```bash
# Copy example to production config
cp config/vertica-cluster.yaml.example config/config.yaml

# Edit with your actual values
vim config/config.yaml

# Deploy
pulumi up
```

## Environment Variable Overrides

You can override any configuration value using environment variables:

```bash
# Override AWS region
export VERTICA_AWS_REGION="us-west-2"

# Override node count
export VERTICA_NODE_COUNT="5"

# Override database name
export VERTICA_DB_NAME="production_analytics"
```

## Pulumi Config Overrides

Pulumi stack configuration has higher priority than YAML files:

```bash
# Set in current stack
pulumi config set compute.provider aws
pulumi config set cluster_name production-cluster
pulumi config set --secret vertica:admin_password "secure-password"
```

## Configuration Priority (Highest to Lowest)

1. Pulumi secrets (`pulumi config set --secret`)
2. Pulumi config (`pulumi config set`)
3. Environment variables (`VERTICA_*`)
4. YAML config file
5. Built-in defaults

## Example Configurations

### Minimal AWS Deployment

```yaml
compute:
  provider: aws
  aws:
    region: us-east-1
    key_name: my-key-pair

vertica:
  cluster_name: my-cluster
  database:
    name: analytics
  nodes:
    count: 3
```

### Bare Metal Import

```yaml
compute:
  provider: baremetal
  baremetal:
    hosts:
      - hostname: db-node-1
        ip: 10.0.1.101
        ssh_user: admin
        ssh_key_path: ~/.ssh/id_rsa
      - hostname: db-node-2
        ip: 10.0.1.102
        ssh_user: admin
        ssh_key_path: ~/.ssh/id_rsa

vertica:
  cluster_name: baremetal-cluster
  database:
    name: analytics
```

### Production AWS with Custom Settings

```yaml
compute:
  provider: aws
  aws:
    region: us-east-1
    instance_type: r6i.4xlarge      # More CPU/memory
    key_name: production-key
    root_volume_size: 200
    additional_volumes:
      - size: 1000                  # 1TB data volume
        type: io2                   # High performance
        iops: 16000
    security_group_rules:
      - protocol: tcp
        port: 5433
        cidr: 10.0.0.0/8            # Restrict to internal
    tags:
      Environment: production
      CostCenter: analytics
      Backup: required

vertica:
  version: "24.1.0-1"
  cluster_name: prod-vertica
  database:
    name: analytics_prod
    admin_username: dbadmin
  nodes:
    count: 5
    data_path: /data/vertica
    catalog_path: /data/catalog
  resources:
    max_memory_percent: 90
  config_params:
    MaxClientSessions: 500
    EnableSSL: 1
```

## Validation

The configuration is validated at runtime. Common issues:

- **Missing `key_name`**: Required for AWS SSH access
- **Invalid `instance_type`**: Must be a valid EC2 instance type
- **Mismatched node counts**: Compute instances must match Vertica node count
- **Missing `admin_password`**: Required for database creation

## See Also

- [AWS Environment Variables](AWS_ENV_VARS.md) - AWS-specific setup
- [Deployment Guide](DEPLOYMENT.md) - Full deployment instructions
