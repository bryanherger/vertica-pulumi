"""
Vertica installation scripts and utilities.

Handles downloading, installing, and initial setup of Vertica
on target nodes.
"""

import os
import platform
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

from modules.compute.base import ComputeInstance


class VerticaInstaller:
    """
    Handles Vertica installation on target systems.
    
    Supports RPM-based (RHEL/CentOS/Amazon Linux) and 
    DEB-based (Ubuntu/Debian) systems.
    """
    
    def __init__(self, vertica_config: Dict[str, Any]):
        """
        Initialize installer.
        
        Args:
            vertica_config: Vertica configuration from user config
        """
        self.config = vertica_config
        self.version = vertica_config.get('version', '24.1.0-1')
        
    def get_download_url(self, version: Optional[str] = None) -> str:
        """
        Get download URL for Vertica installer.
        
        Args:
            version: Specific version (defaults to config)
            
        Returns:
            Download URL string
        """
        version = version or self.version
        
        # This would be customized based on your Vertica distribution source
        # Examples:
        # - Direct from Vertica (requires login)
        # - Internal repository
        # - S3 bucket
        
        # Placeholder - replace with actual URL pattern
        base_url = "https://example.com/vertica-downloads"
        
        # Detect architecture
        arch = platform.machine()
        if arch == 'x86_64':
            arch_suffix = 'x86_64'
        else:
            arch_suffix = arch
        
        # Build URL (example pattern)
        url = f"{base_url}/vertica-{version}.{arch_suffix}.rpm"
        
        return url
    
    def download_installer(self, url: str, 
                         destination: Optional[str] = None) -> str:
        """
        Download Vertica installer to local path.
        
        Args:
            url: Download URL
            destination: Local path (defaults to /tmp)
            
        Returns:
            Path to downloaded file
        """
        import urllib.request
        
        if destination is None:
            destination = f"/tmp/vertica-{self.version}.rpm"
        
        print(f"Downloading Vertica from {url}...")
        
        # Create request with headers
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Vertica-Pulumi-Installer/1.0')
        
        with urllib.request.urlopen(req) as response:
            with open(destination, 'wb') as f:
                f.write(response.read())
        
        print(f"Downloaded to {destination}")
        return destination
    
    def install_on_node(self, instance: ComputeInstance,
                       installer_path: str,
                       ssh_config: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        Install Vertica on a remote node.
        
        Args:
            instance: Target ComputeInstance
            installer_path: Local path to installer package
            ssh_config: SSH configuration (auto-detected if None)
            
        Returns:
            Tuple of (success, message)
        """
        if ssh_config is None and hasattr(instance, 'provider'):
            ssh_config = instance.provider.get_ssh_config(instance)
        
        # Upload installer
        remote_path = f"/tmp/{Path(installer_path).name}"
        
        print(f"Uploading installer to {instance.name}...")
        if hasattr(instance, 'provider'):
            instance.provider.upload_to_instance(instance, installer_path, remote_path)
        
        # Determine package type and install
        if installer_path.endswith('.rpm'):
            return self._install_rpm(instance, remote_path)
        elif installer_path.endswith('.deb'):
            return self._install_deb(instance, remote_path)
        else:
            return False, f"Unknown package format: {installer_path}"
    
    def _install_rpm(self, instance: ComputeInstance, 
                    remote_path: str) -> Tuple[bool, str]:
        """Install RPM package on RHEL/CentOS/Amazon Linux"""
        
        commands = [
            # Install dependencies
            "dnf install -y dialog glibc-headers",
            "",
            # Install Vertica
            f"rpm -Uvh {remote_path}",
            "",
            # Verify installation
            "/opt/vertica/bin/vertica --version",
        ]
        
        command = "; ".join(commands)
        
        if hasattr(instance, 'provider'):
            stdout, stderr, exit_code = instance.provider.execute_on_instance(
                instance, command, timeout=300
            )
            
            if exit_code == 0:
                return True, f"Vertica installed: {stdout.strip()}"
            else:
                return False, f"Installation failed: {stderr}"
        else:
            return False, "No provider available for remote execution"
    
    def _install_deb(self, instance: ComputeInstance,
                    remote_path: str) -> Tuple[bool, str]:
        """Install DEB package on Ubuntu/Debian"""
        
        commands = [
            # Install dependencies
            "apt-get update",
            "apt-get install -y dialog",
            "",
            # Install Vertica
            f"dpkg -i {remote_path}",
            "apt-get install -f -y",  # Fix dependencies
            "",
            # Verify installation
            "/opt/vertica/bin/vertica --version",
        ]
        
        command = "; ".join(commands)
        
        if hasattr(instance, 'provider'):
            stdout, stderr, exit_code = instance.provider.execute_on_instance(
                instance, command, timeout=300
            )
            
            if exit_code == 0:
                return True, f"Vertica installed: {stdout.strip()}"
            else:
                return False, f"Installation failed: {stderr}"
        else:
            return False, "No provider available for remote execution"
    
    def verify_installation(self, instance: ComputeInstance) -> bool:
        """
        Verify Vertica is properly installed on a node.
        
        Args:
            instance: ComputeInstance to check
            
        Returns:
            True if Vertica is installed and accessible
        """
        command = "/opt/vertica/bin/vertica --version"
        
        if hasattr(instance, 'provider'):
            stdout, stderr, exit_code = instance.provider.execute_on_instance(
                instance, command, timeout=30
            )
            return exit_code == 0
        
        return False
    
    def setup_data_directories(self, instance: ComputeInstance) -> Tuple[bool, str]:
        """
        Create and configure Vertica data directories.
        
        Args:
            instance: ComputeInstance to configure
            
        Returns:
            Tuple of (success, message)
        """
        data_path = self.config.get('nodes', {}).get('data_path', '/data/vertica')
        catalog_path = self.config.get('nodes', {}).get('catalog_path', '/data/catalog')
        
        commands = [
            # Create directories
            f"mkdir -p {data_path} {catalog_path}",
            "",
            # Set ownership (Vertica typically runs as dbadmin)
            f"chown -R dbadmin:verticadba {data_path} {catalog_path}",
            f"chmod 755 {data_path} {catalog_path}",
            "",
            # Set appropriate permissions
            f"chmod 700 {data_path}/* 2>/dev/null || true",
        ]
        
        command = "; ".join(commands)
        
        if hasattr(instance, 'provider'):
            stdout, stderr, exit_code = instance.provider.execute_on_instance(
                instance, command, timeout=60
            )
            
            if exit_code == 0:
                return True, "Data directories configured"
            else:
                return False, f"Failed to configure directories: {stderr}"
        
        return False, "No provider available"
    
    def generate_bootstrap_script(self) -> str:
        """
        Generate a bootstrap script for cloud-init.
        
        Returns:
            Shell script string for cloud-init user-data
        """
        script = """#!/bin/bash
set -e

# System configuration for Vertica
echo "Configuring system for Vertica..."

# Kernel parameters
cat >> /etc/sysctl.conf << 'EOF'
vm.max_map_count=262144
vm.swappiness=1
EOF
sysctl -p

# User limits
cat >> /etc/security/limits.conf << 'EOF'
vertica soft nofile 65536
vertica hard nofile 65536
vertica soft nproc 65536
vertica hard nproc 65536
vertica soft core unlimited
vertica hard core unlimited
EOF

# Create vertica user
useradd -m -s /bin/bash vertica 2>/dev/null || true

# Create data directories
mkdir -p /data/vertica /data/catalog
chown -R vertica:vertica /data

# Install dependencies
dnf install -y wget curl net-tools dialog

echo "Bootstrap complete"
"""
        return script
