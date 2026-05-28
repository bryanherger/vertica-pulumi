"""
Complete AWS deployment module for Vertica clusters.

This module creates all necessary AWS infrastructure and provides
a higher-level interface for Vertica provisioning.
"""

import os
import pulumi
import pulumi_aws as aws
from typing import Dict, Any, List, Optional


class VerticaAWSDeployment:
    """
    Manages complete AWS infrastructure for a Vertica cluster.
    
    Creates VPC, subnets, security groups, EC2 instances, and EBS volumes.
    """
    
    def __init__(self, name: str, config: Dict[str, Any]):
        """
        Initialize deployment.
        
        Args:
            name: Deployment/cluster name
            config: AWS and Vertica configuration (with compute.aws nesting)
        """
        self.name = name
        self.config = config
        # Support both nested (compute.aws) and flat (aws) config structures
        self.aws_config = config.get('compute', {}).get('aws', {}) or config.get('aws', {})
        
        # Resources will be stored here
        self.vpc = None
        self.subnet = None
        self.security_group = None
        self.instances = []
        self.volumes = []
    
    def deploy(self) -> Dict[str, Any]:
        """
        Deploy complete AWS infrastructure.
        
        Returns:
            Dictionary of created resources
        """
        # Create network infrastructure
        self._create_network()
        
        # Create security group
        self._create_security_group()
        
        # Create EC2 instances
        self._create_instances()
        
        # Export outputs
        outputs = {
            'vpc_id': self.vpc.id if self.vpc else None,
            'subnet_id': self.subnet.id if self.subnet else None,
            'security_group_id': self.security_group.id if self.security_group else None,
            'instance_ids': [i.id for i in self.instances],
            'instance_ips': [i.public_ip for i in self.instances],
        }
        
        return outputs
    
    def _create_network(self):
        """Create VPC and subnet infrastructure"""
        
        # Create VPC
        self.vpc = aws.ec2.Vpc(
            f"{self.name}-vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags={"Name": f"{self.name}-vpc", **self._get_tags()}
        )
        
        # Create Internet Gateway
        igw = aws.ec2.InternetGateway(
            f"{self.name}-igw",
            vpc_id=self.vpc.id,
            tags={"Name": f"{self.name}-igw", **self._get_tags()}
        )
        
        # Create public subnet
        self.subnet = aws.ec2.Subnet(
            f"{self.name}-subnet",
            vpc_id=self.vpc.id,
            cidr_block="10.0.1.0/24",
            map_public_ip_on_launch=True,
            availability_zone=self._get_availability_zone(),
            tags={"Name": f"{self.name}-subnet", **self._get_tags()}
        )
        
        # Create route table
        route_table = aws.ec2.RouteTable(
            f"{self.name}-rt",
            vpc_id=self.vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    gateway_id=igw.id,
                )
            ],
            tags={"Name": f"{self.name}-rt", **self._get_tags()}
        )
        
        # Associate route table with subnet
        aws.ec2.RouteTableAssociation(
            f"{self.name}-rt-assoc",
            subnet_id=self.subnet.id,
            route_table_id=route_table.id,
        )
    
    def _create_security_group(self):
        """Create security group for Vertica cluster"""
        
        # Default Vertica ports
        default_rules = [
            {"protocol": "tcp", "port": 22, "cidr": "0.0.0.0/0", "description": "SSH"},
            {"protocol": "tcp", "port": 5433, "cidr": "10.0.0.0/16", "description": "Vertica client"},
            {"protocol": "tcp", "port": 5434, "cidr": "10.0.0.0/16", "description": "Vertica spread"},
            {"protocol": "tcp", "port": 5444, "cidr": "10.0.0.0/16", "description": "Vertica REST API"},
            {"protocol": "tcp", "port": 4803, "cidr": "10.0.0.0/16", "description": "Vertica spread"},
            {"protocol": "tcp", "port": 4804, "cidr": "10.0.0.0/16", "description": "Vertica spread"},
            {"protocol": "tcp", "port": 6543, "cidr": "10.0.0.0/16", "description": "Vertica agent"},
        ]
        
        # Custom rules from config
        custom_rules = self.aws_config.get('security_group_rules', [])
        
        # Merge rules, avoiding duplicates by (protocol, port, cidr)
        seen = {}
        all_rules = default_rules + custom_rules
        unique_rules = []
        for rule in all_rules:
            key = (rule.get('protocol', 'tcp'), rule['port'], rule.get('cidr', '0.0.0.0/0'))
            if key not in seen:
                seen[key] = True
                unique_rules.append(rule)
        
        ingress_rules = []
        for rule in unique_rules:
            ingress_rules.append(
                aws.ec2.SecurityGroupIngressArgs(
                    protocol=rule.get('protocol', 'tcp'),
                    from_port=rule['port'],
                    to_port=rule['port'],
                    cidr_blocks=[rule.get('cidr', '0.0.0.0/0')],
                    description=rule.get('description', ''),
                )
            )
        
        # Allow all internal VPC traffic
        ingress_rules.append(
            aws.ec2.SecurityGroupIngressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=["10.0.0.0/16"],
                description="Internal VPC traffic",
            )
        )
        
        self.security_group = aws.ec2.SecurityGroup(
            f"{self.name}-sg",
            vpc_id=self.vpc.id,
            description=f"Security group for {self.name} Vertica cluster",
            ingress=ingress_rules,
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                    description="Allow all outbound",
                )
            ],
            tags={"Name": f"{self.name}-sg", **self._get_tags()}
        )
    
    def _create_instances(self):
        """Create EC2 instances for Vertica cluster"""
        
        node_count = self.config.get('vertica', {}).get('nodes', {}).get('count', 3)
        
        # Get AMI - try explicit config first, then lookup, then fallback to known AMI
        ami_id = self.aws_config.get('ami', '')
        if not ami_id:
            # Try to lookup AMI dynamically
            try:
                ami = aws.ec2.get_ami(
                    most_recent=True,
                    owners=["amazon"],
                    filters=[
                        aws.ec2.GetAmiFilterArgs(
                            name="name",
                            values=["al2023-ami-*-x86_64"],
                        )
                    ]
                )
                ami_id = ami.id
                pulumi.log.info(f"Found AMI via lookup: {ami_id}")
            except Exception as e:
                pulumi.log.warn(f"AMI lookup failed (missing IAM permissions?): {e}")
                pulumi.log.warn("Falling back to hardcoded AMI - please verify this is correct for your region")
                
                # Fallback to known Amazon Linux 2023 AMI IDs by region
                # These are AL2023 x86_64 AMIs from early 2026 - update as needed
                region = self.aws_config.get('region', 'us-east-1')
                fallback_amis = {
                    'us-east-1': 'ami-0ebfd941bbafe70c6',      # AL2023 in us-east-1
                    'us-east-2': 'ami-067594f2eb693b14c',      # AL2023 in us-east-2
                    'us-west-1': 'ami-04f7a54071e2f19e9',      # AL2023 in us-west-1
                    'us-west-2': 'ami-0e4d282a301f7a012',      # AL2023 in us-west-2
                }
                ami_id = fallback_amis.get(region, 'ami-0ebfd941bbafe70c6')  # default to us-east-1
                pulumi.log.info(f"Using fallback AMI: {ami_id} for region {region}")
        
        instance_type = self.aws_config.get('instance_type', 'r6i.2xlarge')
        root_volume_size = self.aws_config.get('root_volume_size', 100)
        key_name = self.aws_config.get('key_name', '')
        
        # Load bootstrap script
        bootstrap_script = self._get_bootstrap_script()
        
        for i in range(node_count):
            instance_name = f"{self.name}-node-{i + 1}"
            
            instance = aws.ec2.Instance(
                instance_name,
                ami=ami_id,
                instance_type=instance_type,
                subnet_id=self.subnet.id,
                vpc_security_group_ids=[self.security_group.id],
                key_name=key_name if key_name else None,
                user_data=bootstrap_script,
                root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
                    volume_size=root_volume_size,
                    volume_type="gp3",
                    encrypted=True,
                ),
                tags={
                    "Name": instance_name,
                    "VerticaNode": str(i),
                    **self._get_tags(),
                },
            )
            
            self.instances.append(instance)
            
            # Create and attach additional volumes
            self._attach_data_volumes(instance, i)
    
    def _attach_data_volumes(self, instance: aws.ec2.Instance, index: int):
        """Attach additional EBS volumes for data storage"""
        
        additional_volumes = self.aws_config.get('additional_volumes', [])
        
        for vol_idx, vol_config in enumerate(additional_volumes):
            volume_name = f"{self.name}-node-{index + 1}-data-{vol_idx}"
            
            # Use pulumi_aws.ebs.Volume instead of aws.ec2.EbsVolume
            ebs_volume = aws.ebs.Volume(
                volume_name,
                availability_zone=self._get_availability_zone(),
                size=vol_config.get('size', 500),
                type=vol_config.get('type', 'gp3'),
                encrypted=True,
                tags={"Name": volume_name, **self._get_tags()},
            )
            
            aws.ec2.VolumeAttachment(
                f"{volume_name}-attach",
                device_name=f"/dev/sd{chr(ord('f') + vol_idx)}",
                volume_id=ebs_volume.id,
                instance_id=instance.id,
            )
            
            self.volumes.append(ebs_volume)
    
    def _get_bootstrap_script(self) -> str:
        """Generate cloud-init user data for bootstrap with Vertica prerequisites"""
        
        # Get Vertica prerequisites from config
        prerequisites = self.config.get('bootstrap', {}).get('prerequisites', [])
        packages = self.config.get('bootstrap', {}).get('packages', [])
        pre_install = self.config.get('bootstrap', {}).get('pre_install', [])
        post_install = self.config.get('bootstrap', {}).get('post_install', [])
        
        # Check if Vertica RPM is configured for auto-install
        vertica_config = self.config.get('vertica', {})
        rpm_config = vertica_config.get('rpm', {})
        license_config = vertica_config.get('license', {})
        has_rpm = bool(rpm_config.get('local_path', '') or rpm_config.get('download_url', ''))
        has_license = bool(license_config.get('local_path', ''))
        
        script_lines = [
            "#!/bin/bash",
            "set -e",
            "",
            "# Update system",
            "dnf update -y",
            "",
        ]
        
        # Install Vertica prerequisites FIRST (before packages)
        # These are required for Vertica RPM installation
        if prerequisites:
            script_lines.append("# Install Vertica prerequisites")
            script_lines.append(f"dnf install -y {' '.join(prerequisites)}")
            script_lines.append("")
        
        # Install additional packages
        if packages:
            script_lines.append("# Install additional packages")
            script_lines.append(f"dnf install -y {' '.join(packages)}")
            script_lines.append("")
        
        # Pre-install commands
        if pre_install:
            script_lines.append("# Run pre-installation tasks")
            for cmd in pre_install:
                script_lines.append(cmd)
            script_lines.append("")
        
        # System configuration
        script_lines.extend([
            "# Configure system for Vertica",
            "sysctl -w vm.max_map_count=262144",
            "echo 'vm.max_map_count=262144' >> /etc/sysctl.conf",
            "",
            "# Create vertica group and user (dbadmin needed for pre_install commands)",
            "groupadd -f verticadba",
            "id -u dbadmin &>/dev/null || useradd -m -s /bin/bash -g verticadba dbadmin",
            "usermod -aG verticadba dbadmin || true",
            "mkdir -p /home/dbadmin",
            "chown dbadmin:verticadba /home/dbadmin",
            "",
            "# Create data directories",
            "mkdir -p /data/vertica /data/catalog /data/depot",
            "chown -R dbadmin:verticadba /data",
            "chmod 755 /data",
            "",
            "# Configure limits",
            "cat >> /etc/security/limits.conf << 'EOF'",
            "dbadmin soft nofile 65536",
            "dbadmin hard nofile 65536",
            "dbadmin soft nproc 65536",
            "dbadmin hard nproc 65536",
            "EOF",
            "",
            "# Configure firewall",
            "systemctl enable firewalld || true",
            "systemctl start firewalld || true",
            "",
            "# Mount additional volumes",
            "for disk in /dev/nvme1n1 /dev/xvdb /dev/sdb; do",
            "    if [ -b \"$disk\" ]; then",
            "        if ! mountpoint -q /data; then",
            "            if ! file -s \"$disk\" | grep -q filesystem; then",
            "                mkfs.ext4 \"$disk\"",
            "            fi",
            "            echo \"$disk /data ext4 defaults,noatime 0 0\" >> /etc/fstab",
            "            mount /data",
            "        fi",
            "        break",
            "    fi",
            "done",
            "",
        ])
        
        # Post-install commands
        if post_install:
            script_lines.append("# Run post-installation tasks")
            for cmd in post_install:
                script_lines.append(cmd)
            script_lines.append("")
        
        # Vertica RPM auto-installation section
        if has_rpm:
            script_lines.extend([
                "# ============================================",
                "# Vertica RPM Auto-Installation",
                "# ============================================",
                "",
                "# Wait for RPM file to be uploaded (if using local_path)",
                "echo 'Checking for Vertica RPM...'",
            ])
            
            if rpm_config.get('local_path', ''):
                rpm_filename = os.path.basename(rpm_config['local_path'])
                script_lines.extend([
                    f"RPM_FILE=\"/tmp/{rpm_filename}\"",
                    "for i in {1..60}; do",
                    "    if [ -f \"$RPM_FILE\" ]; then",
                    "        echo \"RPM found, installing Vertica...\"",
                    f"        rpm -ivh \"$RPM_FILE\" || rpm -Uvh \"$RPM_FILE\"",
                    "        echo \"Vertica RPM installed successfully\"",
                    "        break",
                    "    fi",
                    "    echo \"Waiting for RPM file (attempt $i/60)...\"",
                    "    sleep 10",
                    "done",
                    "",
                ])
            elif rpm_config.get('download_url', ''):
                rpm_url = rpm_config['download_url']
                rpm_filename = rpm_url.split('/')[-1]
                script_lines.extend([
                    f"echo 'Downloading Vertica RPM from URL...'",
                    f"curl -L -o /tmp/{rpm_filename} '{rpm_url}'",
                    f"rpm -ivh /tmp/{rpm_filename} || rpm -Uvh /tmp/{rpm_filename}",
                    "echo 'Vertica RPM installed from URL'",
                    "",
                ])
            
            # Install license if provided
            if has_license:
                license_filename = os.path.basename(license_config['local_path'])
                script_lines.extend([
                    "# Install Vertica license (required for Vertica 26.1+)",
                    f"if [ -f \"/tmp/{license_filename}\" ]; then",
                    "    mkdir -p /opt/vertica/config/licensing",
                    f"    cp /tmp/{license_filename} /opt/vertica/config/licensing/license.xml",
                    "    chown -R dbadmin:verticadba /opt/vertica/config/licensing",
                    "    chmod 644 /opt/vertica/config/licensing/license.xml",
                    "    echo 'License installed'",
                    "fi",
                    "",
                ])
            
            # Post-installation configuration
            script_lines.extend([
                "# Post-installation configuration",
                "echo 'Configuring Vertica...'",
                "# Fix dbadmin primary group",
                "usermod -g verticadba dbadmin || true",
                "",
                "# Set timezone for dbadmin",
                "echo 'export TZ=UTC' >> /home/dbadmin/.bashrc",
                "chown dbadmin:verticadba /home/dbadmin/.bashrc",
                "",
                "# Set proper data directory permissions",
                "chown -R dbadmin:verticadba /data",
                "",
                "# Install missing library for vsql",
                "dnf install -y libxcrypt-compat || true",
                "",
                "# Run install_vertica with -Y to accept EULA and -L for license",
                "echo 'Running install_vertica to configure cluster...'",
            ])
            
            # Add install_vertica command with -Y and -L flags
            if has_license:
                license_path = "/opt/vertica/config/licensing/license.xml"
                script_lines.extend([
                    f"sudo /opt/vertica/sbin/install_vertica -Y -L {license_path} -T || true",
                ])
            else:
                script_lines.extend([
                    "sudo /opt/vertica/sbin/install_vertica -Y -T || true",
                ])
            
            script_lines.extend([
                "",
                "echo 'Vertica configuration complete'",
                "",
            ])
        
        script_lines.extend([
            "# Signal completion",
            "echo 'Bootstrap complete at $(date)' \u003e /var/log/vertica-bootstrap.log",
        ])
        
        return "\n".join(script_lines)
    
    def _get_tags(self) -> Dict[str, str]:
        """Get common tags from configuration"""
        return self.aws_config.get('tags', {})
    
    def _get_availability_zone(self) -> str:
        """Get availability zone for the region"""
        # First check if AZ is explicitly configured
        configured_az = self.aws_config.get('availability_zone', '')
        if configured_az:
            return configured_az
        
        # Try to fetch from AWS, but handle permission errors gracefully
        try:
            azs = aws.get_availability_zones(state="available")
            if azs.names:
                return azs.names[0]
        except Exception:
            # If we can't fetch AZs (e.g., missing IAM permissions),
            # construct from configured region
            pass
        
        # Fallback: construct AZ from region (e.g., us-east-2 -> us-east-2a)
        region = self.aws_config.get('region', 'us-east-1')
        return f"{region}a"
    
    def get_instance_outputs(self) -> List[Dict[str, Any]]:
        """Get instance information for output"""
        outputs = []
        for instance in self.instances:
            outputs.append({
                'id': instance.id,
                'public_ip': instance.public_ip,
                'private_ip': instance.private_ip,
                'public_dns': instance.public_dns,
            })
        return outputs
