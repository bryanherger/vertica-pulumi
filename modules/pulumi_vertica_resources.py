"""
Pulumi Dynamic Resource Providers for Vertica lifecycle management.

Provides custom Pulumi resources that integrate infrastructure provisioning
with vcluster operations for full database lifecycle automation:

  - VerticaDatabase: create/revive/start/stop/drop lifecycle
  - VerticaNodePool: add/remove/scale nodes
  - VerticaSubcluster: add/remove/rename subclusters
  - VerticaClusterStatus: read-only status monitoring

Example usage:
    db = VerticaDatabase("my-db",
        cluster=cluster,
        db_name="analytics",
        node_count=3,
        eon_mode=True,
    )
"""

from typing import Optional, Sequence
import json
import subprocess

import pulumi
from pulumi import Input, Output, ResourceOptions
from pulumi.dynamic import Resource, ResourceProvider, CreateResult, UpdateResult, DeleteResult, DiffResult, CheckResult

from modules.compute.base import ComputeCluster, ComputeInstance
from modules.vertica.vcluster import VClusterManager


class _VerticaDatabaseProvider(ResourceProvider):
    """Dynamic provider for VerticaDatabase Pulumi resource."""

    def _run(self, props: dict, cmd: list, timeout: int = 300) -> dict:
        """Helper to run vcluster commands during Pulumi operations."""
        vcluster_path = props.get("vcluster_path", "vcluster")
        full_cmd = [vcluster_path, "--json"] + cmd
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr or result.stdout}
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check(self, olds: dict, news: dict) -> CheckResult:
        """Validate inputs before create/update."""
        required = ["db_name", "hosts"]
        failures = []
        for field in required:
            if not news.get(field):
                failures.append(pulumi.ResourceError(f"Missing required property: {field}"))
        return CheckResult(inputs=news, failures=failures)

    def create(self, props: dict) -> CreateResult:
        """Create a new Vertica database."""
        db_name = props["db_name"]
        hosts = props["hosts"]
        eon_mode = props.get("eon_mode", False)
        shard_count = props.get("shard_count", 6)

        args = [
            "create_db",
            "--db-name", db_name,
            "--hosts", hosts,
        ]
        if eon_mode:
            args.append("--eon-mode")
            if shard_count:
                args.extend(["--shard-count", str(shard_count)])
        if props.get("data_path"):
            args.extend(["--data-path", props["data_path"]])
        if props.get("catalog_path"):
            args.extend(["--catalog-path", props["catalog_path"]])
        if props.get("db_user"):
            args.extend(["--db-user", props["db_user"]])

        result = self._run(props, args, timeout=600)
        if not result["success"]:
            raise Exception(f"Failed to create database: {result.get('error')}")

        outs = dict(props)
        outs["status"] = "creating"
        outs["db_id"] = result["data"].get("db_id", "unknown")

        return CreateResult(id_=f"{db_name}@{hosts}", outs=outs)

    def diff(self, id: str, olds: dict, news: dict) -> DiffResult:
        """Detect changes that require update/replace."""
        changes = []
        replaces = []

        if olds.get("db_name") != news.get("db_name"):
            replaces.append("db_name")
        if olds.get("hosts") != news.get("hosts"):
            changes.append("hosts")
        if olds.get("eon_mode") != news.get("eon_mode"):
            replaces.append("eon_mode")
        if olds.get("shard_count") != news.get("shard_count"):
            changes.append("shard_count")

        return DiffResult(
            changes=bool(changes or replaces),
            replaces=replaces,
            delete_before_replace=bool(replaces),
        )

    def update(self, id: str, olds: dict, news: dict) -> UpdateResult:
        """Update database properties (scale, etc.)."""
        # For now, most updates require recreation
        # In future: add_node, remove_node, etc.
        outs = dict(news)
        outs["status"] = olds.get("status", "unknown")
        outs["db_id"] = olds.get("db_id", "unknown")
        return UpdateResult(outs=outs)

    def delete(self, id: str, props: dict) -> DeleteResult:
        """Delete (drop) the database."""
        db_name = props["db_name"]
        hosts = props["hosts"]
        force = props.get("force_drop", False)

        args = ["drop_db", "--db-name", db_name, "--hosts", hosts]
        if force:
            args.append("--force")

        result = self._run(props, args, timeout=300)
        if not result["success"]:
            # Log warning but don't fail Pulumi on cleanup
            print(f"Warning: Failed to drop database {db_name}: {result.get('error')}")

        return DeleteResult()


class VerticaDatabase(Resource):
    """
    Pulumi Resource representing a Vertica database lifecycle.

    Args:
        resource_name: Pulumi resource name.
        props:
            - db_name: Name of the database.
            - hosts: Comma-separated list of node hosts.
            - eon_mode: Whether to create in Eon Mode.
            - shard_count: Number of shards (Eon Mode).
            - data_path: Data directory path.
            - catalog_path: Catalog directory path.
            - db_user: Admin username.
            - vcluster_path: Path to vcluster binary.
        opts: Pulumi ResourceOptions.

    Outputs:
        - db_id: Database identifier.
        - status: Current database status.
        - db_name: Database name.
        - hosts: Node hosts.
    """

    db_id: Output[str]
    status: Output[str]
    db_name: Output[str]
    hosts: Output[str]

    def __init__(self, resource_name: str,
                 props: Optional[dict] = None,
                 opts: Optional[ResourceOptions] = None):
        props = props or {}
        super().__init__(_VerticaDatabaseProvider(), resource_name, props, opts)


class _VerticaNodePoolProvider(ResourceProvider):
    """Dynamic provider for VerticaNodePool Pulumi resource."""

    def _run(self, props: dict, cmd: list, timeout: int = 300) -> dict:
        vcluster_path = props.get("vcluster_path", "vcluster")
        full_cmd = [vcluster_path, "--json"] + cmd
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr or result.stdout}
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create(self, props: dict) -> CreateResult:
        """Create node pool by adding nodes to database."""
        db_name = props["db_name"]
        hosts = props["hosts"]
        new_hosts = props.get("new_hosts", "")

        if new_hosts:
            args = [
                "add_node",
                "--db-name", db_name,
                "--hosts", hosts,
                "--new-hosts", new_hosts,
            ]
            if props.get("subcluster"):
                args.extend(["--subcluster", props["subcluster"]])

            result = self._run(props, args, timeout=600)
            if not result["success"]:
                raise Exception(f"Failed to add nodes: {result.get('error')}")

        outs = dict(props)
        outs["node_count"] = len(hosts.split(",")) + len(new_hosts.split(",")) if new_hosts else len(hosts.split(","))
        return CreateResult(id_=f"nodepool-{db_name}", outs=outs)

    def diff(self, id: str, olds: dict, news: dict) -> DiffResult:
        changes = []
        old_hosts = set(olds.get("hosts", "").split(","))
        new_hosts = set(news.get("hosts", "").split(","))

        if old_hosts != new_hosts:
            changes.append("hosts")
        if olds.get("subcluster") != news.get("subcluster"):
            changes.append("subcluster")

        return DiffResult(changes=bool(changes), replaces=[], delete_before_replace=False)

    def update(self, id: str, olds: dict, news: dict) -> UpdateResult:
        # Handle add/remove nodes
        old_hosts = set(olds.get("hosts", "").split(","))
        new_hosts = set(news.get("hosts", "").split(","))

        added = new_hosts - old_hosts
        removed = old_hosts - new_hosts

        # Add new nodes
        if added:
            args = [
                "add_node",
                "--db-name", news["db_name"],
                "--hosts", news["hosts"],
                "--new-hosts", ",".join(added),
            ]
            self._run(news, args, timeout=600)

        # Remove old nodes
        if removed:
            for host in removed:
                args = [
                    "remove_node",
                    "--db-name", news["db_name"],
                    "--hosts", news["hosts"],
                    "--remove-hosts", host,
                ]
                self._run(news, args, timeout=600)

        outs = dict(news)
        outs["node_count"] = len(new_hosts)
        return UpdateResult(outs=outs)

    def delete(self, id: str, props: dict) -> DeleteResult:
        """Remove all nodes from the pool."""
        db_name = props["db_name"]
        hosts = props.get("hosts", "")
        node_hosts = hosts.split(",") if hosts else []

        for host in node_hosts:
            args = [
                "remove_node",
                "--db-name", db_name,
                "--hosts", hosts,
                "--remove-hosts", host,
            ]
            self._run(props, args, timeout=600)

        return DeleteResult()


class VerticaNodePool(Resource):
    """
    Pulumi Resource representing a pool of Vertica nodes.

    Args:
        resource_name: Pulumi resource name.
        props:
            - db_name: Database name.
            - hosts: Current node hosts (comma-separated).
            - new_hosts: Additional hosts to add (comma-separated).
            - subcluster: Subcluster to add nodes to.
            - vcluster_path: Path to vcluster binary.
        opts: Pulumi ResourceOptions.

    Outputs:
        - node_count: Total number of nodes.
    """

    node_count: Output[int]

    def __init__(self, resource_name: str,
                 props: Optional[dict] = None,
                 opts: Optional[ResourceOptions] = None):
        props = props or {}
        super().__init__(_VerticaNodePoolProvider(), resource_name, props, opts)


class _VerticaSubclusterProvider(ResourceProvider):
    """Dynamic provider for VerticaSubcluster Pulumi resource."""

    def _run(self, props: dict, cmd: list, timeout: int = 300) -> dict:
        vcluster_path = props.get("vcluster_path", "vcluster")
        full_cmd = [vcluster_path, "--json"] + cmd
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr or result.stdout}
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create(self, props: dict) -> CreateResult:
        db_name = props["db_name"]
        hosts = props["hosts"]
        subcluster_name = props["subcluster_name"]
        sc_hosts = props.get("sc_hosts", "")

        args = [
            "add_subcluster",
            "--db-name", db_name,
            "--hosts", hosts,
            "--subcluster", subcluster_name,
        ]
        if sc_hosts:
            args.extend(["--sc-hosts", sc_hosts])

        result = self._run(props, args, timeout=600)
        if not result["success"]:
            raise Exception(f"Failed to add subcluster: {result.get('error')}")

        outs = dict(props)
        outs["status"] = "active"
        return CreateResult(id_=f"subcluster-{subcluster_name}", outs=outs)

    def diff(self, id: str, olds: dict, news: dict) -> DiffResult:
        changes = []
        replaces = []

        if olds.get("subcluster_name") != news.get("subcluster_name"):
            replaces.append("subcluster_name")
        if olds.get("sc_hosts") != news.get("sc_hosts"):
            changes.append("sc_hosts")

        return DiffResult(
            changes=bool(changes),
            replaces=replaces,
            delete_before_replace=bool(replaces),
        )

    def update(self, id: str, olds: dict, news: dict) -> UpdateResult:
        outs = dict(news)
        outs["status"] = olds.get("status", "active")
        return UpdateResult(outs=outs)

    def delete(self, id: str, props: dict) -> DeleteResult:
        db_name = props["db_name"]
        hosts = props["hosts"]
        subcluster_name = props["subcluster_name"]

        args = [
            "remove_subcluster",
            "--db-name", db_name,
            "--hosts", hosts,
            "--subcluster", subcluster_name,
        ]
        self._run(props, args, timeout=600)
        return DeleteResult()


class VerticaSubcluster(Resource):
    """
    Pulumi Resource representing a Vertica subcluster.

    Args:
        resource_name: Pulumi resource name.
        props:
            - db_name: Database name.
            - hosts: Node hosts (comma-separated).
            - subcluster_name: Name of the subcluster.
            - sc_hosts: Hosts for the subcluster (comma-separated).
            - vcluster_path: Path to vcluster binary.
        opts: Pulumi ResourceOptions.

    Outputs:
        - status: Subcluster status.
        - subcluster_name: Subcluster name.
    """

    status: Output[str]
    subcluster_name: Output[str]

    def __init__(self, resource_name: str,
                 props: Optional[dict] = None,
                 opts: Optional[ResourceOptions] = None):
        props = props or {}
        super().__init__(_VerticaSubclusterProvider(), resource_name, props, opts)
