# Deployment Guide

## Prerequisites

1. **Pulumi** - Infrastructure as Code tool
2. **AWS CLI** - For AWS deployments
3. **SSH Key** - For accessing instances
4. **Vertica License** - Required for production use

## Installation

1. Install Pulumi:
   ```bash
   curl -fsSL https://get.pulumi.com | sh
   ```

2. Set up Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Configure AWS credentials:
   ```bash
   aws configure
   ```

## Configuration

1. Copy the sample configuration that matches your deployment mode:
   ```bash
   # Eon Mode on AWS (recommended for cloud deployments)
   cp config/vertica-cluster-eon.yaml.example config/config_eon.yaml

   # Enterprise Mode on AWS
   cp config/vertica-cluster.yaml.example config/config.yaml
   ```

2. Edit the copied file with your settings:
   - AWS region and instance types
   - Vertica version and database name
   - SSH key pair name
   - S3 bucket (for Eon Mode)
   - Network settings

3. Set configuration file:
   ```bash
   export VERTICA_CONFIG=config/my-cluster.yaml
   ```

## Deployment

### AWS Deployment

1. Initialize Pulumi stack:
   ```bash
   pulumi stack init dev
   ```

2. Set required secrets:
   ```bash
   pulumi config set --secret vertica:admin_password "your-password"
   ```

3. Preview changes:
   ```bash
   pulumi preview
   ```

4. Deploy infrastructure:
   ```bash
   pulumi up
   ```

5. Access instances:
   ```bash
   # Get instance IPs
   pulumi stack output instance_ips
   
   # SSH to primary node
   ssh -i ~/.ssh/your-key ec2-user@<primary-ip>
   ```

### Bare Metal Import

1. Configure hosts in `config/my-cluster.yaml`:
   ```yaml
   compute:
     provider: baremetal
     baremetal:
       hosts:
         - hostname: server-1
           ip: 192.168.1.101
           ssh_user: admin
           ssh_key_path: ~/.ssh/id_rsa
   ```

2. Import into Pulumi:
   ```bash
   pulumi up
   ```

## Post-Deployment

### Install Vertica

After infrastructure is ready, run the Vertica installer:

```bash
# Set up SSH access
export SSH_KEY=~/.ssh/your-key

# Run installation playbook (to be implemented)
python scripts/install_vertica.py
```

### Configure Database

```bash
# Create database using vcluster
vcluster create_db \
  --db-name analytics \
  --hosts <node1-ip>,<node2-ip>,<node3-ip> \
  --data-path /data/vertica \
  --catalog-path /data/catalog \
  --username dbadmin
```

## Scaling

### Scale Up (AWS)

1. Update node count in configuration
2. Run Pulumi update:
   ```bash
   pulumi up
   ```
3. Add nodes to Vertica cluster:
   ```bash
   python scripts/scale_cluster.py --add-nodes <new-node-ips>
   ```

### Scale Down

1. Remove nodes from Vertica cluster:
   ```bash
   python scripts/scale_cluster.py --remove-nodes <node-ips>
   ```
2. Update configuration
3. Run Pulumi update:
   ```bash
   pulumi up
   ```

## Monitoring

Check cluster health:
```bash
python scripts/check_health.py
```

## Cleanup

Destroy all resources:
```bash
pulumi destroy
```

## Troubleshooting

### Common Issues

1. **SSH Access Denied**
   - Verify security group allows port 22
   - Check SSH key is correct
   - Ensure user exists on instance

2. **Vertica Installation Fails**
   - Check system requirements (memory, disk)
   - Verify OS compatibility
   - Review installation logs: `/opt/vertica/log/`

3. **Database Creation Fails**
   - Ensure all nodes are accessible
   - Check network connectivity between nodes
   - Verify ports are open (5433, 5434, 4803, 4804)

## Support

For issues and feature requests, please file an issue in the project repository.
