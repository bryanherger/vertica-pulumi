"""
Comprehensive vcluster CLI wrapper for Vertica cluster management.

Provides a Python interface to all major vcluster command-line operations
for full lifecycle management of Vertica databases on provisioned infrastructure.

Supported operations:
  - Database lifecycle: create_db, revive_db, start_db, stop_db, drop_db
  - Node management: add_node, remove_node, restart_node, stop_node, start_node
  - Subcluster management: add_subcluster, remove_subcluster, stop_subcluster, start_subcluster, rename_subcluster
  - Status & info: list_all_db, db_status, node_status, show_cluster, list_node
  - Maintenance: re_ip, revoke, manage_config, show_config, set_config_parameter
  - Eon Mode: revive_db, stop_db, start_db (with communal storage)

All commands return a standardized result dict: {"success": bool, "message": str, "data": dict|None, "error": str|None}
"""

import subprocess
import json
import time
import re
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from enum import Enum

from modules.compute.base import ComputeInstance, ComputeCluster


class DatabaseState(Enum):
    """Known database states returned by vcluster db_status."""
    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"
    INITIALIZING = "initializing"
    RECOVERING = "recovering"
    SHUTDOWN = "shutdown"


class NodeState(Enum):
    """Known node states returned by vcluster node_status."""
    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"
    INITIALIZING = "initializing"
    RECOVERING = "recovering"
    SHUTDOWN = "shutdown"
    STANDBY = "standby"


class VClusterCommandError(Exception):
    """Raised when a vcluster command fails."""
    pass


class VClusterManager:
    """
    Manager for Vertica vcluster operations.

    Wraps the vcluster CLI and provides higher-level operations
    for cluster and database lifecycle management.

    Attributes:
        config: Vertica configuration dict (typically from config/config.yaml).
        version: Vertica version string.
        cluster_name: Logical cluster name.
        db_name: Default database name.
        db_config: Database-level config dict.
        node_config: Node-level config dict.
        admin_username: Database admin username.
        admin_password: Database admin password.
        data_path: Vertica data directory on nodes.
        catalog_path: Vertica catalog directory on nodes.
        communal_storage: Eon Mode communal storage config (optional).
    """

    def __init__(self, vertica_config: Dict[str, Any]):
        self.config = vertica_config
        self.version = vertica_config.get("version", "latest")
        self.cluster_name = vertica_config.get("cluster_name", "vertica-cluster")

        # Database configuration
        self.db_config = vertica_config.get("database", {})
        self.db_name = self.db_config.get("name", "analytics")
        self.admin_username = self.db_config.get("admin_username", "dbadmin")
        self.admin_password = self.db_config.get("admin_password", "")
        self.shard_count = self.db_config.get("shard_count", 6)

        # Node configuration
        self.node_config = vertica_config.get("nodes", {})
        self.data_path = self.node_config.get("data_path", "/data/vertica")
        self.catalog_path = self.node_config.get("catalog_path", "/data/catalog")
        self.depot_path = self.node_config.get("depot_path", "/data/depot")

        # Network
        self.network_config = vertica_config.get("network", {})
        self.port = self.network_config.get("port", 5433)
        self.rest_api_port = self.network_config.get("rest_api_port", 5444)

        # Security
        self.security_config = vertica_config.get("security", {})
        self.ssl_mode = self.security_config.get("ssl_mode", "prefer")

        # Eon Mode / communal storage
        self.communal_storage = vertica_config.get("communal_storage", {})
        self.is_eon_mode = bool(self.communal_storage)

        # vcluster binary path (auto-detected or overridden)
        self.vcluster_path = vertica_config.get("vcluster_path", self._detect_vcluster())

    def _detect_vcluster(self) -> str:
        """Attempt to locate the vcluster binary."""
        candidates = [
            "/opt/vertica/bin/vcluster",
            "/usr/bin/vcluster",
            "/usr/local/bin/vcluster",
        ]
        for c in candidates:
            if Path(c).exists():
                return c
        return "vcluster"  # fallback to PATH lookup

    # ------------------------------------------------------------------
    # Low-level command execution
    # ------------------------------------------------------------------

    def _run_vcluster(self, args: List[str], json_output: bool = True,
                      timeout: int = 300, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Execute a vcluster command and return a standardized result.

        Args:
            args: vcluster subcommand + arguments.
            json_output: Whether to request JSON output.
            timeout: Command timeout in seconds.
            env: Optional extra environment variables.

        Returns:
            {"success": bool, "message": str, "data": dict|None, "error": str|None}
        """
        cmd = [self.vcluster_path]
        if json_output:
            cmd.append("--json")
        cmd.extend(args)

        # Ensure LANG is set for consistent parsing
        merged_env = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        if env:
            merged_env.update(env)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=merged_env,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "success": False,
                "message": f"vcluster command timed out after {timeout}s",
                "data": None,
                "error": str(exc),
            }
        except FileNotFoundError as exc:
            return {
                "success": False,
                "message": f"vcluster binary not found: {self.vcluster_path}",
                "data": None,
                "error": str(exc),
            }

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            return {
                "success": False,
                "message": f"vcluster failed (exit {result.returncode})",
                "data": None,
                "error": stderr or stdout,
            }

        data = None
        if json_output and stdout:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                # Some commands return plain text even with --json
                pass

        return {
            "success": True,
            "message": "vcluster command succeeded",
            "data": data,
            "error": None,
        }

    def _build_node_hosts(self, cluster: ComputeCluster) -> List[str]:
        """Build list of node host IPs/names for vcluster commands."""
        return [i.private_ip or i.public_ip for i in cluster.instances]

    def _build_hosts_str(self, cluster: ComputeCluster) -> str:
        """Comma-separated node hosts."""
        return ",".join(self._build_node_hosts(cluster))

    def _get_primary_host(self, cluster: ComputeCluster) -> str:
        """Return the primary (first) node host."""
        if cluster.primary_instance:
            return cluster.primary_instance.private_ip or cluster.primary_instance.public_ip
        return self._build_node_hosts(cluster)[0]

    # ------------------------------------------------------------------
    # Installation helpers
    # ------------------------------------------------------------------

    def install_vertica(self, cluster: ComputeCluster,
                        installer_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Install Vertica on all nodes in the cluster.

        Args:
            cluster: ComputeCluster with instances to install on.
            installer_path: Local path to Vertica installer RPM/DEB.

        Returns:
            Standard result dict.
        """
        print(f"Installing Vertica {self.version} on {cluster.instance_count} nodes...")
        node_list = self._build_node_list(cluster)
        hosts_content = self._generate_hosts_file(cluster)

        # Install on each node (could be parallelised)
        for instance in cluster.instances:
            ok = self._install_on_node(instance, installer_path)
            if not ok:
                return {
                    "success": False,
                    "message": f"Failed to install on {instance.name}",
                    "data": None,
                    "error": "Installation failed",
                }

        vcluster_config = self._generate_vcluster_config(cluster)
        return {
            "success": True,
            "message": f"Vertica installed on {cluster.instance_count} nodes",
            "data": {"nodes": node_list, "config": vcluster_config},
            "error": None,
        }

    def _install_on_node(self, instance: ComputeInstance,
                         installer_path: Optional[str]) -> bool:
        """Install Vertica on a single node (placeholder for provider-specific logic)."""
        # Delegated to provider/SSH; stubbed here.
        return True

    # ------------------------------------------------------------------
    # Database Lifecycle
    # ------------------------------------------------------------------

    def create_database(self, cluster: ComputeCluster,
                        db_name: Optional[str] = None,
                        shard_count: Optional[int] = None,
                        eon_mode: bool = False) -> Dict[str, Any]:
        """
        Create a new Vertica database on the cluster.

        Maps to: vcluster create_db

        Args:
            cluster: ComputeCluster with prepared nodes.
            db_name: Database name (defaults to config).
            shard_count: Number of shards (Eon Mode only).
            eon_mode: Whether to create in Eon Mode.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        shard_count = shard_count or self.shard_count
        hosts = self._build_hosts_str(cluster)

        args = [
            "create_db",
            "--db-name", db_name,
            "--hosts", hosts,
            "--data-path", self.data_path,
            "--catalog-path", self.catalog_path,
        ]

        if eon_mode or self.is_eon_mode:
            args.extend(["--eon-mode"])
            if shard_count:
                args.extend(["--shard-count", str(shard_count)])
            if self.communal_storage:
                args.extend(["--communal-storage-location", self.communal_storage.get("path", "")])

        if self.admin_username:
            args.extend(["--db-user", self.admin_username])

        print(f"Creating database '{db_name}' on {cluster.instance_count} node(s)...")
        return self._run_vcluster(args, timeout=600)

    def revive_database(self, cluster: ComputeCluster,
                        db_name: Optional[str] = None,
                        communal_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Revive an Eon Mode database from communal storage.

        Maps to: vcluster revive_db

        Args:
            cluster: ComputeCluster with nodes to revive onto.
            db_name: Database name.
            communal_path: Communal storage path (overrides config).

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        communal_path = communal_path or self.communal_storage.get("path", "")
        hosts = self._build_hosts_str(cluster)

        args = [
            "revive_db",
            "--db-name", db_name,
            "--hosts", hosts,
            "--communal-storage-location", communal_path,
        ]

        print(f"Reviving database '{db_name}' from communal storage...")
        return self._run_vcluster(args, timeout=600)

    def stop_database(self, cluster: ComputeCluster,
                      db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Stop a running database.

        Maps to: vcluster stop_db

        Args:
            cluster: ComputeCluster.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "stop_db",
            "--db-name", db_name,
            "--hosts", hosts,
        ]

        print(f"Stopping database '{db_name}'...")
        return self._run_vcluster(args, timeout=300)

    def start_database(self, cluster: ComputeCluster,
                       db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Start a stopped database.

        Maps to: vcluster start_db

        Args:
            cluster: ComputeCluster.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "start_db",
            "--db-name", db_name,
            "--hosts", hosts,
        ]

        print(f"Starting database '{db_name}'...")
        return self._run_vcluster(args, timeout=300)

    def drop_database(self, cluster: ComputeCluster,
                      db_name: Optional[str] = None,
                      force: bool = False) -> Dict[str, Any]:
        """
        Drop (delete) a database.

        Maps to: vcluster drop_db

        Args:
            cluster: ComputeCluster.
            db_name: Database name.
            force: Force deletion even if database is running.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "drop_db",
            "--db-name", db_name,
            "--hosts", hosts,
        ]
        if force:
            args.append("--force")

        print(f"Dropping database '{db_name}'...")
        return self._run_vcluster(args, timeout=300)

    # ------------------------------------------------------------------
    # Node Management
    # ------------------------------------------------------------------

    def add_node(self, cluster: ComputeCluster,
                 new_host: str,
                 db_name: Optional[str] = None,
                 subcluster: Optional[str] = None) -> Dict[str, Any]:
        """
        Add a new node to the database.

        Maps to: vcluster add_node

        Args:
            cluster: Existing ComputeCluster.
            new_host: IP or hostname of the new node.
            db_name: Database name.
            subcluster: Subcluster to add the node to.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        existing_hosts = self._build_hosts_str(cluster)

        args = [
            "add_node",
            "--db-name", db_name,
            "--hosts", existing_hosts,
            "--new-hosts", new_host,
        ]
        if subcluster:
            args.extend(["--subcluster", subcluster])

        print(f"Adding node {new_host} to database '{db_name}'...")
        return self._run_vcluster(args, timeout=600)

    def remove_node(self, cluster: ComputeCluster,
                    host_to_remove: str,
                    db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Remove a node from the database.

        Maps to: vcluster remove_node

        Args:
            cluster: Existing ComputeCluster.
            host_to_remove: IP or hostname of the node to remove.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "remove_node",
            "--db-name", db_name,
            "--hosts", hosts,
            "--remove-hosts", host_to_remove,
        ]

        print(f"Removing node {host_to_remove} from database '{db_name}'...")
        return self._run_vcluster(args, timeout=600)

    def restart_node(self, cluster: ComputeCluster,
                     node_host: str,
                     db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Restart a single node.

        Maps to: vcluster restart_node

        Args:
            cluster: Existing ComputeCluster.
            node_host: IP or hostname of the node to restart.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "restart_node",
            "--db-name", db_name,
            "--hosts", hosts,
            "--restart-hosts", node_host,
        ]

        print(f"Restarting node {node_host}...")
        return self._run_vcluster(args, timeout=300)

    def stop_node(self, cluster: ComputeCluster,
                  node_host: str,
                  db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Stop a single node.

        Maps to: vcluster stop_node

        Args:
            cluster: Existing ComputeCluster.
            node_host: IP or hostname of the node to stop.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "stop_node",
            "--db-name", db_name,
            "--hosts", hosts,
            "--stop-hosts", node_host,
        ]

        print(f"Stopping node {node_host}...")
        return self._run_vcluster(args, timeout=300)

    def start_node(self, cluster: ComputeCluster,
                   node_host: str,
                   db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Start a single node.

        Maps to: vcluster start_node

        Args:
            cluster: Existing ComputeCluster.
            node_host: IP or hostname of the node to start.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "start_node",
            "--db-name", db_name,
            "--hosts", hosts,
            "--start-hosts", node_host,
        ]

        print(f"Starting node {node_host}...")
        return self._run_vcluster(args, timeout=300)

    # ------------------------------------------------------------------
    # Subcluster Management
    # ------------------------------------------------------------------

    def add_subcluster(self, cluster: ComputeCluster,
                       subcluster_name: str,
                       db_name: Optional[str] = None,
                       hosts: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Add a new subcluster to the database.

        Maps to: vcluster add_subcluster

        Args:
            cluster: Existing ComputeCluster.
            subcluster_name: Name for the new subcluster.
            db_name: Database name.
            hosts: List of node hosts for the subcluster.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        all_hosts = self._build_hosts_str(cluster)

        args = [
            "add_subcluster",
            "--db-name", db_name,
            "--hosts", all_hosts,
            "--subcluster", subcluster_name,
        ]
        if hosts:
            args.extend(["--sc-hosts", ",".join(hosts)])

        print(f"Adding subcluster '{subcluster_name}' to database '{db_name}'...")
        return self._run_vcluster(args, timeout=600)

    def remove_subcluster(self, cluster: ComputeCluster,
                          subcluster_name: str,
                          db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Remove a subcluster from the database.

        Maps to: vcluster remove_subcluster

        Args:
            cluster: Existing ComputeCluster.
            subcluster_name: Name of the subcluster to remove.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "remove_subcluster",
            "--db-name", db_name,
            "--hosts", hosts,
            "--subcluster", subcluster_name,
        ]

        print(f"Removing subcluster '{subcluster_name}' from database '{db_name}'...")
        return self._run_vcluster(args, timeout=600)

    def stop_subcluster(self, cluster: ComputeCluster,
                        subcluster_name: str,
                        db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Stop all nodes in a subcluster.

        Maps to: vcluster stop_subcluster

        Args:
            cluster: Existing ComputeCluster.
            subcluster_name: Subcluster to stop.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "stop_subcluster",
            "--db-name", db_name,
            "--hosts", hosts,
            "--subcluster", subcluster_name,
        ]

        print(f"Stopping subcluster '{subcluster_name}'...")
        return self._run_vcluster(args, timeout=300)

    def start_subcluster(self, cluster: ComputeCluster,
                         subcluster_name: str,
                         db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Start all nodes in a subcluster.

        Maps to: vcluster start_subcluster

        Args:
            cluster: Existing ComputeCluster.
            subcluster_name: Subcluster to start.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "start_subcluster",
            "--db-name", db_name,
            "--hosts", hosts,
            "--subcluster", subcluster_name,
        ]

        print(f"Starting subcluster '{subcluster_name}'...")
        return self._run_vcluster(args, timeout=300)

    def rename_subcluster(self, cluster: ComputeCluster,
                          old_name: str,
                          new_name: str,
                          db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Rename a subcluster.

        Maps to: vcluster rename_subcluster

        Args:
            cluster: Existing ComputeCluster.
            old_name: Current subcluster name.
            new_name: New subcluster name.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "rename_subcluster",
            "--db-name", db_name,
            "--hosts", hosts,
            "--subcluster", old_name,
            "--new-subcluster", new_name,
        ]

        print(f"Renaming subcluster '{old_name}' -> '{new_name}'...")
        return self._run_vcluster(args, timeout=300)

    # ------------------------------------------------------------------
    # Status & Information
    # ------------------------------------------------------------------

    def list_all_databases(self, host: Optional[str] = None) -> Dict[str, Any]:
        """
        List all databases known to vcluster.

        Maps to: vcluster list_all_db

        Args:
            host: Optional host to query from.

        Returns:
            Standard result dict with database list in data["databases"].
        """
        args = ["list_all_db"]
        if host:
            args.extend(["--host", host])

        print("Listing all databases...")
        return self._run_vcluster(args, timeout=60)

    def database_status(self, cluster: ComputeCluster,
                        db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get the status of a database.

        Maps to: vcluster db_status

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name.

        Returns:
            Standard result dict with status in data["status"].
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "db_status",
            "--db-name", db_name,
            "--hosts", hosts,
        ]

        print(f"Checking status of database '{db_name}'...")
        return self._run_vcluster(args, timeout=60)

    def node_status(self, cluster: ComputeCluster,
                    db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get the status of all nodes in the database.

        Maps to: vcluster node_status

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name.

        Returns:
            Standard result dict with node list in data["nodes"].
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        args = [
            "node_status",
            "--db-name", db_name,
            "--hosts", hosts,
        ]

        print(f"Checking node status for database '{db_name}'...")
        return self._run_vcluster(args, timeout=60)

    def show_cluster(self, cluster: ComputeCluster) -> Dict[str, Any]:
        """
        Show cluster configuration.

        Maps to: vcluster show_cluster

        Args:
            cluster: Existing ComputeCluster.

        Returns:
            Standard result dict with cluster info in data.
        """
        hosts = self._build_hosts_str(cluster)
        args = [
            "show_cluster",
            "--hosts", hosts,
        ]

        print("Showing cluster configuration...")
        return self._run_vcluster(args, timeout=60)

    def list_nodes(self, cluster: ComputeCluster) -> Dict[str, Any]:
        """
        List all nodes in the cluster.

        Maps to: vcluster list_node

        Args:
            cluster: Existing ComputeCluster.

        Returns:
            Standard result dict with node list in data["nodes"].
        """
        hosts = self._build_hosts_str(cluster)
        args = [
            "list_node",
            "--hosts", hosts,
        ]

        print("Listing cluster nodes...")
        return self._run_vcluster(args, timeout=60)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def re_ip(self, cluster: ComputeCluster,
              old_ips: List[str],
              new_ips: List[str]) -> Dict[str, Any]:
        """
        Reconfigure IP addresses for nodes.

        Maps to: vcluster re_ip

        Args:
            cluster: Existing ComputeCluster.
            old_ips: List of old IP addresses.
            new_ips: List of new IP addresses.

        Returns:
            Standard result dict.
        """
        if len(old_ips) != len(new_ips):
            return {
                "success": False,
                "message": "old_ips and new_ips must have same length",
                "data": None,
                "error": "IP list mismatch",
            }

        hosts = self._build_hosts_str(cluster)
        old_str = ",".join(old_ips)
        new_str = ",".join(new_ips)

        args = [
            "re_ip",
            "--hosts", hosts,
            "--old-ips", old_str,
            "--new-ips", new_str,
        ]

        print(f"Re-IP: {old_str} -> {new_str}")
        return self._run_vcluster(args, timeout=300)

    def revoke_node(self, cluster: ComputeCluster,
                    node_host: str) -> Dict[str, Any]:
        """
        Revoke trust for a node.

        Maps to: vcluster revoke

        Args:
            cluster: Existing ComputeCluster.
            node_host: Node to revoke.

        Returns:
            Standard result dict.
        """
        hosts = self._build_hosts_str(cluster)
        args = [
            "revoke",
            "--hosts", hosts,
            "--revoke-hosts", node_host,
        ]

        print(f"Revoking node {node_host}...")
        return self._run_vcluster(args, timeout=60)

    def show_config(self, cluster: ComputeCluster,
                    db_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Show database configuration parameters.

        Maps to: vcluster show_config

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)
        args = [
            "show_config",
            "--db-name", db_name,
            "--hosts", hosts,
        ]

        print(f"Showing configuration for database '{db_name}'...")
        return self._run_vcluster(args, timeout=60)

    def manage_config(self, cluster: ComputeCluster,
                      db_name: Optional[str] = None,
                      action: str = "show") -> Dict[str, Any]:
        """
        Manage database configuration.

        Maps to: vcluster manage_config

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name.
            action: Config action (show, edit, etc.).

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)
        args = [
            "manage_config",
            "--db-name", db_name,
            "--hosts", hosts,
            "--action", action,
        ]

        print(f"Managing config for database '{db_name}' (action={action})...")
        return self._run_vcluster(args, timeout=60)

    def set_config_parameter(self, cluster: ComputeCluster,
                             parameter: str,
                             value: str,
                             db_name: Optional[str] = None,
                             level: str = "database") -> Dict[str, Any]:
        """
        Set a configuration parameter.

        Maps to: vcluster set_config_parameter (or SQL via vsql).

        Args:
            cluster: Existing ComputeCluster.
            parameter: Parameter name.
            value: Parameter value.
            db_name: Database name.
            level: Configuration level (database, node, session).

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        hosts = self._build_hosts_str(cluster)

        # This may require vsql if vcluster doesn't expose it directly
        args = [
            "set_config_parameter",
            "--db-name", db_name,
            "--hosts", hosts,
            "--param-name", parameter,
            "--param-value", value,
            "--level", level,
        ]

        print(f"Setting {level} parameter {parameter}={value}...")
        return self._run_vcluster(args, timeout=60)

    # ------------------------------------------------------------------
    # High-level convenience
    # ------------------------------------------------------------------

    def wait_for_database(self, cluster: ComputeCluster,
                          db_name: Optional[str] = None,
                          target_state: str = "up",
                          timeout: int = 600,
                          poll_interval: int = 10) -> Dict[str, Any]:
        """
        Poll until database reaches target state.

        Args:
            cluster: Existing ComputeCluster.
            db_name: Database name.
            target_state: Desired state (up, down).
            timeout: Maximum wait time in seconds.
            poll_interval: Seconds between polls.

        Returns:
            Standard result dict.
        """
        db_name = db_name or self.db_name
        start = time.time()

        while time.time() - start < timeout:
            result = self.database_status(cluster, db_name)
            status = (result.get("data") or {}).get("status", "unknown").lower()

            if status == target_state.lower():
                return {
                    "success": True,
                    "message": f"Database '{db_name}' reached state '{target_state}'",
                    "data": result.get("data"),
                    "error": None,
                }

            print(f"  ... database state: {status} (waiting for {target_state})")
            time.sleep(poll_interval)

        return {
            "success": False,
            "message": f"Timeout waiting for database '{db_name}' to reach '{target_state}'",
            "data": None,
            "error": "timeout",
        }

    def is_database_up(self, cluster: ComputeCluster,
                        db_name: Optional[str] = None) -> bool:
        """Quick check if database is UP."""
        result = self.database_status(cluster, db_name)
        if not result["success"]:
            return False
        status = (result.get("data") or {}).get("status", "").lower()
        return status == "up"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_node_list(self, cluster: ComputeCluster) -> List[Dict[str, Any]]:
        """Build node list structure for vcluster config generation."""
        return [
            {
                "name": instance.name,
                "ip": instance.private_ip or instance.public_ip,
                "hostname": instance.hostname,
            }
            for instance in cluster.instances
        ]

    def _generate_hosts_file(self, cluster: ComputeCluster) -> str:
        """Generate /etc/hosts content for cluster nodes."""
        lines = ["# Vertica cluster nodes"]
        for instance in cluster.instances:
            ip = instance.private_ip or instance.public_ip
            lines.append(f"{ip}    {instance.hostname} {instance.name}")
        return "\n".join(lines)

    def _generate_vcluster_config(self, cluster: ComputeCluster) -> Dict[str, Any]:
        """Generate vcluster configuration dict."""
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
                "port": self.port,
                "rest_api_port": self.rest_api_port,
            },
        }
