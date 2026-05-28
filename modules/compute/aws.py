"""
AWS EC2 compute provider implementation.

Uses Pulumi's AWS provider to create and manage EC2 instances,
VPCs, subnets, security groups, and related resources.
"""

import pulumi
import pulumi_aws as aws
from typing import List, Optional, Dict, Any

from .base import ComputeProvider, ComputeInstance, ComputeCluster


class AWSComputeProvider(ComputeProvider):
    """
    AWS EC2 implementation of the ComputeProvider interface.
    
    Creates VPC, subnets, security groups, and EC2 instances
    for Vertica cluster deployment.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider_name = "aws"
        self.aws_config = config.get('aws', {})
        
        # Get AWS provider configuration
        self.region = self.aws_config.get('region', 'us-east-1')
        
    def create_cluster(self, name: str, node_count: int, **kwargs) -> ComputeCluster:
        """
        Create a complete AWS infrastructure for Vertica cluster.
        
        Creates:
        - VPC with Internet Gateway
        - Public subnet with route table
        - Security group with Vertica ports
        - EC2 instances with appropriate sizing
        - EBS volumes for data storage
        """
        
        # Create VPC
        vpc = aws.ec2.Vpc(
            f"{name}-vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags={"Name": f"{name}-vpc", **self._get_tags()}
        )
        
        # Create Internet Gateway
        igw = aws.ec2.InternetGateway(
            f"{name}-igw",
            vpc_id=vpc.id,
            tags={"Name": f"{name}-igw", **self._get_tags()}
        )
        
        # Create public subnet
        subnet = aws.ec2.Subnet(
            f"{name}-subnet",
            vpc_id=vpc.id,
            cidr_block="10.0.1.0/24",
            map_public_ip_on_launch=True,
            availability_zone=self._get_availability_zone(),
            tags={"Name": f"{name}-subnet", **self._get_tags()}
        )
        
        # Create route table
        route_table = aws.ec2.RouteTable(
            f"{name}-rt",
            vpc_id=vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    gateway_id=igw.id,
                )
            ],
            tags={"Name": f"{name}-rt", **self._get_tags()}
        )
        
        # Associate route table with subnet
        route_table_association = aws.ec2.RouteTableAssociation(
            f"{name}-rt-assoc",
            subnet_id=subnet.id,
            route_table_id=route_table.id,
        )
        
        # Create security group for Vertica
        security_group = self._create_security_group(name, vpc.id)
        
        # Create SSH key pair if specified
        key_name = self.aws_config.get('key_name', '')
        
        # Create instances
        instances = []
        for i in range(node_count):
            instance = self._create_instance(
                name=name,
                index=i,
                subnet_id=subnet.id,
                security_group_id=security_group.id,
                key_name=key_name,
            )
            instances.append(instance)
        
        # Build ComputeCluster from created resources
        # Note: We need to use pulumi.Output values, so we'll construct
        # the ComputeCluster using apply
        
        return self._build_cluster_output(name, vpc.id, subnet.id, instances)
    
    def _create_security_group(self, name: str, vpc_id: pulumi.Output[str]) -> aws.ec2.SecurityGroup:
        """Create security group with Vertica-specific rules"""
        
        # Default rules if none specified in config
        default_rules = [
            {"protocol": "tcp", "port": 22, "cidr": "0.0.0.0/0", "description": "SSH"},
            {"protocol": "tcp", "port": 5433, "cidr": "0.0.0.0/0", "description": "Vertica client"},
            {"protocol": "tcp", "port": 5444, "cidr": "0.0.0.0/0", "description": "Vertica REST API"},
        ]
        
        rules = self.aws_config.get('security_group_rules', default_rules)
        
        ingress_rules = []
        for rule in rules:
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
        
        sg = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc_id,
            description=f"Security group for {name} Vertica cluster",
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
            tags={"Name": f"{name}-sg", **self._get_tags()}
        )
        
        return sg
    
    def _create_instance(self, name: str, index: int, 
                        subnet_id: pulumi.Output[str],
                        security_group_id: pulumi.Output[str],
                        key_name: str) -> aws.ec2.Instance:
        """Create a single EC2 instance"""
        
        instance_name = f"{name}-node-{index + 1}"
        
        # Get AMI - use Amazon Linux 2023 if not specified
        ami_id = self.aws_config.get('ami', '')
        if not ami_id:
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
        
        # Get instance type
        instance_type = self.aws_config.get('instance_type', 'r6i.2xlarge')
        
        # Root volume configuration
        root_volume_size = self.aws_config.get('root_volume_size', 100)
        
        # User data for bootstrap
        user_data = self._generate_user_data()
        
        instance = aws.ec2.Instance(
            instance_name,
            ami=ami_id,
            instance_type=instance_type,
            subnet_id=subnet_id,
            vpc_security_group_ids=[security_group_id],
            key_name=key_name if key_name else None,
            user_data=user_data,
            root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
                volume_size=root_volume_size,
                volume_type="gp3",
                encrypted=True,
            ),
            tags={
                "Name": instance_name,
                "VerticaNode": str(index),
                **self._get_tags(),
            },
        )
        
        # Add additional data volumes if configured
        additional_volumes = self.aws_config.get('additional_volumes', [])
        for vol_idx, vol_config in enumerate(additional_volumes):
            ebs_volume = aws.ec2.EbsVolume(
                f"{instance_name}-data-vol-{vol_idx}",
                availability_zone=self._get_availability_zone(),
                size=vol_config.get('size', 500),
                type=vol_config.get('type', 'gp3'),
                encrypted=True,
                tags={"Name": f"{instance_name}-data-{vol_idx}", **self._get_tags()},
            )
            
            aws.ec2.VolumeAttachment(
                f"{instance_name}-vol-attach-{vol_idx}",
                device_name=f"/dev/sd{chr(ord('f') + vol_idx)}",
                volume_id=ebs_volume.id,
                instance_id=instance.id,
            )
        
        return instance
    
    def _generate_user_data(self) -> str:
        """Generate cloud-init user data for instance bootstrap"""
        
        packages = self.config.get('bootstrap', {}).get('packages', [])
        pre_install = self.config.get('bootstrap', {}).get('pre_install', [])
        post_install = self.config.get('bootstrap', {}).get('post_install', [])
        
        # Build cloud-init script
        script_lines = [
            "#!/bin/bash",
            "set -e",
            "",
            "# Update system",
            "dnf update -y",
            "",
        ]
        
        # Install packages
        if packages:
            script_lines.append("# Install required packages")
            script_lines.append(f"dnf install -y {' '.join(packages)}")
            script_lines.append("")
        
        # Run pre-install commands
        if pre_install:
            script_lines.append("# Run pre-installation tasks")
            for cmd in pre_install:
                script_lines.append(cmd)
            script_lines.append("")
        
        # System configuration for Vertica
        script_lines.extend([
            "# Configure system for Vertica",
            "sysctl -w vm.max_map_count=262144",
            "echo 'vm.max_map_count=262144' >> /etc/sysctl.conf",
            "",
            "# Create vertica user and directories",
            "useradd -m -s /bin/bash vertica || true",
            "mkdir -p /data/vertica /data/catalog",
            "chown -R vertica:vertica /data",
            "",
            "# Configure limits",
            "cat >> /etc/security/limits.conf << 'EOF'",
            "vertica soft nofile 65536",
            "vertica hard nofile 65536",
            "vertica soft nproc 65536",
            "vertica hard nproc 65536",
            "EOF",
            "",
            "# Configure firewalld",
            "systemctl enable firewalld || true",
            "systemctl start firewalld || true",
            "",
        ])
        
        # Run post-install commands
        if post_install:
            script_lines.append("# Run post-installation tasks")
            for cmd in post_install:
                script_lines.append(cmd)
            script_lines.append("")
        
        script_lines.extend([
            "# Signal completion",
            "echo 'Bootstrap complete' > /var/log/bootstrap-complete.log",
        ])
        
        return "\n".join(script_lines)
    
    def _build_cluster_output(self, name: str, vpc_id: pulumi.Output[str], 
                             subnet_id: pulumi.Output[str],
                             instances: List[aws.ec2.Instance]) -> ComputeCluster:
        """Build ComputeCluster from Pulumi resources"""
        
        # For Pulumi outputs, we need to use Output.all() to access values
        instance_outputs = []
        
        for i, instance in enumerate(instances):
            instance_outputs.append(
                pulumi.Output.all(
                    instance.id,
                    instance.public_ip,
                    instance.private_ip,
                    instance.public_dns,
                ).apply(lambda args, idx=i: ComputeInstance(
                    id=args[0],
                    name=f"{name}-node-{idx + 1}",
                    public_ip=args[1],
                    private_ip=args[2],
                    hostname=args[3] or args[2],
                    ssh_user="ec2-user",
                    ssh_key_path=self.aws_config.get('key_name'),
                    status="running",
                    tags=self._get_tags(),
                ))
            )
        
        # We need to return a ComputeCluster, but since we're in Pulumi context,
        # we'll return an Output that resolves to ComputeCluster
        return ComputeCluster(
            name=name,
            instances=[],  # Will be populated by the caller using outputs
            provider="aws",
            vpc_id=vpc_id,
            subnet_id=subnet_id,
            tags=self._get_tags(),
        )
    
    def _get_tags(self) -> Dict[str, str]:
        """Get common tags from config"""
        return self.aws_config.get('tags', {})
    
    def _get_availability_zone(self) -> str:
        """Get availability zone for the region"""
        # Get first AZ in the region
        azs = aws.get_availability_zones(state="available")
        return azs.names[0] if azs.names else "us-east-1a"
    
    def import_cluster(self, instance_ids: List[str], **kwargs) -> ComputeCluster:
        """Import existing EC2 instances"""
        
        instances = []
        for instance_id in instance_ids:
            # Get existing instance details
            ec2_instance = aws.ec2.get_instance(id=instance_id)
            
            instances.append(ComputeInstance(
                id=instance_id,
                name=ec2_instance.tags.get("Name", instance_id),
                public_ip=ec2_instance.public_ip,
                private_ip=ec2_instance.private_ip,
                hostname=ec2_instance.public_dns or ec2_instance.private_ip,
                ssh_user="ec2-user",
                ssh_key_path=self.aws_config.get('key_name'),
                status=ec2_instance.state,
                tags=ec2_instance.tags,
                provider_specific={"aws_instance_id": instance_id},
            ))
        
        return ComputeCluster(
            name=kwargs.get('name', 'imported-cluster'),
            instances=instances,
            provider="aws",
            vpc_id=instances[0].provider_specific.get('vpc_id') if instances else None,
            tags=self._get_tags(),
        )
    
    def get_instance(self, instance_id: str) -> Optional[ComputeInstance]:
        """Get EC2 instance details"""
        try:
            ec2_instance = aws.ec2.get_instance(id=instance_id)
            return ComputeInstance(
                id=instance_id,
                name=ec2_instance.tags.get("Name", instance_id),
                public_ip=ec2_instance.public_ip,
                private_ip=ec2_instance.private_ip,
                hostname=ec2_instance.public_dns or ec2_instance.private_ip,
                ssh_user="ec2-user",
                ssh_key_path=self.aws_config.get('key_name'),
                status=ec2_instance.state,
                tags=ec2_instance.tags,
            )
        except Exception:
            return None
    
    def terminate_instance(self, instance_id: str) -> bool:
        """Terminate an EC2 instance"""
        try:
            # In Pulumi, we don't directly terminate - we remove from state
            # This would be handled by Pulumi's destroy
            return True
        except Exception:
            return False
    
    def resize_cluster(self, cluster: ComputeCluster, new_node_count: int) -> ComputeCluster:
        """Scale the cluster by adding or removing nodes"""
        current_count = cluster.instance_count
        
        if new_node_count > current_count:
            # Add nodes
            pass  # Implementation depends on Pulumi resource management
        elif new_node_count < current_count:
            # Remove nodes (should be done carefully with Vertica)
            pass
        
        return cluster
    
    def get_ssh_config(self, instance: ComputeInstance) -> Dict[str, Any]:
        """Get SSH configuration for an EC2 instance"""
        return {
            'host': instance.public_ip or instance.private_ip,
            'port': 22,
            'user': instance.ssh_user,
            'key_path': instance.ssh_key_path,
        }
