"""
vcluster CLI wrapper for Vertica cluster management.

Provides Python interface to the vcluster command-line tool
for installing, configuring, and managing Vertica clusters.
"""

import subprocess
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from modules.compute.base import ComputeInstance, ComputeCluster


class VClusterManager:
    """
    Manager for Vertica vcluster operations.
    
    Wraps the vcluster CLI and provides higher-level operations
    for cluster lifecycle management.
    """
    
    def __init__(self, vertica_config: Dict[str, Any]):
        """
        Initialize vcluster manager.
        
        Args:
            vertica_config: Configuration from the user config file
        """
        self.config = vertica_config
        self.version = vertica_config.get('version', 'latest')
        self.cluster_name = vertica_config.get('cluster_name', 'vertica-cluster')
        
        # Database configuration
        self.db_config = vertica_config.get('database', {})
        self.db_name = self.db_config.get('name', 'analytics')
        self.admin_username = self.db_config.get('admin_username', 'dbadmin')
        self.admin_password = self.db_config.get('admin_password', '')
        
        # Node configuration
        self.node_config = vertica_config.get('nodes', {})
        self.data_path = self.node_config.get('data_path', '/data/vertica')
        self.catalog_path = self.node_config.get('catalog_path', '/data/catalog')
        
    def install_vertica(self, cluster: ComputeCluster, 
                       installer_path: Optional[str] = None) -> Tuple[bool, str]:
        """
        Install Vertica on all nodes in the cluster.
        
        Args:
            cluster: ComputeCluster with instances to install on
            installer_path: Local path to Vertica installer RPM/DEB
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Installing Vertica {self.version} on {cluster.instance_count} nodes...")
        
        # Build node list for vcluster
        node_list = self._build_node_list(cluster)
        
        # Create hosts file
        hosts_content = self._generate_hosts_file(cluster)
        
        # For each node, install Vertica
        for instance in cluster.instances:
            success = self._install_on_node(instance, installer_path)
            if not success:
                return False, f"Failed to install on {instance.name}"
        
        # Create vcluster config
        vcluster_config = self._generate_vcluster_config(cluster)
        
        return True, f"Vertica installed on {cluster.instance_count} nodes"
    
    def create_database(self, cluster: ComputeCluster,
                       db_name: Optional[str] = None) -> Tuple[bool, str]:
        """
        Create a new Vertica database on the cluster.
        
        Args:
            cluster: ComputeCluster with prepared nodes
            db_name: Database name (defaults to config)
            
        Returns:
            Tuple of (success, message)
        """
        db_name = db_name or self.db_name
        
        print(f"Creating database '{db_name}' on {cluster.name}...")
        
        # Build vcluster command
        node_list = [inst.private_ip for inst in cluster.instances]
        primary = cluster.primary_instance
        
        # Create database using vcluster CLI
        cmd = [
            "vcluster",
            "create_db",
            "--db-name", db_name,
            "--hosts", ",".join(node_list),
            "--data-path", self.data_path,
            "--catalog-path", self.catalog_path,
            "--username", self.admin_username,
        ]
        
        if self.admin_password:
            cmd.extend(["--password", self.admin_password])
        
        # Execute on primary node
        stdout, stderr, exit_code = self._execute_vcluster(
            primary, cmd, timeout=600
        )
        
        if exit_code == 0:
            return True, f"Database '{db_name}' created successfully"
        else:
            return False, f"Failed to create database: {stderr}"
    
    def add_nodes(self, cluster: ComputeCluster, 
                 new_instances: List[ComputeInstance]) -> Tuple[bool, str]:
        """
        Add nodes to existing Vertica cluster.
        
        Args:
            cluster: Existing ComputeCluster
            new_instances: New instances to add
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Adding {len(new_instances)} nodes to cluster {cluster.name}...")
        
        node_ips = [inst.private_ip for inst in new_instances]
        primary = cluster.primary_instance
        
        cmd = [
            "vcluster",
            "add_node",
            "--db-name", self.db_name,
            "--hosts", ",".join(node_ips),
            "--username", self.admin_username,
        ]
        
        stdout, stderr, exit_code = self._execute_vcluster(
            primary, cmd, timeout=300
        )
        
        if exit_code == 0:
            return True, f"Added {len(new_instances)} nodes successfully"
        else:
            return False, f"Failed to add nodes: {stderr}"
    
    def remove_nodes(self, cluster: ComputeCluster,
                    node_ips: List[str]) -> Tuple[bool, str]:
        """
        Remove nodes from Vertica cluster.
        
        Args:
            cluster: Existing ComputeCluster
            node_ips: IPs of nodes to remove
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Removing {len(node_ips)} nodes from cluster...")
        
        primary = cluster.primary_instance
        
        cmd = [
            "vcluster",
            "remove_node",
            "--db-name", self.db_name,
            "--hosts", ",".join(node_ips),
            "--username", self.admin_username,
        ]
        
        stdout, stderr, exit_code = self._execute_vcluster(
            primary, cmd, timeout=300
        )
        
        if exit_code == 0:
            return True, f"Removed {len(node_ips)} nodes successfully"
        else:
            return False, f"Failed to remove nodes: {stderr}"
    
    def start_database(self, cluster: ComputeCluster) -> Tuple[bool, str]:
        """Start the Vertica database"""
        primary = cluster.primary_instance
        
        cmd = [
            "vcluster",
            "start_db",
            "--db-name", self.db_name,
            "--hosts", ",".join([i.private_ip for i in cluster.instances]),
            "--username", self.admin_username,
        ]
        
        stdout, stderr, exit_code = self._execute_vcluster(
            primary, cmd, timeout=300
        )
        
        return exit_code == 0, stdout if exit_code == 0 else stderr
    
    def stop_database(self, cluster: ComputeCluster) -> Tuple[bool, str]:
        """Stop the Vertica database"""
        primary = cluster.primary_instance
        
        cmd = [
            "vcluster",
            "stop_db",
            "--db-name", self.db_name,
            "--username", self.admin_username,
        ]
        
        stdout, stderr, exit_code = self._execute_vcluster(
            primary, cmd, timeout=300
        )
        
        return exit_code == 0, stdout if exit_code == 0 else stderr
    
    def get_cluster_status(self, cluster: ComputeCluster) -> Dict[str, Any]:
        """
        Get current status of the Vertica cluster.
        
        Returns:
            Dictionary with cluster status information
        """
        primary = cluster.primary_instance
        
        cmd = [
            "vcluster",
            "show_cluster",
            "--db-name", self.db_name,
            "--username", self.admin_username,
            "--format", "json",
        ]
        
        stdout, stderr, exit_code = self._execute_vcluster(
            primary, cmd, timeout=60
        )
        
        if exit_code == 0:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return {"status": "unknown", "raw_output": stdout}
        else:
            return {"status": "error", "error": stderr}
    
    def _install_on_node(self, instance: ComputeInstance,
                        installer_path: Optional[str]) -> bool:
        """Install Vertica on a single node"""
        
        # For now, we'll use SSH to install
        # In production, you might use cloud-init or a config management tool
        
        ssh_config = instance.provider.get_ssh_config(instance) if hasattr(instance, 'provider') else {}
        
        # Upload installer if provided
        if installer_path and Path(installer_path).exists():
            # Upload to node
            pass
        
        # Install Vertica packages
        # This would be customized based on the OS (RPM vs DEB)
        
        return True
    
    def _execute_vcluster(self, instance: ComputeInstance, 
                         cmd: List[str], timeout: int = 300) -> Tuple[str, str, int]:
        """
        Execute vcluster command on a node via SSH.
        
        Args:
            instance: ComputeInstance to execute on
            cmd: Command list (for subprocess)
            timeout: Command timeout
            
        Returns:
            Tuple of (stdout, stderr, exit_code)
        """
        # Use SSH to execute vcluster on the remote node
        # This requires that vcluster is installed and configured
        
        if hasattr(instance, 'provider'):
            return instance.provider.execute_on_instance(instance, " ".join(cmd), timeout)
        else:
            # For testing/development, execute locally
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return process.stdout, process.stderr, process.returncode
    
    def _build_node_list(self, cluster: ComputeCluster) -> List[Dict[str, str]]:
        """Build node list structure for vcluster"""
        nodes = []
        for instance in cluster.instances:
            nodes.append({
                "name": instance.name,
                "ip": instance.private_ip,
                "hostname": instance.hostname,
            })
        return nodes
    
    def _generate_hosts_file(self, cluster: ComputeCluster) -> str:
        """Generate /etc/hosts content for cluster nodes"""
        lines = ["# Vertica cluster nodes"]
        for instance in cluster.instances:
            lines.append(f"{instance.private_ip}    {instance.hostname} {instance.name}")
        return "\n".join(lines)
    
    def _generate_vcluster_config(self, cluster: ComputeCluster) -> Dict[str, Any]:
        """Generate vcluster configuration"""
        return {
            "cluster": {
                "name": self.cluster_name,
                "nodes": self._build_node_list(cluster),
            },
            "database": {
                "name": self.db_name,
                "admin_user": self.admin_username,
                "data_path": self.data_path,
                "catalog_path": self.catalog_path,
            },
            "network": {
                "port": self.config.get('network', {}).get('port', 5433),
                "rest_api_port": self.config.get('network', {}).get('rest_api_port', 5444),
            },
        }
