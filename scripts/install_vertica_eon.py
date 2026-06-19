#!/usr/bin/env python3
"""
Vertica Eon Mode Installation Script.

Automates the installation and configuration of Vertica in Eon Mode
on AWS EC2 instances. Handles RPM distribution, communal storage setup,
certificate generation, NMA service startup, and database creation.

Usage:
    # Full automated deployment
    python scripts/install_vertica_eon.py \
        --config config/config_eon.yaml \
        --rpm-path /path/to/vertica.rpm \
        --license-path /path/to/license.xml

    # Or manual host specification
    python scripts/install_vertica_eon.py \
        --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
        --ssh-key ~/.ssh/pulumi.pem \
        --rpm-path /path/to/vertica.rpm \
        --communal-storage s3://my-bucket/analytics \
        --shard-count 3

Prerequisites:
    - AWS infrastructure deployed via Pulumi
    - SSH access to all instances
    - Vertica RPM and license file
    - S3 bucket for communal storage
"""

import argparse
import json
import os
import subprocess
import sys
import time
import yaml
from pathlib import Path
from typing import List, Optional, Tuple


class VerticaEonInstaller:
    """Handles Vertica Eon Mode installation on cluster nodes"""

    def __init__(self, config: dict, ssh_key_path: str, ssh_user: str = "ec2-user"):
        """
        Initialize Eon Mode installer.

        Args:
            config: Configuration dictionary
            ssh_key_path: Path to SSH private key
            ssh_user: SSH username
        """
        self.config = config
        self.vertica_config = config.get('vertica', {})
        self.eon_config = self.vertica_config.get('eon', {})
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self.ssh_user = ssh_user
        self.compute_config = config.get('compute', {})

        # Get instance IPs
        if config.get('_instance_ips'):
            self.instance_ips = config['_instance_ips']
        else:
            self.instance_ips = self._get_instance_ips()

        # Eon Mode settings
        self.communal_storage = self.eon_config.get('communal_storage_location', '')
        self.shard_count = self.eon_config.get('shard_count', 3)
        self.depot_path = self.eon_config.get('depot_path', '/data/depot')
        self.depot_size = self.eon_config.get('depot_size', '80%')

        # Database settings
        self.db_config = self.vertica_config.get('database', {})
        self.db_name = self.db_config.get('name', 'analytics')
        self.admin_username = self.db_config.get('admin_username', 'dbadmin')
        self.admin_password = self.db_config.get('admin_password', '')

        # Database initialization action (Create or Revive)
        self.db_init = self.eon_config.get('dbinit', 'Create').lower()
        if self.db_init not in ('create', 'revive'):
            print(f"WARNING: Invalid dbinit '{self.db_init}', defaulting to 'Create'")
            self.db_init = 'create'

        # AWS settings for S3
        self.aws_access_key = self.eon_config.get('aws_access_key_id', '')
        self.aws_secret_key = self.eon_config.get('aws_secret_access_key', '')
        self.aws_region = self.eon_config.get('aws_region', 'us-east-2')
        self.s3_endpoint = self.eon_config.get('s3_endpoint', '')
        self.aws_enable_https = self.eon_config.get('aws_enable_https', True)
        self.enable_s3_encryption = self.eon_config.get('enable_s3_encryption', True)
        self.s3_auth_mode = self.compute_config.get('aws', {}).get('s3_auth_mode', 'iam_role')

        # Certificate settings
        self.security_config = self.vertica_config.get('security', {})
        self.generate_certs = self.security_config.get('generate_nma_certs', True)

    def _get_instance_ips(self) -> List[str]:
        """Get instance IPs from Pulumi outputs or config"""
        # Try the aggregated instance_ips output first
        try:
            result = subprocess.run(
                ["pulumi", "stack", "output", "instance_ips", "--json"],
                capture_output=True, text=True, check=True
            )
            ips = json.loads(result.stdout)
            if isinstance(ips, list) and len(ips) > 0:
                return ips
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            pass

        # Fallback: try individual node_*_public_ip / node_*_private_ip outputs
        for suffix in ("public_ip", "private_ip"):
            ips = []
            node = 1
            while True:
                try:
                    result = subprocess.run(
                        ["pulumi", "stack", "output", f"node_{node}_{suffix}", "--json"],
                        capture_output=True, text=True, check=True
                    )
                    ip = json.loads(result.stdout)
                    if ip:
                        ips.append(ip)
                        node += 1
                    else:
                        break
                except (subprocess.CalledProcessError, json.JSONDecodeError):
                    break
            if ips:
                return ips

        # Fallback: try to read from config (for bare metal)
        hosts = self.compute_config.get('baremetal', {}).get('hosts', [])
        if hosts:
            return [h['ip'] for h in hosts]

        return []

    def _ssh(self, ip: str, command: str, timeout: int = 300,
             sudo: bool = False) -> Tuple[int, str, str]:
        """Execute SSH command on remote host"""
        if sudo:
            command = f"sudo {command}"

        ssh_cmd = [
            "ssh",
            "-i", self.ssh_key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            "-o", "BatchMode=yes",
            f"{self.ssh_user}@{ip}",
            command,
        ]

        try:
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -1, "", str(e)

    def _scp(self, local_path: str, remote_path: str, ip: str,
             timeout: int = 1800) -> Tuple[int, str, str]:
        """Copy file to remote host via SCP

        SECURITY NOTE: For large files (like Vertica RPM), SCP can be very slow.
        Consider using S3 as an intermediary instead of direct SCP:
        1. Upload RPM to S3: aws s3 cp local.rpm s3://bucket/
        2. Download on instances: aws s3 cp s3://bucket/local.rpm /tmp/
        This is faster and more reliable for multi-node deployments.
        """
        scp_cmd = [
            "scp",
            "-i", self.ssh_key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            "-o", "BatchMode=yes",
            local_path,
            f"{self.ssh_user}@{ip}:{remote_path}",
        ]

        try:
            # Use Popen for large files with long timeout
            process = subprocess.Popen(
                scp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate(timeout=timeout)
            return process.returncode, stdout.decode() if stdout else "", stderr.decode() if stderr else ""
        except subprocess.TimeoutExpired:
            process.kill()
            return -1, "", f"SCP timed out after {timeout} seconds"
        except Exception as e:
            return -1, "", str(e)

    def upload_rpm(self, rpm_path: str) -> bool:
        """Upload Vertica RPM to all nodes"""
        if not os.path.exists(rpm_path):
            print(f"ERROR: RPM file not found: {rpm_path}")
            return False

        rpm_name = os.path.basename(rpm_path)
        print(f"Uploading {rpm_name} to {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"  Uploading to {ip}...")
            rc, _, err = self._scp(rpm_path, f"/tmp/{rpm_name}", ip)
            if rc != 0:
                print(f"    FAILED: {err}")
                all_success = False
            else:
                print(f"    SUCCESS")

        return all_success

    def upload_license(self, license_path: str) -> bool:
        """Upload license file to all nodes"""
        if not license_path or not os.path.exists(license_path):
            print("WARNING: No license file provided. Vertica 26.1+ requires a valid license.")
            return False

        license_name = os.path.basename(license_path)
        print(f"Uploading {license_name} to {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"  Uploading to {ip}...")
            rc, _, err = self._scp(license_path, f"/tmp/{license_name}", ip)
            if rc != 0:
                print(f"    FAILED: {err}")
                all_success = False
            else:
                print(f"    SUCCESS")

        return all_success

    def _ensure_vertica_user(self, ip: str) -> bool:
        """Ensure the verticadba group and dbadmin user exist on the node."""
        cmd = (
            "getent group verticadba >/dev/null || sudo groupadd -g 10000 verticadba; "
            "getent passwd dbadmin >/dev/null || sudo useradd -u 10000 -g verticadba -m -s /bin/bash dbadmin"
        )
        rc, out, err = self._ssh(ip, cmd, timeout=30)
        if rc != 0:
            print(f"    WARNING: Could not ensure dbadmin/verticadba: {err}")
            return False
        return True

    def install_vertica(self, rpm_path: str, license_path: str = "") -> bool:
        """Install Vertica RPM on all nodes"""
        rpm_name = os.path.basename(rpm_path)
        license_name = os.path.basename(license_path) if license_path else ""

        print(f"\nInstalling Vertica on {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"\n  Installing on {ip}...")

            # Install prerequisites
            print("    Installing prerequisites (dialog, psmisc, which, net-tools)...")
            prereq_cmd = (
                "if command -v dnf >/dev/null 2>&1; then "
                "  sudo dnf install -y dialog psmisc which net-tools; "
                "elif command -v yum >/dev/null 2>&1; then "
                "  sudo yum install -y dialog psmisc which net-tools; "
                "elif command -v apt-get >/dev/null 2>&1; then "
                "  sudo apt-get install -y dialog psmisc net-tools; "
                "else "
                "  echo 'WARNING: no supported package manager found'; "
                "fi"
            )
            self._ssh(ip, prereq_cmd, sudo=False, timeout=180)

            # Try package-manager based install first (resolves dependencies)
            install_cmd = (
                "if command -v dnf >/dev/null 2>&1; then "
                f"  sudo dnf localinstall -y /tmp/{rpm_name}; "
                "elif command -v yum >/dev/null 2>&1; then "
                f"  sudo yum localinstall -y /tmp/{rpm_name}; "
                "else "
                f"  sudo rpm -ivh --nodeps /tmp/{rpm_name} || sudo rpm -Uvh --nodeps /tmp/{rpm_name}; "
                "fi"
            )
            rc, out, err = self._ssh(ip, install_cmd, sudo=False, timeout=300)
            if rc != 0:
                print(f"    ERROR: RPM install failed: {err}")
                print("    Attempting fallback install with --nodeps...")
                fallback_cmd = f"sudo rpm -ivh --nodeps /tmp/{rpm_name} || sudo rpm -Uvh --nodeps /tmp/{rpm_name}"
                rc, out, err = self._ssh(ip, fallback_cmd, sudo=False, timeout=300)
                if rc != 0:
                    print(f"    ERROR: Fallback RPM install also failed: {err}")
                    all_success = False
                    continue
                else:
                    print(f"    RPM installed successfully with --nodeps")
            else:
                print(f"    RPM installed successfully")

            # Ensure dbadmin user exists (RPM normally creates it, but fallback may not)
            self._ensure_vertica_user(ip)

            # Copy license if provided
            if license_name:
                license_cmd = (
                    f"sudo bash -c 'mkdir -p /opt/vertica/config/licensing && "
                    f"cp /tmp/{license_name} /opt/vertica/config/licensing/license.xml && "
                    f"chown -R dbadmin:verticadba /opt/vertica/config/licensing && "
                    f"chmod 644 /opt/vertica/config/licensing/license.xml'"
                )
                rc, out, err = self._ssh(ip, license_cmd, sudo=False, timeout=60)
                if rc != 0:
                    print(f"    WARNING: License install failed: {err}")
                else:
                    print(f"    License installed")

            # Fix permissions
            perm_cmd = (
                f"sudo bash -c 'chown -R dbadmin:verticadba /opt/vertica/config 2>/dev/null; "
                f"chmod 755 /opt/vertica/config 2>/dev/null; "
                f"mkdir -p /opt/vertica/log && chown dbadmin:verticadba /opt/vertica/log && chmod 755 /opt/vertica/log; "
                f"mkdir -p {self.depot_path} && "
                f"chown dbadmin:verticadba {self.depot_path} && "
                f"chmod 755 {self.depot_path}'"
            )
            rc, out, err = self._ssh(ip, perm_cmd, sudo=False, timeout=60)
            if rc != 0:
                print(f"    WARNING: Permission setup failed: {err}")
            else:
                print(f"    Permissions set")

            # Verify vcluster exists
            check_cmd = "ls -la /opt/vertica/bin/vcluster 2>/dev/null || echo 'vcluster NOT FOUND'"
            rc, out, err = self._ssh(ip, check_cmd, sudo=False, timeout=30)
            if "vcluster NOT FOUND" in out:
                print(f"    ERROR: Vertica did not install correctly (vcluster missing)")
                all_success = False
            else:
                print(f"    Verified: {out.strip()}")

        return all_success

    def generate_and_deploy_certs(self) -> bool:
        """Generate and deploy NMA certificates to all nodes"""
        if not self.generate_certs:
            print("Certificate generation disabled in config")
            return True

        print("\nGenerating and deploying NMA certificates...")

        try:
            # Import and use the certificate generator
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from generate_nma_certs import NMACertificateGenerator

            cert_config = {
                'country': self.security_config.get('cert_country', 'US'),
                'organization': self.security_config.get('cert_org', 'Vertica'),
                'common_name': self.security_config.get('cert_cn', 'vertica-nma'),
                'validity_days': self.security_config.get('cert_validity_days', 365)
            }

            generator = NMACertificateGenerator(
                hosts=self.instance_ips,
                ssh_key_path=self.ssh_key_path,
                ssh_user=self.ssh_user,
                output_dir="./certs",
                cert_config=cert_config
            )

            return generator.run()

        except ImportError as e:
            print(f"ERROR: Could not import certificate generator: {e}")
            return False
        except Exception as e:
            print(f"ERROR: Certificate generation failed: {e}")
            return False

    def start_nma_services(self) -> bool:
        """Start NMA services on all nodes"""
        print(f"\nStarting NMA services on {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"  Starting NMA on {ip}...")

            # Try to start NMA
            start_cmd = (
                f"systemctl enable vertica-nma 2>/dev/null; "
                f"systemctl start vertica-nma 2>/dev/null || "
                f"/opt/vertica/sbin/vertica-nma start 2>/dev/null || "
                f"echo 'NMA start attempted'"
            )

            rc, out, err = self._ssh(ip, start_cmd, sudo=True, timeout=60)
            if rc != 0:
                print(f"    WARNING: NMA start may have issues: {err}")
            else:
                print(f"    NMA started")

            # Wait a moment for NMA to initialize
            time.sleep(2)

            # Verify NMA is running
            check_cmd = (
                f"systemctl is-active vertica-nma 2>/dev/null || "
                f"ps aux | grep -q 'vertica-nma' && echo 'running' || echo 'not running'"
            )

            rc, out, err = self._ssh(ip, check_cmd, sudo=True, timeout=30)
            if 'running' in out.lower() or 'active' in out.lower():
                print(f"    NMA is running")
            else:
                print(f"    WARNING: NMA may not be running. Output: {out.strip()}")
                all_success = False

        return all_success

    def create_eon_database(self) -> bool:
        """Create or Revive Eon Mode database using vcluster"""
        if not self.instance_ips:
            print("ERROR: No instance IPs available")
            return False
        
        if not self.communal_storage:
            print("ERROR: Communal storage location not configured")
            return False
        
        primary_ip = self.instance_ips[0]
        hosts = ",".join(self.instance_ips)
        
        action = "Create" if self.db_init == "create" else "Revive"
        cmd_action = "create_db" if self.db_init == "create" else "revive_db"
        
        print(f"\n{action} Eon Mode database '{self.db_name}'...")
        print(f"  Primary node: {primary_ip}")
        print(f"  Hosts: {hosts}")
        print(f"  Communal storage: {self.communal_storage}")
        print(f"  Mode: {self.db_init.upper()}")
        
        if self.db_init == "create":
            print(f"  Shard count: {self.shard_count}")
        print(f"  Depot path: {self.depot_path}")
        print(f"  Depot size: {self.depot_size}")
        
        # Build vcluster command
        cmd_parts = [
            "vcluster",
            cmd_action,
            "--db-name", self.db_name,
            "--hosts", hosts,
            "--catalog-path", "/data/catalog",
            "--data-path", "/data/vertica",
            "--communal-storage-location", self.communal_storage,
            "--depot-path", self.depot_path,
            "--depot-size", self.depot_size,
        ]
        
        # Only add shard-count for create (not revive - existing count is used)
        if self.db_init == "create":
            cmd_parts.extend(["--shard-count", str(self.shard_count)])
        
        # Add password if configured (vcluster uses --password, not --username)
        if self.admin_password:
            cmd_parts.extend(["--password", self.admin_password])
        
        # Add AWS config parameters
        # IAM role (default): no AWS auth flags; vcluster uses instance metadata service.
        # Access keys: pass AWSAuth via --config-param.
        # Env vars: pass --get-aws-credentials-from-env-vars flag.
        if self.s3_auth_mode == "access_keys" and self.aws_access_key and self.aws_secret_key:
            cmd_parts.extend([
                "--config-param",
                f"AWSAuth={self.aws_access_key}:{self.aws_secret_key}"
            ])
        elif self.s3_auth_mode == "env_vars":
            cmd_parts.append("--get-aws-credentials-from-env-vars")
        # For "iam_role" (or unset), rely on instance profile / IMDS and pass nothing.
        
        cmd_parts.extend([
            "--config-param",
            f"AWSRegion={self.aws_region},AWSEneableHttps={1 if self.aws_enable_https else 0}"
        ])
        
        # Add certificate files if generated
        if self.generate_certs:
            cmd_parts.extend([
                "--cert-file", "/opt/vertica/config/share/nma_cert.pem",
                "--key-file", "/opt/vertica/config/share/nma_key.pem"
            ])
        
        # Skip package install (we already installed)
        cmd_parts.append("--skip-package-install")
        
        vcluster_cmd = " ".join(cmd_parts)

        # Vertica commands must run as the dbadmin OS user.
        # Write the command to a script and execute it with su to avoid quoting hell.
        script_content = f"""#!/bin/bash
set -e
export PATH=$PATH:/opt/vertica/bin
echo "Running as user: $(whoami), uid: $(id -u), groups: $(id -G)"
/opt/vertica/bin/{vcluster_cmd}
"""
        script_path = "/tmp/vcluster_create_db.sh"
        with open(script_path, "w") as f:
            f.write(script_content)

        print(f"\n  Executing vcluster command...")
        print(f"  Command: /opt/vertica/bin/{vcluster_cmd}")

        # Upload script to primary node
        rc, _, err = self._scp(script_path, f"{script_path}", primary_ip)
        if rc != 0:
            print(f"  ERROR: Failed to upload vcluster script: {err}")
            return False

        # Make executable and run as dbadmin
        exec_cmd = f"chmod +x {script_path} && sudo su - dbadmin -c '{script_path}'"
        rc, out, err = self._ssh(primary_ip, exec_cmd, timeout=600)
        
        print(f"\n  Output:\n{out}")
        if err:
            print(f"  Errors:\n{err}")
        
        if rc == 0:
            print(f"\n  SUCCESS: Database '{self.db_name}' {self.db_init}d successfully")
            
            # For CREATE mode, sync catalog to ensure data is persisted to communal storage
            if self.db_init == "create":
                print("\n  Syncing catalog to communal storage...")
                sync_cmd = (
                    f"su - dbadmin -c \"/opt/vertica/bin/vsql -U {self.admin_username} "
                    f"-d {self.db_name} -w '{self.admin_password}' -c 'SELECT sync_catalog();'\""
                )
                rc_sync, out_sync, err_sync = self._ssh(primary_ip, sync_cmd, timeout=120)
                if rc_sync == 0:
                    print("  Catalog synced successfully")
                    print("  IMPORTANT: Data is now persisted to S3 communal storage")
                else:
                    print(f"  WARNING: Catalog sync may have issues")
                    print(f"  Output: {out_sync}")
            
            return True
        else:
            print(f"\n  FAILED: Database {self.db_init} failed with exit code {rc}")
            return False

    def verify_database(self) -> bool:
        """Verify database is running and accessible"""
        if not self.instance_ips:
            return False

        primary_ip = self.instance_ips[0]

        print(f"\nVerifying database '{self.db_name}'...")

        # Check if database is up
        check_cmd = (
            f"su - dbadmin -c \"/opt/vertica/bin/vsql -U {self.admin_username} "
            f"-d {self.db_name} -w '{self.admin_password}' -c 'SELECT version();'\""
        )

        rc, out, err = self._ssh(primary_ip, check_cmd, timeout=60)

        if rc == 0 and "Vertica" in out:
            print(f"  Database is running!")
            print(f"  Version: {out.strip()}")

            # Check nodes
            nodes_cmd = (
                f"su - dbadmin -c \"/opt/vertica/bin/vsql -U {self.admin_username} "
                f"-d {self.db_name} -w '{self.admin_password}' -c 'SELECT * FROM nodes;'\""
            )

            rc, out, err = self._ssh(primary_ip, nodes_cmd, timeout=60)
            if rc == 0:
                print(f"\n  Nodes:\n{out}")

            return True
        else:
            print(f"  Database verification failed")
            print(f"  Output: {out}")
            print(f"  Error: {err}")
            return False

    def run(self, rpm_path: str, license_path: str = "") -> bool:
        """
        Execute full Eon Mode installation workflow.

        Args:
            rpm_path: Path to Vertica RPM file
            license_path: Path to license file (optional but recommended)

        Returns:
            True if installation succeeded
        """
        print("=" * 70)
        print("Vertica Eon Mode Installation")
        print("=" * 70)
        print(f"Nodes: {len(self.instance_ips)}")
        print(f"Database: {self.db_name}")
        print(f"Communal Storage: {self.communal_storage}")
        print(f"Action: {self.db_init.upper()}")
        if self.db_init == "create":
            print(f"Shards: {self.shard_count}")
        print(f"Depot: {self.depot_path} ({self.depot_size})")
        print("=" * 70)

        if not self.instance_ips:
            print("ERROR: No target instances found.")
            print("Deploy infrastructure first with 'pulumi up' or specify --hosts")
            return False

        # Step 1: Upload RPM
        if not self.upload_rpm(rpm_path):
            print("\nWARNING: RPM upload had issues, continuing...")

        # Step 2: Upload license
        self.upload_license(license_path)

        # Step 3: Install Vertica RPM
        if not self.install_vertica(rpm_path, license_path):
            print("\nERROR: Vertica RPM installation failed. Aborting.")
            return False

        # Step 4: Generate and deploy certificates
        if not self.generate_and_deploy_certs():
            print("\nWARNING: Certificate deployment had issues, continuing...")

        # Step 5: Start NMA services
        if not self.start_nma_services():
            print("\nWARNING: NMA service startup had issues, continuing...")

        # Step 6: Create Eon database
        if not self.create_eon_database():
            print("\nERROR: Database creation failed")
            return False

        # Step 7: Verify database
        if not self.verify_database():
            print("\nWARNING: Database verification failed")
            return False

        print("\n" + "=" * 70)
        print("Installation Complete!")
        print("=" * 70)
        print(f"Database: {self.db_name}")
        print(f"Action: {self.db_init.upper()}")
        print(f"Primary Node: {self.instance_ips[0]}")
        print(f"Communal Storage: {self.communal_storage}")
        if self.db_init == "create":
            print(f"Shards: {self.shard_count}")
        print(f"Nodes: {len(self.instance_ips)}")
        print("\nTo connect:")
        print(f"  vsql -U {self.admin_username} -d {self.db_name} -h {self.instance_ips[0]}")

        return True


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Install Vertica in Eon Mode on AWS EC2 instances"
    )
    parser.add_argument(
        "--config",
        help="Path to configuration YAML file"
    )
    parser.add_argument(
        "--hosts",
        help="Comma-separated list of host IPs (overrides config)"
    )
    parser.add_argument(
        "--ssh-key",
        help="Path to SSH private key (overrides config)"
    )
    parser.add_argument(
        "--ssh-user",
        default="ec2-user",
        help="SSH username"
    )
    parser.add_argument(
        "--rpm-path",
        help="Path to Vertica RPM file (or set vertica.rpm.local_path in config)"
    )
    parser.add_argument(
        "--license-path",
        help="Path to Vertica license file (or set vertica.license.local_path in config)"
    )
    parser.add_argument(
        "--communal-storage",
        help="S3 communal storage location (e.g., s3://bucket/path)"
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        help="Number of shards"
    )

    args = parser.parse_args()

    # Load configuration
    if args.config:
        config = load_config(args.config)
    else:
        config = {'vertica': {'eon': {}}, 'compute': {}}

    # Override with command line arguments
    if args.communal_storage:
        config['vertica']['eon']['communal_storage_location'] = args.communal_storage
    if args.shard_count:
        config['vertica']['eon']['shard_count'] = args.shard_count

    # Get SSH key
    ssh_key = args.ssh_key
    if not ssh_key:
        key_name = config.get('compute', {}).get('aws', {}).get('key_name', 'pulumi')
        ssh_key = f"~/.ssh/{key_name}.pem"

    # Get hosts
    if args.hosts:
        config['_instance_ips'] = [h.strip() for h in args.hosts.split(",")]

    installer = VerticaEonInstaller(
        config=config,
        ssh_key_path=ssh_key,
        ssh_user=args.ssh_user
    )

    # Override instance IPs if provided
    if args.hosts:
        installer.instance_ips = [h.strip() for h in args.hosts.split(",")]

    # Get RPM path from CLI or config
    rpm_path = args.rpm_path
    if not rpm_path:
        rpm_path = config.get('vertica', {}).get('rpm', {}).get('local_path', '')
        if not rpm_path:
            print("ERROR: --rpm-path not provided and vertica.rpm.local_path not set in config")
            sys.exit(1)
    rpm_path = os.path.expanduser(rpm_path)

    if not os.path.exists(rpm_path):
        print(f"ERROR: RPM file not found: {rpm_path}")
        sys.exit(1)

    # Get license path from CLI or config
    license_path = args.license_path
    if not license_path:
        license_path = config.get('vertica', {}).get('license', {}).get('local_path', '')
        if license_path:
            license_path = os.path.expanduser(license_path)

    success = installer.run(rpm_path, license_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
