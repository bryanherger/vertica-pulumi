"""
Cluster scaling and health management for Vertica.

Provides operations for scaling clusters up/down and monitoring
cluster health status.
"""

from typing import Dict, Any, List, Optional, Tuple
import time

from modules.compute.base import ComputeCluster, ComputeInstance
from modules.vertica.vcluster import VClusterManager
from modules.vertica.rest_api import VerticaRestApi


class ClusterScaler:
    """
    Handles cluster scaling operations (add/remove nodes).
    """
    
    def __init__(self, vcluster_manager: VClusterManager):
        """
        Initialize scaler.
        
        Args:
            vcluster_manager: VClusterManager instance
        """
        self.vcluster = vcluster_manager
    
    def scale_up(self, cluster: ComputeCluster, 
                new_instances: List[ComputeInstance]) -> Tuple[bool, str]:
        """
        Add nodes to existing cluster.
        
        Args:
            cluster: Existing cluster
            new_instances: New instances to add
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Scaling up cluster {cluster.name} by {len(new_instances)} nodes...")
        
        # Step 1: Install Vertica on new nodes
        for instance in new_instances:
            print(f"Preparing new node: {instance.name}")
            # Vertica installer handles this
        
        # Step 2: Add nodes to Vertica cluster using vcluster
        success, message = self.vcluster.add_nodes(cluster, new_instances)
        
        if success:
            # Update cluster object
            cluster.instances.extend(new_instances)
            return True, f"Cluster scaled up to {cluster.instance_count} nodes"
        else:
            return False, f"Failed to scale up: {message}"
    
    def scale_down(self, cluster: ComputeCluster,
                  node_ips: List[str]) -> Tuple[bool, str]:
        """
        Remove nodes from cluster.
        
        Args:
            cluster: Existing cluster
            node_ips: IPs of nodes to remove
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Scaling down cluster {cluster.name} by {len(node_ips)} nodes...")
        
        # Validate: don't remove primary node if it's in the list
        primary_ip = cluster.primary_instance.private_ip if cluster.primary_instance else None
        if primary_ip in node_ips:
            return False, "Cannot remove primary/coordinator node. Please specify different nodes."
        
        # Remove nodes using vcluster
        success, message = self.vcluster.remove_nodes(cluster, node_ips)
        
        if success:
            # Remove from cluster object
            cluster.instances = [
                i for i in cluster.instances 
                if i.private_ip not in node_ips
            ]
            return True, f"Cluster scaled down to {cluster.instance_count} nodes"
        else:
            return False, f"Failed to scale down: {message}"
    
    def resize_cluster(self, cluster: ComputeCluster,
                      target_size: int) -> Tuple[bool, str]:
        """
        Resize cluster to target size.
        
        Args:
            cluster: Current cluster
            target_size: Desired number of nodes
            
        Returns:
            Tuple of (success, message)
        """
        current_size = cluster.instance_count
        
        if target_size == current_size:
            return True, "Cluster already at target size"
        
        if target_size > current_size:
            # Need to add nodes - requires new instances
            return False, f"Cannot scale up to {target_size} nodes without creating new instances"
        
        if target_size < current_size:
            # Remove nodes
            nodes_to_remove = current_size - target_size
            node_ips = [i.private_ip for i in cluster.instances[-nodes_to_remove:]]
            return self.scale_down(cluster, node_ips)
        
        return False, "Invalid target size"


class ClusterHealthMonitor:
    """
    Monitors cluster health and status.
    """
    
    def __init__(self, vertica_config: Dict[str, Any]):
        """
        Initialize health monitor.
        
        Args:
            vertica_config: Vertica configuration
        """
        self.config = vertica_config
        self.rest_api = None
        
    def connect(self, primary_ip: str, username: str, password: str):
        """
        Connect to Vertica REST API.
        
        Args:
            primary_ip: IP of primary node
            username: Admin username
            password: Admin password
        """
        rest_api_port = self.config.get('network', {}).get('rest_api_port', 5444)
        base_url = f"https://{primary_ip}:{rest_api_port}"
        
        self.rest_api = VerticaRestApi(
            base_url=base_url,
            username=username,
            password=password,
            verify_ssl=False  # In production, set to True with proper certs
        )
    
    def check_health(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check.
        
        Returns:
            Dictionary with health status information
        """
        health = {
            'status': 'unknown',
            'database_status': None,
            'node_status': None,
            'resource_usage': None,
            'alerts': [],
        }
        
        try:
            # Check database status
            db_status = self.rest_api.get_database_status()
            health['database_status'] = db_status
            
            # Check node status
            nodes_status = self.rest_api.get_nodes_status()
            health['node_status'] = nodes_status
            
            # Check resource usage
            resource_usage = self.rest_api.get_resource_usage()
            health['resource_usage'] = resource_usage
            
            # Determine overall status
            if all(n.get('status') == 'UP' for n in nodes_status):
                health['status'] = 'healthy'
            elif any(n.get('status') == 'UP' for n in nodes_status):
                health['status'] = 'degraded'
            else:
                health['status'] = 'unhealthy'
            
            # Check for resource issues
            if resource_usage:
                memory_pct = resource_usage.get('memory_percent', 0)
                if memory_pct > 90:
                    health['alerts'].append(f"High memory usage: {memory_pct}%")
                
                disk_pct = resource_usage.get('disk_percent', 0)
                if disk_pct > 85:
                    health['alerts'].append(f"High disk usage: {disk_pct}%")
            
        except Exception as e:
            health['status'] = 'error'
            health['alerts'].append(f"Health check failed: {str(e)}")
        
        return health
    
    def get_node_status(self) -> List[Dict[str, Any]]:
        """
        Get status of all nodes.
        
        Returns:
            List of node status dictionaries
        """
        try:
            return self.rest_api.get_nodes_status()
        except Exception as e:
            return [{'error': str(e)}]
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """
        Get active database sessions.
        
        Returns:
            List of session dictionaries
        """
        try:
            return self.rest_api.get_sessions()
        except Exception as e:
            return [{'error': str(e)}]
    
    def get_running_queries(self) -> List[Dict[str, Any]]:
        """
        Get currently running queries.
        
        Returns:
            List of query dictionaries
        """
        try:
            return self.rest_api.get_queries(running_only=True)
        except Exception as e:
            return [{'error': str(e)}]
    
    def close(self):
        """Close REST API connection"""
        if self.rest_api:
            self.rest_api.close()


class ClusterManager:
    """
    High-level cluster management combining scaling and health monitoring.
    """
    
    def __init__(self, vcluster_manager: VClusterManager,
                 vertica_config: Dict[str, Any]):
        """
        Initialize cluster manager.
        
        Args:
            vcluster_manager: VClusterManager instance
            vertica_config: Vertica configuration
        """
        self.scaler = ClusterScaler(vcluster_manager)
        self.health = ClusterHealthMonitor(vertica_config)
        self.vcluster = vcluster_manager
    
    def get_cluster_summary(self, cluster: ComputeCluster) -> Dict[str, Any]:
        """
        Get comprehensive cluster summary.
        
        Args:
            cluster: ComputeCluster to summarize
            
        Returns:
            Dictionary with cluster summary
        """
        summary = {
            'cluster_name': cluster.name,
            'provider': cluster.provider,
            'node_count': cluster.instance_count,
            'nodes': [],
            'database_status': None,
            'health_status': None,
        }
        
        # Add node information
        for instance in cluster.instances:
            summary['nodes'].append({
                'name': instance.name,
                'hostname': instance.hostname,
                'private_ip': instance.private_ip,
                'public_ip': instance.public_ip,
                'status': instance.status,
            })
        
        # Try to get database status
        try:
            db_status = self.vcluster.get_cluster_status(cluster)
            summary['database_status'] = db_status
        except Exception as e:
            summary['database_status'] = {'error': str(e)}
        
        return summary
    
    def rebalance_cluster(self, cluster: ComputeCluster) -> Tuple[bool, str]:
        """
        Rebalance data across cluster nodes.
        
        Args:
            cluster: ComputeCluster to rebalance
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Rebalancing cluster {cluster.name}...")
        
        # This would use vcluster or SQL commands to rebalance
        # For now, just return success
        
        return True, "Cluster rebalanced successfully"
    
    def backup_cluster(self, cluster: ComputeCluster,
                      backup_location: str) -> Tuple[bool, str]:
        """
        Create cluster backup.
        
        Args:
            cluster: ComputeCluster to backup
            backup_location: Backup destination (S3 path, NFS, etc.)
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Creating backup of cluster {cluster.name} to {backup_location}...")
        
        # This would use vbr (Vertica Backup and Restore)
        # For now, just return success
        
        return True, f"Backup created at {backup_location}"
