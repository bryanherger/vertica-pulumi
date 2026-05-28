#!/usr/bin/env python3
"""
Vertica Cluster Installation Script.

This script automates the installation of Vertica on AWS EC2 instances
after Pulumi infrastructure deployment. Handles RPM and license file distribution.

Usage:
    python scripts/install_vertica.py --config config/config.yaml --rpm-path /path/to/vertica.rpm --license-path /path/to/license.xml

Prerequisites:
    - AWS infrastructure deployed via Pulumi
    - SSH access to instances
    - Vertica RPM and license file
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


class VerticaInstaller:
    """Handles Vertica Enterprise Edition installation on cluster nodes"""
    
    def __init__(self, config: dict, ssh_key_path: str, ssh_user: str = "ec2-user"):
        """
        Initialize EE installer.
        
        Args:
            config: Configuration dictionary (must include license for EE)
            ssh_key_path: Path to SSH private key
            ssh_user: SSH username
        """
        self.config = config
        self.vertica_config = config.get('vertica', {})
        self.ssh_key_path = ssh_key_path
        self.ssh_user = ssh_user
        self.compute_config = config.get('compute', {})
        
        # Validate license is present (required for EE)
        license_config = self.vertica_config.get('license', {})
        if not license_config.get('local_path', ''):
            print("WARNING: No license file configured. Vertica 26.1+ requires a valid license for Enterprise Edition.")
        
        # Get instance IPs from Pulumi outputs or config
        self.instance_ips = self._get_instance_ips()
        
    def _get_instance_ips(self) -> List[str]:
        """Get instance IPs from Pulumi outputs"""
        try:
            # Try to get from Pulumi outputs
            result = subprocess.run(
                ["pulumi", "stack", "output", "instance_ips", "--json"],
                capture_output=True, text=True, check=True
            )
            ips = json.loads(result.stdout)
            if isinstance(ips, list) and len(ips) > 0:
                return ips
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            pass
        
        # Fallback: try to read from config (for bare metal)
        hosts = self.compute_config.get('baremetal', {}).get('hosts', [])
        if hosts:
            return [h['ip'] for h in hosts]
        
        return []
    
    def _ssh(self, ip: str, command: str, timeout: int = 300,
             sudo: bool = False) -> Tuple[int, str, str]:
        """
        Execute SSH command on remote host.
        
        Args:
            ip: Target IP address
            command: Command to execute
            timeout: Command timeout in seconds
            sudo: Whether to run with sudo
            
        Returns:
            Tuple of (returncode, stdout, stderr)
        """
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
        
        print(f"[SSH] {ip}: {command[:80]}...")
        result = subprocess.run(
            ssh_cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    
    def _scp(self, local_path: str, ip: str, remote_path: str) -> Tuple[int, str, str]:
        """
        Copy file to remote host via SCP.
        
        Args:
            local_path: Local file path
            ip: Target IP address
            remote_path: Remote destination path
            
        Returns:
            Tuple of (returncode, stdout, stderr)
        """
        scp_cmd = [
            "scp",
            "-i", self.ssh_key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            local_path,
            f"{self.ssh_user}@{ip}:{remote_path}",
        ]
        
        print(f"[SCP] {local_path} -> {ip}:{remote_path}")
        result = subprocess.run(
            scp_cmd, capture_output=True, text=True, timeout=300
        )
        return result.returncode, result.stdout, result.stderr
    
    def wait_for_instances(self, timeout: int = 300) -> bool:
        """
        Wait for all instances to be accessible via SSH.
        
        Args:
            timeout: Maximum wait time in seconds
            
        Returns:
            True if all instances are ready
        """
        print(f"Waiting for {len(self.instance_ips)} instances to be ready...")
        
        start_time = time.time()
        ready = set()
        
        while time.time() - start_time < timeout:
            for ip in self.instance_ips:
                if ip in ready:
                    continue
                    
                rc, _, _ = self._ssh(ip, "echo 'ready'", timeout=10)
                if rc == 0:
                    print(f"  ✓ {ip} is ready")
                    ready.add(ip)
            
            if len(ready) == len(self.instance_ips):
                print("All instances are ready!")
                return True
            
            time.sleep(5)
        
        print(f"Timeout: Only {len(ready)}/{len(self.instance_ips)} instances ready")
        return False
    
    def upload_file(self, local_path: str, remote_dir: str = "/tmp") -> bool:
        """
        Upload file to all instances.
        
        Args:
            local_path: Local file path
            remote_dir: Remote destination directory
            
        Returns:
            True if upload succeeded on all instances
        """
        if not os.path.exists(local_path):
            print(f"File not found: {local_path}")
            return False
        
        filename = os.path.basename(local_path)
        remote_path = f"{remote_dir}/{filename}"
        
        print(f"Uploading {filename} to {len(self.instance_ips)} instances...")
        
        for ip in self.instance_ips:
            rc, stdout, stderr = self._scp(local_path, ip, remote_path)
            if rc != 0:
                print(f"  ✗ Failed to upload to {ip}: {stderr}")
                return False
            print(f"  ✓ Uploaded to {ip}")
        
        return True
    
    def install_vertica(self, rpm_path: str) -> bool:
        """
        Install Vertica RPM on all instances.
        
        Args:
            rpm_path: Path to RPM file on remote hosts
            
        Returns:
            True if installation succeeded
        """
        print(f"Installing Vertica on {len(self.instance_ips)} instances...")
        
        remote_path = f"/tmp/{os.path.basename(rpm_path)}"
        
        for ip in self.instance_ips:
            print(f"\nInstalling on {ip}...")
            
            # Check if already installed
            rc, _, _ = self._ssh(ip, "rpm -q vertica", timeout=30)
            if rc == 0:
                print(f"  ℹ Vertica already installed on {ip}")
                continue
            
            # Install dependencies for Amazon Linux 2023
            print(f"  Installing dependencies...")
            rc, _, stderr = self._ssh(ip, "dnf install -y dialog pcre pcre2", sudo=True, timeout=120)
            if rc != 0:
                print(f"  Warning: Failed to install dependencies: {stderr}")
            
            # Install Vertica RPM
            print(f"  Installing Vertica RPM...")
            rc, stdout, stderr = self._ssh(
                ip, f"rpm -Uvh {remote_path} 2>&1 || rpm -ivh {remote_path} 2>&1",
                sudo=True, timeout=300
            )
            
            if rc != 0:
                print(f"  ✗ Installation failed on {ip}")
                print(f"    stderr: {stderr}")
                return False
            
            print(f"  ✓ Vertica installed on {ip}")
            
            # Verify installation
            rc, stdout, _ = self._ssh(
                ip, "/opt/vertica/bin/vertica --version 2>/dev/null || echo 'Not found'",
                timeout=30
            )
            print(f"  Version: {stdout.strip()}")
        
        return True
    
    def install_license(self, license_path: str) -> bool:
        """
        Install Vertica license on all nodes.
        
        Args:
            license_path: Path to license file on remote hosts
            
        Returns:
            True if license installed successfully
        """
        print(f"Installing license on {len(self.instance_ips)} instances...")
        
        remote_path = f"/tmp/{os.path.basename(license_path)}"
        license_dir = "/opt/vertica/config/licensing"
        
        for ip in self.instance_ips:
            print(f"\nInstalling license on {ip}...")
            
            # Create license directory
            rc, _, stderr = self._ssh(
                ip, f"mkdir -p {license_dir} && cp {remote_path} {license_dir}/license.xml",
                sudo=True
            )
            
            if rc != 0:
                print(f"  ✗ Failed to install license: {stderr}")
                return False
            
            # Set correct permissions
            self._ssh(ip, f"chown -R dbadmin:verticadba {license_dir} && chmod 644 {license_dir}/license.xml", sudo=True)
            
            print(f"  ✓ License installed on {ip}")
        
        return True
    
    def configure_hosts(self) -> bool:
        """
        Configure /etc/hosts on all instances for cluster communication.
        
        Returns:
            True if configuration succeeded
        """
        print("Configuring /etc/hosts for cluster communication...")
        
        # Get private IPs
        private_ips = {}
        hostnames = {}
        for ip in self.instance_ips:
            rc, stdout, _ = self._ssh(ip, "hostname -I | awk '{print $1}'", timeout=30)
            if rc == 0:
                private_ips[ip] = stdout.strip().split()[0]
            
            # Get hostname
            rc, stdout, _ = self._ssh(ip, "hostname -s", timeout=30)
            if rc == 0:
                hostnames[ip] = stdout.strip()
            else:
                hostnames[ip] = f"vertica-node-{self.instance_ips.index(ip) + 1}"
        
        # Build hosts file entries
        hosts_entries = []
        for i, (public_ip, private_ip) in enumerate(private_ips.items()):
            hostname = hostnames[public_ip]
            hosts_entries.append(f"{private_ip}    {hostname}")
        
        hosts_content = "\n".join(hosts_entries)
        print(f"  Adding host entries:\n{hosts_content}")
        
        # Update /etc/hosts on all nodes
        for ip in self.instance_ips:
            print(f"  Updating {ip}...")
            
            # Add entries to /etc/hosts
            rc, _, stderr = self._ssh(
                ip,
                f"echo '{hosts_content}' >> /etc/hosts",
                sudo=True
            )
            
            if rc != 0:
                print(f"    ✗ Failed: {stderr}")
                return False
        
        print("  ✓ Hosts configured")
        return True
    
    def configure_firewall(self) -> bool:
        """
        Configure firewall for Vertica ports.
        
        Returns:
            True if configuration succeeded
        """
        print("Configuring firewall for Vertica...")
        
        # Vertica ports
        ports = [5433, 5434, 5444, 4803, 4804, 6543, 22]
        
        for ip in self.instance_ips:
            print(f"  Configuring {ip}...")
            
            for port in ports:
                self._ssh(
                    ip,
                    f"firewall-cmd --permanent --add-port={port}/tcp 2>/dev/null || iptables -I INPUT -p tcp --dport {port} -j ACCEPT",
                    sudo=True
                )
            
            # Try to reload firewall
            self._ssh(ip, "firewall-cmd --reload 2>/dev/null || true", sudo=True)
        
        print("  ✓ Firewall configured")
        return True
    
    def create_database(self, db_name: Optional[str] = None,
                       admin_password: Optional[str] = None) -> bool:
        """
        Create Vertica database using admintools.
        
        Args:
            db_name: Database name (defaults to config)
            admin_password: Admin password (defaults to config)
            
        Returns:
            True if database created successfully
        """
        db_name = db_name or self.vertica_config.get('database', {}).get('name', 'analytics')
        admin_password = admin_password or self.vertica_config.get('database', {}).get('admin_password', '')
        
        print(f"\nCreating database '{db_name}'...")
        
        # Get primary node (first IP)
        primary_ip = self.instance_ips[0]
        
        # Get all hostnames for node list
        node_list = []
        for ip in self.instance_ips:
            rc, stdout, _ = self._ssh(ip, "hostname -s", timeout=30)
            if rc == 0:
                node_list.append(stdout.strip())
        
        if not node_list:
            print("Failed to get hostnames")
            return False
        
        print(f"Database nodes: {', '.join(node_list)}")
        
        # Create database using admintools
        # Format: admintools -t create_db -s host1,host2,host3 -d dbname -c catalog_path -D data_path
        host_list = ",".join(node_list)
        
        create_cmd = (
            f"/opt/vertica/bin/admintools -t create_db "
            f"-s {host_list} "
            f"-d {db_name} "
            f"-c /data/catalog "
            f"-D /data/vertica "
            f"-r /data/depot "
            f"-p {admin_password} "
            f"--skip-fs-checks"
        )
        
        print(f"\nDatabase creation command:")
        print(f"  {create_cmd}")
        print(f"\nTo create the database, SSH to the primary node and run:")
        print(f"  ssh -i {self.ssh_key_path} {self.ssh_user}@{primary_ip}")
        print(f"  sudo su - dbadmin")
        print(f"  {create_cmd}")
        
        # Optionally run automatically
        print(f"\nWould you like me to create the database automatically? (y/n)")
        response = input("> ").strip().lower()
        
        if response == 'y' or response == 'yes':
            print(f"Creating database on {primary_ip}...")
            rc, stdout, stderr = self._ssh(
                primary_ip,
                f"su - dbadmin -c '{create_cmd}'",
                sudo=True,
                timeout=600
            )
            
            if rc != 0:
                print(f"  ✗ Database creation failed")
                print(f"    stdout: {stdout}")
                print(f"    stderr: {stderr}")
                return False
            
            print(f"  ✓ Database '{db_name}' created successfully!")
            print(f"  stdout: {stdout}")
            
            # Start database
            print(f"\nStarting database...")
            rc, stdout, _ = self._ssh(
                primary_ip,
                f"su - dbadmin -c '/opt/vertica/bin/admintools -t db_start -d {db_name}'",
                sudo=True,
                timeout=300
            )
            
            if rc == 0:
                print(f"  ✓ Database started")
            else:
                print(f"  Warning: Could not start database automatically")
        
        return True
    
    def run_full_installation(self, rpm_path: str, license_path: Optional[str] = None,
                              auto_create_db: bool = False) -> bool:
        """
        Run full Vertica installation workflow.
        
        Args:
            rpm_path: Path to Vertica RPM file
            license_path: Path to license file (optional)
            auto_create_db: Whether to automatically create database
            
        Returns:
            True if installation completed successfully
        """
        print("=" * 70)
        print("Vertica Cluster Installation")
        print("=" * 70)
        print(f"RPM: {rpm_path}")
        if license_path:
            print(f"License: {license_path}")
        print(f"Nodes: {len(self.instance_ips)}")
        print("=" * 70)
        
        # Step 1: Wait for instances
        if not self.wait_for_instances():
            print("Instances not ready. Aborting.")
            return False
        
        # Step 2: Upload RPM
        if not self.upload_file(rpm_path):
            print("Failed to upload RPM")
            return False
        
        # Step 3: Upload license if provided
        if license_path and os.path.exists(license_path):
            if not self.upload_file(license_path):
                print("Failed to upload license")
                return False
        
        # Step 4: Install Vertica
        if not self.install_vertica(rpm_path):
            print("Installation failed")
            return False
        
        # Step 5: Install license
        if license_path and os.path.exists(license_path):
            if not self.install_license(license_path):
                print("License installation failed")
                return False
        
        # Step 6: Configure hosts
        if not self.configure_hosts():
            print("Host configuration failed")
            return False
        
        # Step 7: Run install_vertica with -Y to accept EULA and -L for license
        if license_path and os.path.exists(license_path):
            print("\n=== Running install_vertica with EULA acceptance (-Y) and license (-L) ===")
            for ip in self.instance_ips:
                print(f"Configuring {ip}...")
                rc, stdout, stderr = self._ssh(
                    ip,
                    "sudo /opt/vertica/sbin/install_vertica -Y -L /opt/vertica/config/licensing/license.xml -T || true",
                    timeout=300
                )
                if rc == 0:
                    print(f"  ✓ install_vertica completed on {ip}")
                else:
                    print(f"  ⚠ install_vertica warning on {ip}: {stderr[:100]}")
        
        # Step 8: Create database (optional)
        if auto_create_db:
            self.create_database()
        
        print("\n" + "=" * 70)
        print("Enterprise Edition Installation Complete!")
        print("=" * 70)
        print(f"\nPrimary node: {self.instance_ips[0]}")
        print(f"SSH: ssh -i {self.ssh_key_path} {self.ssh_user}@{self.instance_ips[0]}")
        print("\nNext steps:")
        if not auto_create_db:
            print("1. Create database using admintools or vcluster:")
            print(f"   ssh -i {self.ssh_key_path} {self.ssh_user}@{self.instance_ips[0]}")
            print(f"   sudo su - dbadmin")
            print(f"   /opt/vertica/bin/admintools -t create_db -s $(hostname -s) -d analytics -c /data/catalog -D /data/vertica")
        print("2. Connect using vsql:")
        print(f"   /opt/vertica/bin/vsql -U dbadmin -h {self.instance_ips[0]}")
        
        return True


def main():
    parser = argparse.ArgumentParser(description='Install Vertica on cluster')
    parser.add_argument('--config', '-c', default='config/config.yaml',
                       help='Path to configuration file')
    parser.add_argument('--rpm-path', required=True,
                       help='Local path to Vertica RPM')
    parser.add_argument('--license-path', default=None,
                       help='Local path to Vertica license file')
    parser.add_argument('--ssh-key', default='~/.ssh/pulumi.pem',
                       help='Path to SSH private key')
    parser.add_argument('--ssh-user', default='ec2-user',
                       help='SSH username')
    parser.add_argument('--auto-create-db', action='store_true',
                       help='Automatically create database after installation')
    
    args = parser.parse_args()
    
    # Check RPM exists
    rpm_path = os.path.expanduser(args.rpm_path)
    if not os.path.exists(rpm_path):
        print(f"Error: RPM not found: {rpm_path}")
        return 1
    
    # Load configuration
    config_file = os.path.expanduser(args.config)
    if not os.path.exists(config_file):
        print(f"Error: Config file not found: {config_file}")
        return 1
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    # Create installer
    installer = VerticaInstaller(
        config=config,
        ssh_key_path=os.path.expanduser(args.ssh_key),
        ssh_user=args.ssh_user,
    )
    
    if not installer.instance_ips:
        print("No instance IPs found. Deploy infrastructure first with 'pulumi up'")
        return 1
    
    # Run installation
    success = installer.run_full_installation(
        rpm_path=rpm_path,
        license_path=args.license_path,
        auto_create_db=args.auto_create_db,
    )
    
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
