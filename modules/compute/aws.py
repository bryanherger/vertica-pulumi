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
    for Vertica cluster deployment. Supports full lifecycle:
    provision, terminate, scale up, scale down, start, stop.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider_name = "aws"
        self.aws_config = config.get('aws', {})

        # Get AWS provider configuration
        self.region = self.aws_config.get('region', 'us-east-1')

    # ------------------------------------------------------------------
    # Cluster lifecycle
    # ------------------------------------------------------------------

    def create_cluster(self, cluster_name: str, node_count: int,
                       instance_type: Optional[str] = None,
                       region: Optional[str] = None,
                       **kwargs) -> ComputeCluster:
        """
        Create a complete AWS infrastructure for Vertica cluster.

        Creates:
        - VPC with Internet Gateway
        - Public subnet with route table
        - Security group with Vertica ports
        - EC2 instances with appropriate sizing
        - EBS volumes for data storage
        """
        region = region or self.region

        # Create VPC
        vpc = aws.ec2.Vpc(
            f"{cluster_name}-vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags={"Name": f"{cluster_name}-vpc", **self._get_tags()},
            opts=pulumi.ResourceOptions(provider=self._get_provider(region)),
        )

        # Create Internet Gateway
        igw = aws.ec2.InternetGateway(
            f"{cluster_name}-igw",
            vpc_id=vpc.id,
            tags={"Name": f"{cluster_name}-igw", **self._get_tags()},
            opts=pulumi.ResourceOptions(provider=self._get_provider(region)),
        )

        # Create public subnet
        subnet = aws.ec2.Subnet(
            f"{cluster_name}-subnet",
            vpc_id=vpc.id,
            cidr_block="10.0.1.0/24",
            map_public_ip_on_launch=True,
            availability_zone=self._get_availability_zone(region),
            tags={"Name": f"{cluster_name}-subnet", **self._get_tags()},
            opts=pulumi.ResourceOptions(provider=self._get_provider(region)),
        )

        # Create route table
        route_table = aws.ec2.RouteTable(
            f"{cluster_name}-rt",
            vpc_id=vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    gateway_id=igw.id,
                )
            ],
            tags={"Name": f"{cluster_name}-rt", **self._get_tags()},
            opts=pulumi.ResourceOptions(provider=self._get_provider(region)),
        )

        # Associate route table with subnet
        route_table_association = aws.ec2.RouteTableAssociation(
            f"{cluster_name}-rt-assoc",
            subnet_id=subnet.id,
            route_table_id=route_table.id,
            opts=pulumi.ResourceOptions(provider=self._get_provider(region)),
        )

        # Create security group for Vertica
        security_group = self._create_security_group(cluster_name, vpc.id, region)

        # Create SSH key pair if specified
        key_name = self.aws_config.get('key_name', '')

        # Create instances
        instances = []
        instance_outputs = []
        for i in range(node_count):
            instance = self._create_instance(
                cluster_name=cluster_name,
                index=i,
                subnet_id=subnet.id,
                security_group_id=security_group.id,
                key_name=key_name,
                instance_type=instance_type,
                region=region,
            )
            instances.append(instance)
            instance_outputs.append(instance)

        # Build ComputeCluster from created resources
        return self._build_cluster_output(cluster_name, vpc.id, subnet.id,
                                            security_group.id, key_name, region,
                                            instance_outputs)

    def destroy_cluster(self, cluster: ComputeCluster) -> None:
        """
        Destroy all instances in a cluster.

        Note: In Pulumi, resources are destroyed by removing them from
        the program. This method is called for imperative cleanup.
        """
        for instance in cluster.instances:
            self.terminate_instance(instance.instance_id)

    # ------------------------------------------------------------------
    # Scaling operations
    # ------------------------------------------------------------------

    def scale_up(self, cluster: ComputeCluster, additional_nodes: int,
                instance_type: Optional[str] = None) -> List[ComputeInstance]:
        """
        Add nodes to an existing cluster.

        Args:
            cluster: Existing cluster.
            additional_nodes: Number of nodes to add.
            instance_type: Instance type for new nodes.

        Returns:
            List of new ComputeInstances.
        """
        new_instances = []
        current_count = cluster.instance_count

        # Get existing VPC/subnet/security group from cluster
        vpc_id = cluster.vpc_id
        subnet_id = cluster.subnet_id
        sg_id = cluster.security_group_id
        region = cluster.instances[0].region if cluster.instances else self.region
        key_name = cluster.key_pair_name or self.aws_config.get('key_name', '')

        for i in range(additional_nodes):
            idx = current_count + i
            instance = self._create_instance(
                cluster_name=cluster.name,
                index=idx,
                subnet_id=pulumi.Output.from_input(subnet_id) if subnet_id else None,
                security_group_id=pulumi.Output.from_input(sg_id) if sg_id else None,
                key_name=key_name,
                instance_type=instance_type,
                region=region,
                tags={"role": "secondary"},
            )
            new_instances.append(
                ComputeInstance(
                    instance_id=instance.id,
                    name=f"{cluster.name}-node-{idx + 1}",
                    instance_type=instance_type or self.aws_config.get('instance_type', 'r6i.2xlarge'),
                    private_ip="",  # Will be filled after apply
                    public_ip="",
                    hostname=f"{cluster.name}-node-{idx + 1}",
                    status="pending",
                    region=region,
                )
            )

        cluster.instances.extend(new_instances)
        return new_instances

    def scale_down(self, cluster: ComputeCluster, node_ips: List[str]) -> None:
        """
        Remove nodes from an existing cluster.

        Args:
            cluster: Existing cluster.
            node_ips: IPs of nodes to remove.
        """
        for ip in node_ips:
            instance = cluster.get_instance_by_ip(ip)
            if instance:
                self.terminate_instance(instance.instance_id)

        # Update cluster object
        cluster.instances = [
            i for i in cluster.instances
            if i.private_ip not in node_ips and i.public_ip not in node_ips
        ]

    # ------------------------------------------------------------------
    # Instance operations
    # ------------------------------------------------------------------

    def get_instance_status(self, instance_id: str) -> str:
        """Get status of a single instance."""
        try:
            ec2_instance = aws.ec2.get_instance(id=instance_id)
            return ec2_instance.state
        except Exception:
            return "unknown"

    def start_instance(self, instance_id: str) -> bool:
        """Start a stopped instance."""
        try:
            # In Pulumi, we don't directly start/stop via SDK
            # This would typically be done via AWS CLI or boto3
            import boto3
            ec2 = boto3.client('ec2', region_name=self.region)
            ec2.start_instances(InstanceIds=[instance_id])
            return True
        except Exception:
            return False

    def stop_instance(self, instance_id: str) -> bool:
        """Stop a running instance."""
        try:
            import boto3
            ec2 = boto3.client('ec2', region_name=self.region)
            ec2.stop_instances(InstanceIds=[instance_id])
            return True
        except Exception:
            return False

    def terminate_instance(self, instance_id: str) -> bool:
        """Terminate (delete) an instance."""
        try:
            import boto3
            ec2 = boto3.client('ec2', region_name=self.region)
            ec2.terminate_instances(InstanceIds=[instance_id])
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Import / lookup
    # ------------------------------------------------------------------

    def import_cluster(self, hosts: List[str], name: str = "vertica-cluster",
                      **kwargs) -> ComputeCluster:
        """Import an existing cluster by host IPs."""
        instances = []
        for i, ip in enumerate(hosts):
            instances.append(ComputeInstance(
                instance_id=f"imported-{i}",
                name=f"{name}-node-{i + 1}",
                instance_type="unknown",
                private_ip=ip,
                hostname=f"node-{i + 1}",
                status="unknown",
                region=self.region,
            ))
        return ComputeCluster(
            name=name,
            instances=instances,
            provider="aws",
        )

    def get_cluster_info(self, cluster_name: str) -> Optional[ComputeCluster]:
        """Get information about an existing cluster by name."""
        try:
            import boto3
            ec2 = boto3.client('ec2', region_name=self.region)

            # Find instances by tag
            response = ec2.describe_instances(
                Filters=[
                    {"Name": "tag:Name", "Values": [f"{cluster_name}-node-*"]},
                ]
            )

            instances = []
            for reservation in response.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    instances.append(ComputeInstance(
                        instance_id=inst["InstanceId"],
                        name=tags.get("Name", inst["InstanceId"]),
                        instance_type=inst["InstanceType"],
                        private_ip=inst.get("PrivateIpAddress", ""),
                        public_ip=inst.get("PublicIpAddress", None),
                        hostname=tags.get("Name", ""),
                        status=inst["State"]["Name"],
                        region=self.region,
                        tags=tags,
                    ))

            if not instances:
                return None

            return ComputeCluster(
                name=cluster_name,
                instances=instances,
                provider="aws",
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_provider(self, region: str):
        """Get or create Pulumi AWS provider for region."""
        # In a real implementation, cache providers
        return None  # Uses default provider

    def _create_security_group(self, cluster_name: str, vpc_id: pulumi.Output[str],
                               region: Optional[str] = None) -> aws.ec2.SecurityGroup:
        """Create security group with Vertica-specific rules."""
        # Default rules if none specified in config
        default_rules = [
            {"protocol": "tcp", "port": 22, "cidr": "0.0.0.0/0", "description": "SSH"},
            {"protocol": "tcp", "port": 5433, "cidr": "0.0.0.0/0", "description": "Vertica client"},
            {"protocol": "tcp", "port": 5444, "cidr": "0.0.0.0/0", "description": "Vertica REST API"},
            {"protocol": "tcp", "port": 5434, "cidr": "0.0.0.0/0", "description": "Vertica internode"},
            {"protocol": "tcp", "port": 4803, "cidr": "0.0.0.0/0", "description": "Spread"},
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
            f"{cluster_name}-sg",
            vpc_id=vpc_id,
            description=f"Security group for {cluster_name} Vertica cluster",
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
            tags={"Name": f"{cluster_name}-sg", **self._get_tags()},
            opts=pulumi.ResourceOptions(provider=self._get_provider(region or self.region)),
        )

        return sg

    def _create_instance(self, cluster_name: str, index: int,
                         subnet_id: Optional[pulumi.Output[str]],
                         security_group_id: Optional[pulumi.Output[str]],
                         key_name: str,
                         instance_type: Optional[str] = None,
                         region: Optional[str] = None,
                         tags: Optional[Dict[str, str]] = None) -> aws.ec2.Instance:
        """Create a single EC2 instance."""
        instance_name = f"{cluster_name}-node-{index + 1}"

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
                ],
                opts=pulumi.ResourceOptions(provider=self._get_provider(region or self.region)),
            )
            ami_id = ami.id

        # Get instance type
        instance_type = instance_type or self.aws_config.get('instance_type', 'r6i.2xlarge')

        # Root volume configuration
        root_volume_size = self.aws_config.get('root_volume_size', 100)

        # User data for bootstrap
        user_data = self._generate_user_data()

        instance_tags = {
            "Name": instance_name,
            "VerticaNode": str(index),
            **self._get_tags(),
        }
        if tags:
            instance_tags.update(tags)
        if index == 0:
            instance_tags["role"] = "primary"

        # Optional IAM instance profile for S3/communal storage access
        iam_instance_profile = self.aws_config.get('iam_instance_profile')

        instance_kwargs = dict(
            ami=ami_id,
            instance_type=instance_type,
            subnet_id=subnet_id,
            vpc_security_group_ids=[security_group_id] if security_group_id else [],
            key_name=key_name if key_name else None,
            user_data=user_data,
            root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
                volume_size=root_volume_size,
                volume_type="gp3",
                encrypted=True,
            ),
            tags=instance_tags,
            opts=pulumi.ResourceOptions(provider=self._get_provider(region or self.region)),
        )
        if iam_instance_profile:
            instance_kwargs["iam_instance_profile"] = iam_instance_profile

        instance = aws.ec2.Instance(
            instance_name,
            **instance_kwargs,
        )

        # Add additional data volumes if configured
        additional_volumes = self.aws_config.get('additional_volumes', [])
        for vol_idx, vol_config in enumerate(additional_volumes):
            ebs_volume = aws.ec2.EbsVolume(
                f"{instance_name}-data-vol-{vol_idx}",
                availability_zone=self._get_availability_zone(region or self.region),
                size=vol_config.get('size', 500),
                type=vol_config.get('type', 'gp3'),
                encrypted=True,
                tags={"Name": f"{instance_name}-data-{vol_idx}", **self._get_tags()},
                opts=pulumi.ResourceOptions(provider=self._get_provider(region or self.region)),
            )

            aws.ec2.VolumeAttachment(
                f"{instance_name}-vol-attach-{vol_idx}",
                device_name=f"/dev/sd{chr(ord('f') + vol_idx)}",
                volume_id=ebs_volume.id,
                instance_id=instance.id,
                opts=pulumi.ResourceOptions(provider=self._get_provider(region or self.region)),
            )

        return instance

    def _build_cluster_output(self, cluster_name: str, vpc_id: pulumi.Output[str],
                              subnet_id: pulumi.Output[str],
                              security_group_id: pulumi.Output[str],
                              key_pair_name: str,
                              region: str,
                              instances: List[aws.ec2.Instance]) -> ComputeCluster:
        """Build ComputeCluster from created Pulumi resources."""
        # In a real implementation, use Output.apply to extract values
        # For now, return placeholder
        compute_instances = []
        for i, instance in enumerate(instances):
            compute_instances.append(
                ComputeInstance(
                    instance_id=instance.id,
                    name=f"{cluster_name}-node-{i + 1}",
                    instance_type="",
                    private_ip="",
                    public_ip=None,
                    hostname=f"{cluster_name}-node-{i + 1}",
                    status="pending",
                    region=region,
                )
            )

        return ComputeCluster(
            name=cluster_name,
            instances=compute_instances,
            provider="aws",
            vpc_id=vpc_id,
            subnet_id=subnet_id,
            security_group_id=security_group_id,
            key_pair_name=key_pair_name,
        )

    def _generate_user_data(self) -> str:
        """Generate cloud-init user data for bootstrap."""
        return """#!/bin/bash
# Basic bootstrap for Vertica nodes
echo "Vertica node bootstrap complete"
"""

    def _get_tags(self) -> Dict[str, str]:
        """Get common tags from config."""
        return self.aws_config.get('tags', {})

    def _get_availability_zone(self, region: Optional[str] = None) -> str:
        """Get availability zone for the region."""
        azs = aws.get_availability_zones(
            state="available",
            opts=pulumi.ResourceOptions(provider=self._get_provider(region or self.region)),
        )
        return azs.names[0] if azs.names else f"{region or self.region}a"

    def get_ssh_config(self, instance: ComputeInstance) -> Dict[str, Any]:
        """Get SSH configuration for an EC2 instance."""
        return {
            'host': instance.public_ip or instance.private_ip,
            'port': 22,
            'user': 'ec2-user',
            'key_path': self.aws_config.get('key_name'),
        }
