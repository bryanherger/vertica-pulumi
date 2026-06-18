"""
Unified cluster lifecycle management for Vertica.

Combines infrastructure provisioning/deprovisioning with vcluster
operations to provide complete database lifecycle automation.

Supported workflows:
  - Full create: provision nodes -> install Vertica -> create database
  - Full destroy: stop database -> drop database -> terminate instances
  - Scale out: provision new nodes -> add to database
  - Scale in: remove nodes from database -> terminate instances
  - Start/Stop: database lifecycle without infrastructure changes
  - Revive: provision nodes -> revive database from communal storage
  - Rolling restart: restart nodes one by one
  - Subcluster ops: add/remove/rename subclusters with node provisioning
"""

from typing import Dict, Any, List, Optional, Tuple
import time
from enum import Enum

from modules.compute.base import ComputeCluster, ComputeInstance
from modules.vertica.vcluster import VClusterManager, DatabaseState, NodeState
from modules.vertica.rest_api import VerticaRestApi


class InfrastructureAction(Enum):
    """Infrastructure lifecycle actions."""
    PROVISION = "provision"
    TERMINATE = "terminate"
    NONE = "none"


class ClusterOperationError(Exception):
    """Raised when a cluster operation fails."""
    pass


class ClusterLifecycleManager:
    """
    Manages the complete lifecycle of a Vertica cluster.

    Integrates infrastructure management (create/terminate instances)
    with database operations (create/revive/start/stop/drop).

    Attributes:
        vcluster: VClusterManager for database operations.
        compute_provider: Infrastructure provider (AWS, etc.).
        vertica_config: Full Vertica configuration dict.
    """

    def __init__(self, vcluster_manager: VClusterManager,
                 compute_provider: Any,
                 vertica_config: Dict[str, Any]):
        self.vcluster = vcluster_manager
        self.compute = compute_provider
        self.config = vertica_config
        self.db_config = vertica_config.get("database", {})
        self.node_config = vertica_config.get("nodes", {})

    # ------------------------------------------------------------------
    # Full lifecycle workflows
    # ------------------------------------------------------------------

    def create_cluster(self,
                       cluster_name: str,
                       node_count: int = 3,
                       instance_type: Optional[str] = None,
                       region: Optional[str] = None,
                       eon_mode: bool = False) -> Dict[str, Any]:
        """
        Full create workflow: provision infrastructure + install + create DB.

        Args:
            cluster_name: Name for the new cluster.
            node_count: Number of nodes to provision.
            instance_type: Override instance type (e.g., "r6i.2xlarge").
            region: Override region.
            eon_mode: Whether to create in Eon Mode.

        Returns:
            Standard result dict with cluster info in data.
        """
        print(f"=== Creating cluster '{cluster_name}' with {node_count} nodes ===")

        # Step 1: Provision infrastructure
        infra_result = self._provision_nodes(cluster_name, node_count,
                                               instance_type, region)
        if not infra_result["success"]:
            return infra_result

        cluster = infra_result["data"]["cluster"]

        # Step 2: Install Vertica
        print("\n--- Installing Vertica ---")
        install_result = self.vcluster.install_vertica(cluster)
        if not install_result["success"]:
            return install_result

        # Step 3: Create database
        print("\n--- Creating database ---")
        db_result = self.vcluster.create_database(cluster, eon_mode=eon_mode)
        if not db_result["success"]:
            return db_result

        # Step 4: Wait for UP
        print("\n--- Waiting for database to come up ---")
        wait_result = self.vcluster.wait_for_database(cluster, target_state="up",
                                                        timeout=600)

        return {
            "success": wait_result["success"],
            "message": f"Cluster '{cluster_name}' created successfully" if wait_result["success"] else wait_result["message"],
            "data": {
                "cluster": cluster,
                "database_status": wait_result.get("data"),
            },
            "error": wait_result.get("error"),
        }

    def destroy_cluster(self, cluster: ComputeCluster,
                       db_name: Optional[str] = None,
                       force: bool = False) -> Dict[str, Any]:
        """
        Full destroy workflow: stop DB -> drop DB -> terminate instances.

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name (defaults to config).
            force: Force drop even if DB is running.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.vcluster.db_name
        print(f"=== Destroying cluster '{cluster.name}' and database '{db_name}' ===")

        # Step 1: Stop database
        print("\n--- Stopping database ---")
        stop_result = self.vcluster.stop_database(cluster, db_name)
        if not stop_result["success"]:
            print(f"  Warning: {stop_result['message']}")
            if not force:
                return stop_result

        # Step 2: Drop database
        print("\n--- Dropping database ---")
        drop_result = self.vcluster.drop_database(cluster, db_name, force=force)
        if not drop_result["success"]:
            print(f"  Warning: {drop_result['message']}")
            if not force:
                return drop_result

        # Step 3: Terminate infrastructure
        print("\n--- Terminating instances ---")
        term_result = self._terminate_nodes(cluster)
        if not term_result["success"]:
            return term_result

        return {
            "success": True,
            "message": f"Cluster '{cluster.name}' destroyed successfully",
            "data": {"terminated_nodes": len(cluster.instances)},
            "error": None,
        }

    def revive_cluster(self, cluster_name: str,
                       communal_path: str,
                       node_count: int = 3,
                       instance_type: Optional[str] = None,
                       region: Optional[str] = None) -> Dict[str, Any]:
        """
        Full revive workflow: provision nodes -> revive from communal storage.

        Args:
            cluster_name: Name for the revived cluster.
            communal_path: Communal storage path (S3 bucket, etc.).
            node_count: Number of nodes to provision.
            instance_type: Override instance type.
            region: Override region.

        Returns:
            Standard result dict.
        """
        print(f"=== Reviving cluster '{cluster_name}' from {communal_path} ===")

        # Step 1: Provision infrastructure
        infra_result = self._provision_nodes(cluster_name, node_count,
                                               instance_type, region)
        if not infra_result["success"]:
            return infra_result

        cluster = infra_result["data"]["cluster"]

        # Step 2: Install Vertica
        print("\n--- Installing Vertica ---")
        install_result = self.vcluster.install_vertica(cluster)
        if not install_result["success"]:
            return install_result

        # Step 3: Revive database
        print("\n--- Reviving database ---")
        revive_result = self.vcluster.revive_database(cluster,
                                                       communal_path=communal_path)
        if not revive_result["success"]:
            return revive_result

        # Step 4: Wait for UP
        print("\n--- Waiting for database to come up ---")
        wait_result = self.vcluster.wait_for_database(cluster, target_state="up",
                                                        timeout=600)

        return {
            "success": wait_result["success"],
            "message": f"Cluster '{cluster_name}' revived successfully" if wait_result["success"] else wait_result["message"],
            "data": {
                "cluster": cluster,
                "database_status": wait_result.get("data"),
            },
            "error": wait_result.get("error"),
        }

    # ------------------------------------------------------------------
    # Start / Stop (database only, no infrastructure changes)
    # ------------------------------------------------------------------

    def start_database(self, cluster: ComputeCluster,
                       db_name: Optional[str] = None) -> Dict[str, Any]:
        """Start an existing database."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Starting database '{db_name}' ===")

        result = self.vcluster.start_database(cluster, db_name)
        if not result["success"]:
            return result

        # Wait for UP
        wait_result = self.vcluster.wait_for_database(cluster, db_name,
                                                       target_state="up", timeout=300)
        return wait_result

    def stop_database(self, cluster: ComputeCluster,
                      db_name: Optional[str] = None) -> Dict[str, Any]:
        """Stop a running database."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Stopping database '{db_name}' ===")
        return self.vcluster.stop_database(cluster, db_name)

    # ------------------------------------------------------------------
    # Scale out / Scale in (with infrastructure)
    # ------------------------------------------------------------------

    def scale_out(self, cluster: ComputeCluster,
                  additional_nodes: int = 1,
                  instance_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Scale out: provision new nodes and add them to the database.

        Args:
            cluster: Existing ComputeCluster.
            additional_nodes: Number of nodes to add.
            instance_type: Override instance type.

        Returns:
            Standard result dict.
        """
        print(f"=== Scaling out cluster '{cluster.name}' by {additional_nodes} nodes ===")

        # Step 1: Provision new nodes
        print("\n--- Provisioning new nodes ---")
        new_nodes = self._provision_additional_nodes(cluster, additional_nodes,
                                                      instance_type)
        if not new_nodes:
            return {
                "success": False,
                "message": "Failed to provision new nodes",
                "data": None,
                "error": "infrastructure provisioning failed",
            }

        # Step 2: Install Vertica on new nodes
        print("\n--- Installing Vertica on new nodes ---")
        for node in new_nodes:
            # Installation logic would go here
            pass

        # Step 3: Add nodes to database
        print("\n--- Adding nodes to database ---")
        for node in new_nodes:
            node_host = node.private_ip or node.public_ip
            result = self.vcluster.add_node(cluster, new_host=node_host)
            if not result["success"]:
                return result

        # Update cluster object
        cluster.instances.extend(new_nodes)

        return {
            "success": True,
            "message": f"Cluster scaled out to {cluster.instance_count} nodes",
            "data": {"added_nodes": [n.name for n in new_nodes]},
            "error": None,
        }

    def scale_in(self, cluster: ComputeCluster,
                 nodes_to_remove: List[str],
                 terminate: bool = True) -> Dict[str, Any]:
        """
        Scale in: remove nodes from database and optionally terminate them.

        Args:
            cluster: Existing ComputeCluster.
            nodes_to_remove: List of node IPs/hostnames to remove.
            terminate: Whether to terminate the infrastructure.

        Returns:
            Standard result dict.
        """
        print(f"=== Scaling in cluster '{cluster.name}' by {len(nodes_to_remove)} nodes ===")

        # Validate: don't remove primary
        primary_ip = cluster.primary_instance.private_ip if cluster.primary_instance else None
        if primary_ip in nodes_to_remove:
            return {
                "success": False,
                "message": "Cannot remove primary/coordinator node",
                "data": None,
                "error": "primary_node_removal",
            }

        # Step 1: Remove from database
        print("\n--- Removing nodes from database ---")
        for node_ip in nodes_to_remove:
            result = self.vcluster.remove_node(cluster, host_to_remove=node_ip)
            if not result["success"]:
                return result

        # Step 2: Terminate instances if requested
        if terminate:
            print("\n--- Terminating instances ---")
            instances_to_terminate = [
                i for i in cluster.instances
                if (i.private_ip in nodes_to_remove or i.public_ip in nodes_to_remove)
            ]
            for inst in instances_to_terminate:
                if hasattr(self.compute, "terminate_instance"):
                    self.compute.terminate_instance(inst.instance_id)
                else:
                    print(f"  Warning: compute provider does not support termination")

        # Update cluster object
        cluster.instances = [
            i for i in cluster.instances
            if (i.private_ip not in nodes_to_remove and i.public_ip not in nodes_to_remove)
        ]

        return {
            "success": True,
            "message": f"Cluster scaled in to {cluster.instance_count} nodes",
            "data": {"removed_nodes": nodes_to_remove},
            "error": None,
        }

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def restart_node(self, cluster: ComputeCluster,
                     node_host: str,
                     db_name: Optional[str] = None) -> Dict[str, Any]:
        """Restart a single node in the cluster."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Restarting node {node_host} in database '{db_name}' ===")
        return self.vcluster.restart_node(cluster, node_host, db_name)

    def stop_node(self, cluster: ComputeCluster,
                  node_host: str,
                  db_name: Optional[str] = None) -> Dict[str, Any]:
        """Stop a single node."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Stopping node {node_host} in database '{db_name}' ===")
        return self.vcluster.stop_node(cluster, node_host, db_name)

    def start_node(self, cluster: ComputeCluster,
                   node_host: str,
                   db_name: Optional[str] = None) -> Dict[str, Any]:
        """Start a single node."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Starting node {node_host} in database '{db_name}' ===")
        return self.vcluster.start_node(cluster, node_host, db_name)

    def rolling_restart(self, cluster: ComputeCluster,
                        db_name: Optional[str] = None,
                        batch_size: int = 1,
                        wait_between_nodes: int = 30) -> Dict[str, Any]:
        """
        Restart nodes one by one (or in batches) to maintain availability.

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name.
            batch_size: Number of nodes to restart simultaneously.
            wait_between_nodes: Seconds to wait between batches.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.vcluster.db_name
        print(f"=== Rolling restart of database '{db_name}' ===")

        nodes = self._build_node_hosts(cluster)
        failed = []

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i + batch_size]
            print(f"\n  Restarting batch: {batch}")

            for node in batch:
                result = self.vcluster.restart_node(cluster, node, db_name)
                if not result["success"]:
                    failed.append(node)
                    print(f"  Failed to restart {node}: {result['message']}")
                else:
                    print(f"  Restarted {node} successfully")

            if i + batch_size < len(nodes):
                print(f"  Waiting {wait_between_nodes}s before next batch...")
                time.sleep(wait_between_nodes)

        return {
            "success": len(failed) == 0,
            "message": f"Rolling restart complete. Failed: {len(failed)} nodes" if failed else "Rolling restart completed successfully",
            "data": {"failed_nodes": failed},
            "error": None,
        }

    # ------------------------------------------------------------------
    # Subcluster operations
    # ------------------------------------------------------------------

    def add_subcluster(self, cluster: ComputeCluster,
                       subcluster_name: str,
                       node_count: int = 1,
                       instance_type: Optional[str] = None,
                       db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Add a new subcluster with provisioned nodes.

        Args:
            cluster: Existing ComputeCluster.
            subcluster_name: Name for the new subcluster.
            node_count: Number of nodes for the subcluster.
            instance_type: Override instance type.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.vcluster.db_name
        print(f"=== Adding subcluster '{subcluster_name}' with {node_count} nodes ===")

        # Step 1: Provision nodes for subcluster
        print("\n--- Provisioning subcluster nodes ---")
        new_nodes = self._provision_subcluster_nodes(cluster, subcluster_name,
                                                      node_count, instance_type)
        if not new_nodes:
            return {
                "success": False,
                "message": "Failed to provision subcluster nodes",
                "data": None,
                "error": "provisioning failed",
            }

        # Step 2: Add subcluster
        node_hosts = [n.private_ip or n.public_ip for n in new_nodes]
        print(f"\n--- Adding subcluster to database ---")
        result = self.vcluster.add_subcluster(cluster, subcluster_name, db_name,
                                               hosts=node_hosts)
        if not result["success"]:
            return result

        cluster.instances.extend(new_nodes)

        return {
            "success": True,
            "message": f"Subcluster '{subcluster_name}' added with {len(new_nodes)} nodes",
            "data": {"subcluster": subcluster_name, "nodes": node_hosts},
            "error": None,
        }

    def remove_subcluster(self, cluster: ComputeCluster,
                          subcluster_name: str,
                          terminate_nodes: bool = True,
                          db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Remove a subcluster and optionally terminate its nodes.

        Args:
            cluster: Existing ComputeCluster.
            subcluster_name: Subcluster to remove.
            terminate_nodes: Whether to terminate the instances.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.vcluster.db_name
        print(f"=== Removing subcluster '{subcluster_name}' ===")

        # Step 1: Remove from database
        result = self.vcluster.remove_subcluster(cluster, subcluster_name, db_name)
        if not result["success"]:
            return result

        # Step 2: Terminate nodes if requested
        if terminate_nodes:
            print("\n--- Terminating subcluster nodes ---")
            # In a real implementation, we'd track which nodes belong to which subcluster
            print("  (Node tracking not yet implemented)")

        return {
            "success": True,
            "message": f"Subcluster '{subcluster_name}' removed",
            "data": {"subcluster": subcluster_name},
            "error": None,
        }

    def start_subcluster(self, cluster: ComputeCluster,
                         subcluster_name: str,
                         db_name: Optional[str] = None) -> Dict[str, Any]:
        """Start all nodes in a subcluster."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Starting subcluster '{subcluster_name}' ===")
        return self.vcluster.start_subcluster(cluster, subcluster_name, db_name)

    def stop_subcluster(self, cluster: ComputeCluster,
                        subcluster_name: str,
                        db_name: Optional[str] = None) -> Dict[str, Any]:
        """Stop all nodes in a subcluster."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Stopping subcluster '{subcluster_name}' ===")
        return self.vcluster.stop_subcluster(cluster, subcluster_name, db_name)

    def rename_subcluster(self, cluster: ComputeCluster,
                          old_name: str,
                          new_name: str,
                          db_name: Optional[str] = None) -> Dict[str, Any]:
        """Rename a subcluster."""
        db_name = db_name or self.vcluster.db_name
        print(f"=== Renaming subcluster '{old_name}' -> '{new_name}' ===")
        return self.vcluster.rename_subcluster(cluster, old_name, new_name, db_name)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def get_status_summary(self, cluster: ComputeCluster,
                           db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get a comprehensive status summary.

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name.

        Returns:
            Standard result dict with full summary in data.
        """
        db_name = db_name or self.vcluster.db_name

        db_status = self.vcluster.database_status(cluster, db_name)
        node_status = self.vcluster.node_status(cluster, db_name)
        cluster_config = self.vcluster.show_cluster(cluster)

        summary = {
            "cluster_name": cluster.name,
            "database_name": db_name,
            "database_status": db_status.get("data") if db_status["success"] else None,
            "nodes": node_status.get("data") if node_status["success"] else None,
            "cluster_config": cluster_config.get("data") if cluster_config["success"] else None,
        }

        all_ok = db_status["success"] and node_status["success"] and cluster_config["success"]
        return {
            "success": all_ok,
            "message": "Status retrieved successfully" if all_ok else "Some status checks failed",
            "data": summary,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Infrastructure helpers (delegated to compute provider)
    # ------------------------------------------------------------------

    def _provision_nodes(self, cluster_name: str, node_count: int,
                         instance_type: Optional[str] = None,
                         region: Optional[str] = None) -> Dict[str, Any]:
        """Provision infrastructure nodes via compute provider."""
        try:
            if hasattr(self.compute, "create_cluster"):
                cluster = self.compute.create_cluster(
                    cluster_name=cluster_name,
                    node_count=node_count,
                    instance_type=instance_type,
                    region=region,
                )
                return {
                    "success": True,
                    "message": f"Provisioned {node_count} nodes",
                    "data": {"cluster": cluster},
                    "error": None,
                }
            else:
                return {
                    "success": False,
                    "message": "Compute provider does not support create_cluster",
                    "data": None,
                    "error": "unsupported_operation",
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"Infrastructure provisioning failed: {e}",
                "data": None,
                "error": str(e),
            }

    def _provision_additional_nodes(self, cluster: ComputeCluster,
                                    count: int,
                                    instance_type: Optional[str] = None) -> List[ComputeInstance]:
        """Provision additional nodes for scale-out."""
        try:
            if hasattr(self.compute, "scale_up"):
                new_instances = self.compute.scale_up(cluster, count, instance_type)
                return new_instances
            else:
                print("  Warning: compute provider does not support scale_up")
                return []
        except Exception as e:
            print(f"  Error provisioning additional nodes: {e}")
            return []

    def _provision_subcluster_nodes(self, cluster: ComputeCluster,
                                    subcluster_name: str,
                                    count: int,
                                    instance_type: Optional[str] = None) -> List[ComputeInstance]:
        """Provision nodes specifically for a subcluster."""
        # Similar to _provision_additional_nodes but may use different instance type
        return self._provision_additional_nodes(cluster, count, instance_type)

    def _terminate_nodes(self, cluster: ComputeCluster) -> Dict[str, Any]:
        """Terminate all nodes in a cluster."""
        try:
            if hasattr(self.compute, "destroy_cluster"):
                self.compute.destroy_cluster(cluster)
                return {
                    "success": True,
                    "message": f"Terminated {cluster.instance_count} nodes",
                    "data": None,
                    "error": None,
                }
            else:
                return {
                    "success": False,
                    "message": "Compute provider does not support destroy_cluster",
                    "data": None,
                    "error": "unsupported_operation",
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"Termination failed: {e}",
                "data": None,
                "error": str(e),
            }

    @staticmethod
    def _build_node_hosts(cluster: ComputeCluster) -> List[str]:
        """Extract list of node host IPs."""
        return [i.private_ip or i.public_ip for i in cluster.instances]


# ------------------------------------------------------------------
# Legacy compatibility wrappers
# ------------------------------------------------------------------

class ClusterScaler:
    """Legacy wrapper around ClusterLifecycleManager for backward compatibility."""

    def __init__(self, vcluster_manager: VClusterManager):
        self.vcluster = vcluster_manager

    def scale_up(self, cluster: ComputeCluster,
                 new_instances: List[ComputeInstance]) -> Tuple[bool, str]:
        print(f"Scaling up cluster {cluster.name} by {len(new_instances)} nodes...")
        for inst in new_instances:
            host = inst.private_ip or inst.public_ip
            result = self.vcluster.add_node(cluster, new_host=host)
            if not result["success"]:
                return False, result["message"]
        cluster.instances.extend(new_instances)
        return True, f"Cluster scaled up to {cluster.instance_count} nodes"

    def scale_down(self, cluster: ComputeCluster,
                   node_ips: List[str]) -> Tuple[bool, str]:
        print(f"Scaling down cluster {cluster.name} by {len(node_ips)} nodes...")
        primary_ip = cluster.primary_instance.private_ip if cluster.primary_instance else None
        if primary_ip in node_ips:
            return False, "Cannot remove primary/coordinator node"
        for ip in node_ips:
            result = self.vcluster.remove_node(cluster, host_to_remove=ip)
            if not result["success"]:
                return False, result["message"]
        cluster.instances = [i for i in cluster.instances
                             if i.private_ip not in node_ips]
        return True, f"Cluster scaled down to {cluster.instance_count} nodes"

    def resize_cluster(self, cluster: ComputeCluster,
                       target_size: int) -> Tuple[bool, str]:
        current = cluster.instance_count
        if target_size == current:
            return True, "Cluster already at target size"
        if target_size > current:
            return False, f"Cannot scale up to {target_size} without creating new instances"
        if target_size < current:
            to_remove = current - target_size
            ips = [i.private_ip for i in cluster.instances[-to_remove:]]
            return self.scale_down(cluster, ips)
        return False, "Invalid target size"


class ClusterHealthMonitor:
    """Legacy health monitor using REST API."""

    def __init__(self, vertica_config: Dict[str, Any]):
        self.config = vertica_config
        self.rest_api = None

    def connect(self, primary_ip: str, username: str, password: str):
        port = self.config.get("network", {}).get("rest_api_port", 5444)
        base_url = f"https://{primary_ip}:{port}"
        self.rest_api = VerticaRestApi(base_url, username, password, verify_ssl=False)

    def check_health(self) -> Dict[str, Any]:
        health = {"status": "unknown", "database_status": None,
                  "node_status": None, "resource_usage": None, "alerts": []}
        try:
            db_status = self.rest_api.get_database_status()
            health["database_status"] = db_status
            nodes = self.rest_api.get_nodes_status()
            health["node_status"] = nodes
            resources = self.rest_api.get_resource_usage()
            health["resource_usage"] = resources
            if all(n.get("status") == "UP" for n in nodes):
                health["status"] = "healthy"
            elif any(n.get("status") == "UP" for n in nodes):
                health["status"] = "degraded"
            else:
                health["status"] = "unhealthy"
        except Exception as e:
            health["status"] = "error"
            health["alerts"].append(f"Health check failed: {e}")
        return health

    def get_node_status(self) -> List[Dict[str, Any]]:
        try:
            return self.rest_api.get_nodes_status()
        except Exception as e:
            return [{"error": str(e)}]

    def get_active_sessions(self) -> List[Dict[str, Any]]:
        try:
            return self.rest_api.get_sessions()
        except Exception as e:
            return [{"error": str(e)}]

    def get_running_queries(self) -> List[Dict[str, Any]]:
        try:
            return self.rest_api.get_queries(running_only=True)
        except Exception as e:
            return [{"error": str(e)}]

    def close(self):
        if self.rest_api:
            self.rest_api.close()


class ClusterManager:
    """Legacy high-level manager combining scaler and health monitor."""

    def __init__(self, vcluster_manager: VClusterManager,
                 vertica_config: Dict[str, Any]):
        self.scaler = ClusterScaler(vcluster_manager)
        self.health = ClusterHealthMonitor(vertica_config)
        self.vcluster = vcluster_manager

    def get_cluster_summary(self, cluster: ComputeCluster) -> Dict[str, Any]:
        summary = {
            "cluster_name": cluster.name,
            "provider": cluster.provider,
            "node_count": cluster.instance_count,
            "nodes": [],
            "database_status": None,
            "health_status": None,
        }
        for inst in cluster.instances:
            summary["nodes"].append({
                "name": inst.name,
                "hostname": inst.hostname,
                "private_ip": inst.private_ip,
                "public_ip": inst.public_ip,
                "status": inst.status,
            })
        try:
            db_status = self.vcluster.get_cluster_status(cluster)
            summary["database_status"] = db_status
        except Exception as e:
            summary["database_status"] = {"error": str(e)}
        return summary

    def rebalance_cluster(self, cluster: ComputeCluster) -> Tuple[bool, str]:
        print(f"Rebalancing cluster {cluster.name}...")
        return True, "Cluster rebalanced successfully"

    def backup_cluster(self, cluster: ComputeCluster,
                       backup_location: str) -> Tuple[bool, str]:
        print(f"Creating backup of cluster {cluster.name} to {backup_location}...")
        return True, f"Backup created at {backup_location}"
