# Configuration Reference

This document describes every configuration key used by the Vertica Pulumi project. Configuration is YAML-driven. Pulumi reads the file pointed to by the stack key `vertica:config_file` (or the `VERTICA_CONFIG_FILE` environment variable). The default is `config/config.yaml`.

## Top-level sections

```yaml
compute:     # AWS infrastructure settings
vertica:     # Vertica software, database, and Eon Mode settings
bootstrap:   # OS bootstrap commands run on every EC2 instance
```

---

## `compute`

### `compute.provider`

- **Type:** string
- **Default:** `aws`
- Cloud provider to use. Only `aws` is implemented today.

### `compute.aws`

All AWS-specific infrastructure settings live here.

#### `compute.aws.region`

- **Type:** string
- **Example:** `us-east-1`
- AWS region for the VPC, EC2 instances, and S3 bucket.

#### `compute.aws.key_name`

- **Type:** string
- **Example:** `vertica-automation`
- **Required.** Name of an existing AWS EC2 key pair. This is the **name**, not the file path. The private key path is passed to `scripts/install_vertica_eon.py` via `--ssh-key`.

#### `compute.aws.instance_type`

- **Type:** string
- **Default:** `t3.medium`
- **Recommended for Eon:** `r6i.2xlarge`
- EC2 instance type. Eon Mode with a 3-node cluster and `/data` depot requires at least `r6i.2xlarge`.

#### `compute.aws.root_volume_size`

- **Type:** integer
- **Default:** `20`
- Size in GB of the root EBS volume.

#### `compute.aws.connect_via_public_ip`

- **Type:** boolean
- **Default:** `false`
- **Recommended when running Pulumi from outside the VPC:** `true`
- When `true`, Pulumi exports public IPs and the installer uses public IPs for SSH. Vertica internal traffic still uses private IPs.

#### `compute.aws.run_db_create_inline`

- **Type:** boolean
- **Default:** `false`
- When `true`, Pulumi runs a simple inline `vcluster create_db` command after the instances are up. This is **deprecated** and not as reliable as running `scripts/install_vertica_eon.py`. Keep `false` for the supported end-to-end flow.

#### `compute.aws.s3_auth_mode`

- **Type:** string
- **Default:** `iam_role`
- Allowed values: `iam_role`, `access_keys`
- How Vertica nodes authenticate to S3 communal storage.
  - `iam_role`: Pulumi creates an IAM instance profile with a least-privilege policy for the communal bucket and attaches it to every instance. No keys are stored on nodes or in config files.
  - `access_keys`: Pulumi does not create an IAM profile. You must provide credentials with `vertica.eon.aws_access_key_id` and `vertica.eon.aws_secret_access_key`, or configure them on the nodes manually. The installer will pass `--get-aws-credentials-from-env-vars` to vcluster.

#### `compute.aws.iam_instance_profile`

- **Type:** string
- **Default:** `""`
- When set and non-empty, Pulumi attaches the specified existing IAM instance profile name to the instances instead of creating a new one. Only used when `s3_auth_mode` is `iam_role`.

#### `compute.aws.additional_volumes`

- **Type:** list of objects
- **Default:** `[]`
- Extra EBS volumes to attach to each instance. For Eon Mode this should include the depot/catalog/data volume mounted at `/data`.

Example:

```yaml
additional_volumes:
  - size: 500
    type: gp3
    mount_point: /data
```

#### `compute.aws.security_group_rules`

- **Type:** list of objects
- **Default:** rules for SSH (22), Vertica (5433, 5444), and NMA (5554, 8443)
- Ingress rules added to the cluster security group. Each rule can specify a single `port` or a range with `from_port` / `to_port` plus `protocol` and `cidr`.

Example:

```yaml
security_group_rules:
  - protocol: tcp
    port: 22
    cidr: 10.0.0.0/8
  - protocol: tcp
    from_port: 5433
    to_port: 5444
    cidr: 10.0.0.0/8
```

#### `compute.aws.tags`

- **Type:** map of strings
- AWS tags applied to EC2 instances and related resources.

---

## `vertica`

### `vertica.version`

- **Type:** string
- **Example:** `"26.2.0-0"`
- Vertica version string. Should match the RPM being installed. Currently informational.

### `vertica.cluster_name`

- **Type:** string
- **Example:** `vertica-eon-cluster`
- Name of the Vertica cluster. Used for resource naming and the default database path.

### `vertica.mode`

- **Type:** string
- **Allowed values:** `eon`, `enterprise`
- Deployment mode. This project is primarily maintained for **Eon Mode**; Enterprise Mode support is legacy.

### `vertica.license`

#### `vertica.license.local_path`

- **Type:** string
- **Example:** `./vertica_license.xml`
- Path to a valid Vertica license XML file. Relative paths are resolved from the project directory. The file is uploaded to every node by `install_vertica_eon.py`.

### `vertica.rpm`

#### `vertica.rpm.local_path`

- **Type:** string
- **Example:** `./vertica.rpm`
- Path to the Vertica RPM file. Relative paths are resolved from the project directory. The file is uploaded to every node by `install_vertica_eon.py`.

### `vertica.database`

#### `vertica.database.name`

- **Type:** string
- **Example:** `pulumidb`
- Name of the Vertica database to create or revive.

#### `vertica.database.admin_username`

- **Type:** string
- **Default:** `dbadmin`
- Administrative database user name.

#### `vertica.database.admin_password`

- **Type:** string
- **Example:** `"CHANGE_ME_USE_STRONG_PASSWORD"`
- Password for the administrative database user. For production, pass this through Pulumi config secrets or environment variables instead of plain YAML.

### `vertica.eon`

Eon Mode settings.

#### `vertica.eon.communal_storage_location`

- **Type:** string
- **Example:** `s3://my-vertica-bucket/pulumidb`
- S3 location used as communal storage. The bucket must exist; the sub-path should be empty before `Create`.

#### `vertica.eon.shard_count`

- **Type:** integer
- **Default:** `3`
- Number of shards for the Eon database. Usually equal to the node count.

#### `vertica.eon.depot_path`

- **Type:** string
- **Default:** `/data/depot`
- Local path for the depot cache.

#### `vertica.eon.depot_size`

- **Type:** string
- **Default:** `80%`
- Depot size as a percentage of the `/data` volume or an absolute value such as `200GB`.

#### `vertica.eon.aws_region`

- **Type:** string
- **Example:** `us-east-1`
- AWS region for communal storage. Must match `compute.aws.region`.

#### `vertica.eon.aws_enable_https`

- **Type:** boolean
- **Default:** `true`
- Passed to Vertica as the `AWSEnableHttps` config parameter.

#### `vertica.eon.enable_s3_encryption`

- **Type:** boolean
- **Default:** `true`
- Enables S3 server-side encryption settings when generating policies.

#### `vertica.eon.dbinit`

- **Type:** string
- **Default:** `Create`
- Allowed values: `Create`, `Revive`
- Database initialization action. `Create` builds a new database. `Revive` starts an existing database from communal storage after infrastructure is recreated.

#### `vertica.eon.aws_access_key_id` and `vertica.eon.aws_secret_access_key`

- **Type:** string
- Only used when `compute.aws.s3_auth_mode: access_keys`. Required in that case.

### `vertica.nodes`

#### `vertica.nodes.count`

- **Type:** integer
- **Default:** `3`
- Number of EC2 instances in the cluster.

#### `vertica.nodes.data_path`

- **Type:** string
- **Default:** `/data/vertica`
- Local path for Vertica data files.

#### `vertica.nodes.catalog_path`

- **Type:** string
- **Default:** `/data/catalog`
- Local path for Vertica catalog files.

### `vertica.network`

#### `vertica.network.port`

- **Type:** integer
- **Default:** `5433`
- Vertica client port.

#### `vertica.network.rest_api_port`

- **Type:** integer
- **Default:** `5444`
- Vertica REST API / vclusterops port.

### `vertica.security`

#### `vertica.security.generate_nma_certs`

- **Type:** boolean
- **Default:** `true`
- When `true`, `install_vertica_eon.py` generates the full TLS bootstrap material (CA, server cert, dbadmin cert, and `httpstls.json`) locally on the runner and deploys it to `/opt/vertica/config/https_certs/` on every node. This is required for the no-node-to-node-SSH workflow.

#### `vertica.security.cert_validity_days`

- **Type:** integer
- **Default:** `365`
- Validity period for generated certificates.

#### `vertica.security.cert_country`, `cert_state`, `cert_locality`, `cert_org`, `cert_ou`, `cert_cn`

- **Type:** string
- Fields used in the generated certificate subject and subjectAltName.

---

## `bootstrap`

Commands run on every EC2 instance during initial provisioning. The Pulumi program uploads them as `user_data` and executes them as root.

### `bootstrap.prerequisites`

- **Type:** list of strings
- OS packages required by Vertica. The example includes `dialog`, `pcre`, `pcre2`, `sysstat`, and `libxcrypt-compat`.

### `bootstrap.packages`

- **Type:** list of strings
- Extra packages to install, such as `vim`, `htop`, `tmux`, `wget`, `net-tools`, `psmisc`, `lsof`, and `aws-cli`.

### `bootstrap.pre_install`

- **Type:** list of strings
- Shell commands run before the Vertica RPM is installed. Use this for sysctl tuning, swap, limits, and creating data directories.

### `bootstrap.post_install`

- **Type:** list of strings
- Shell commands run after the Vertica RPM is installed.

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `VERTICA_CONFIG_FILE` | YAML file path used when `vertica:config_file` is not set in Pulumi stack config |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | Pulumi uses these to authenticate to AWS |

---

## Example: minimal Eon config

```yaml
compute:
  provider: aws
  aws:
    region: us-east-1
    key_name: vertica-automation
    instance_type: r6i.2xlarge
    connect_via_public_ip: true
    run_db_create_inline: false
    s3_auth_mode: iam_role
    additional_volumes:
      - size: 500
        type: gp3
        mount_point: /data

vertica:
  version: "26.2.0-0"
  cluster_name: vertica-eon-cluster
  mode: eon
  license:
    local_path: ./vertica_license.xml
  rpm:
    local_path: ./vertica.rpm
  database:
    name: pulumidb
    admin_username: dbadmin
    admin_password: "CHANGE_ME_USE_STRONG_PASSWORD"
  eon:
    communal_storage_location: s3://my-vertica-bucket/pulumidb
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
  security:
    generate_nma_certs: true
    cert_validity_days: 365
    cert_country: "US"
    cert_org: "MyOrganization"
```

For a full annotated example, see `config/vertica-cluster-eon.yaml.example`.
