"""
Base compute abstractions for Vertica cluster deployment.

Provides common interfaces for instance/cluster lifecycle that can be
implemented by AWS, Azure, GCP, or bare-metal providers.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod


@dataclass
class ComputeInstance:
    """Represents a single compute instance."""
    
    instance_id: str
    name: str
    instance_type: str
    private_ip: str
    public_ip: Optional[str] = None
    hostname: str = ""
    status: str = "unknown"
    region: str = ""
    zone: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_primary(self) -> bool:
        """Check if this is the primary/coordinator node."""
        return self.tags.get("role", "") == "primary"
    
    def __str__(self) -> str:
        return f"ComputeInstance({self.name}, {self.instance_type}, {self.private_ip})"


@dataclass
class ComputeCluster:
    """Represents a cluster of compute instances."""
    
    name: str
    instances: List[ComputeInstance]
    provider: str = "unknown"
    vpc_id: Optional[str] = None
    subnet_id: Optional[str] = None
    security_group_id: Optional[str] = None
    key_pair_name: Optional[str] = None
    
    @property
    def instance_count(self) -> int:
        """Get number of instances in cluster."""
        return len(self.instances)
    
    @property
    def primary_instance(self) -> Optional[ComputeInstance]:
        """Get primary/coordinator instance."""
        for instance in self.instances:
            if instance.is_primary:
                return instance
        # Fallback to first instance
        return self.instances[0] if self.instances else None
    
    @property
    def all_ips(self) -> List[str]:
        """Get all private IPs."""
        return [i.private_ip for i in self.instances]
    
    def get_instance_by_ip(self, ip: str) -> Optional[ComputeInstance]:
        """Find instance by IP address."""
        for instance in self.instances:
            if instance.private_ip == ip or instance.public_ip == ip:
                return instance
        return None
    
    def get_instance_by_id(self, instance_id: str) -> Optional[ComputeInstance]:
        """Find instance by ID."""
        for instance in self.instances:
            if instance.instance_id == instance_id:
                return instance
        return None
    
    def __str__(self) -> str:
        return f"ComputeCluster({self.name}, {self.instance_count} nodes, provider={self.provider})"


class ComputeProvider(ABC):
    """Abstract base class for compute providers."""
    
    @abstractmethod
    def create_cluster(self, cluster_name: str, node_count: int,
                      instance_type: Optional[str] = None,
                      region: Optional[str] = None,
                      **kwargs) -> ComputeCluster:
        """
        Create a new cluster with the specified number of nodes.
        
        Args:
            cluster_name: Name for the cluster.
            node_count: Number of nodes to create.
            instance_type: Instance type/size.
            region: Cloud region.
            **kwargs: Additional provider-specific options.
            
        Returns:
            ComputeCluster with created instances.
        """
        pass
    
    @abstractmethod
    def destroy_cluster(self, cluster: ComputeCluster) -> None:
        """
        Destroy all instances in a cluster.
        
        Args:
            cluster: ComputeCluster to destroy.
        """
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
    def scale_down(self, cluster: ComputeCluster, node_ips: List[str]) -> None:
        """
        Remove nodes from an existing cluster.
        
        Args:
            cluster: Existing cluster.
            node_ips: IPs of nodes to remove.
        """
        pass
    
    @abstractmethod
    def get_instance_status(self, instance_id: str) -> str:
        """
        Get status of a single instance.
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            Status string.
        """
        pass
    
    @abstractmethod
    def start_instance(self, instance_id: str) -> bool:
        """
        Start a stopped instance.
        
        Args:
            instance_id: Instance to start.
            
        Returns:
            True if started successfully.
        """
        pass
    
    @abstractmethod
    def stop_instance(self, instance_id: str) -> bool:
        """
        Stop a running instance.
        
        Args:
            instance_id: Instance to stop.
            
        Returns:
            True if stopped successfully.
        """
        pass
    
    @abstractmethod
    def terminate_instance(self, instance_id: str) -> bool:
        """
        Terminate (delete) an instance.
        
        Args:
            instance_id: Instance to terminate.
            
        Returns:
            True if terminated successfully.
        """
        pass
    
    @abstractmethod
    def import_cluster(self, hosts: List[str], name: str = "vertica-cluster",
                      **kwargs) -> ComputeCluster:
        """
        Import an existing cluster by host IPs.
        
        Args:
            hosts: List of host IPs/names.
            name: Cluster name.
            **kwargs: Additional options.
            
        Returns:
            ComputeCluster with imported instances.
        """
        pass
    
    @abstractmethod
    def get_cluster_info(self, cluster_name: str) -> Optional[ComputeCluster]:
        """
        Get information about an existing cluster.
        
        Args:
            cluster_name: Cluster name.
            
        Returns:
            ComputeCluster or None if not found.
        """
        pass


class ClusterBuilder:
    """Helper for building ComputeCluster objects."""
    
    @staticmethod
    def from_instances(instances: List[ComputeInstance],
                      name: str = "vertica-cluster",
                      provider: str = "unknown") -> ComputeCluster:
        """Build a ComputeCluster from a list of instances."""
        return ComputeCluster(
            name=name,
            instances=instances,
            provider=provider,
        )
    
    @staticmethod
    def from_ips(ips: List[str], name: str = "vertica-cluster",
                provider: str = "unknown") -> ComputeCluster:
        """Build a ComputeCluster from IP addresses."""
        instances = []
        for i, ip in enumerate(ips):
            instances.append(ComputeInstance(
                instance_id=f"imported-{i}",
                name=f"node-{i}",
                instance_type="unknown",
                private_ip=ip,
                hostname=f"node-{i}",
                status="unknown",
            ))
        return ComputeCluster(name=name, instances=instances, provider=provider)
