"""
Bare metal and existing compute provider implementation.

This provider allows importing existing physical servers or VMs
that are not managed by a cloud provider.
"""

import pulumi
from typing import List, Optional, Dict, Any

from .base import ComputeProvider, ComputeInstance, ComputeCluster


class BareMetalProvider(ComputeProvider):
    """
    Provider for bare metal servers and existing infrastructure.
    
    This provider doesn't create resources but imports them into
    the Pulumi state for management and configuration.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider_name = "baremetal"
        self.baremetal_config = config.get('baremetal', {})
    
    def create_cluster(self, name: str, node_count: int, **kwargs) -> ComputeCluster:
        """
        Bare metal provider cannot create instances.
        Use import_cluster instead.
        """
        raise NotImplementedError(
            "BareMetalProvider cannot create instances. "
            "Use import_cluster() with existing server details."
        )
    
    def import_cluster(self, instance_ids: List[str], **kwargs) -> ComputeCluster:
        """
        Import existing bare metal servers into management.
        
        Args:
            instance_ids: List of IP addresses or hostnames
            **kwargs: Additional configuration:
                - ssh_user: SSH username (default: root)
                - ssh_key_path: Path to SSH private key
                - ssh_port: SSH port (default: 22)
                
        Returns:
            ComputeCluster with imported instances
        """
        hosts_config = self.baremetal_config.get('hosts', [])
        
        instances = []
        for host_config in hosts_config:
            hostname = host_config.get('hostname', 'unknown')
            ip = host_config.get('ip', '')
            
            if not ip:
                pulumi.log.warn(f"Host {hostname} has no IP address, skipping")
                continue
            
            instance = ComputeInstance(
                id=ip,  # Use IP as ID for bare metal
                name=hostname,
                public_ip=ip,
                private_ip=ip,
                hostname=hostname,
                ssh_user=host_config.get('ssh_user', 'root'),
                ssh_key_path=host_config.get('ssh_key_path', '~/.ssh/id_rsa'),
                ssh_port=host_config.get('ssh_port', 22),
                status="unknown",  # Will be determined during provisioning
                tags={"source": "baremetal", "hostname": hostname},
                provider_specific=host_config,
            )
            instances.append(instance)
        
        return ComputeCluster(
            name=kwargs.get('name', 'baremetal-cluster'),
            instances=instances,
            provider="baremetal",
            tags={"managed_by": "pulumi"},
        )
    
    def get_instance(self, instance_id: str) -> Optional[ComputeInstance]:
        """Get bare metal server by IP/ID"""
        hosts_config = self.baremetal_config.get('hosts', [])
        
        for host_config in hosts_config:
            if host_config.get('ip') == instance_id or host_config.get('hostname') == instance_id:
                return ComputeInstance(
                    id=instance_id,
                    name=host_config.get('hostname', instance_id),
                    public_ip=host_config.get('ip', instance_id),
                    private_ip=host_config.get('ip', instance_id),
                    hostname=host_config.get('hostname', instance_id),
                    ssh_user=host_config.get('ssh_user', 'root'),
                    ssh_key_path=host_config.get('ssh_key_path', '~/.ssh/id_rsa'),
                    ssh_port=host_config.get('ssh_port', 22),
                    status="unknown",
                    tags={"source": "baremetal"},
                    provider_specific=host_config,
                )
        
        return None
    
    def terminate_instance(self, instance_id: str) -> bool:
        """
        Bare metal instances cannot be terminated.
        Returns False to indicate physical machine remains.
        """
        pulumi.log.info(
            f"Bare metal instance {instance_id} cannot be terminated. "
            "Remove from cluster configuration instead."
        )
        return False
    
    def resize_cluster(self, cluster: ComputeCluster, 
                      new_node_count: int) -> ComputeCluster:
        """
        Resize bare metal cluster by updating host configuration.
        
        Note: Actual hardware changes must be done manually.
        This updates the Pulumi state to reflect new topology.
        """
        current_count = cluster.instance_count
        
        if new_node_count != current_count:
            pulumi.log.warn(
                f"Bare metal cluster resize from {current_count} to {new_node_count} "
                f"requires manual hardware changes. Update the hosts configuration "
                f"and re-run Pulumi import."
            )
        
        return cluster
    
    def get_ssh_config(self, instance: ComputeInstance) -> Dict[str, Any]:
        """Get SSH configuration for a bare metal server"""
        return {
            'host': instance.public_ip or instance.private_ip,
            'port': instance.ssh_port,
            'user': instance.ssh_user,
            'key_path': instance.ssh_key_path,
        }
