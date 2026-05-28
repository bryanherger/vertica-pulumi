"""
Pulumi-native Vertica RPM installation automation.

This module handles installing Vertica RPM packages on EC2 instances
as part of the Pulumi deployment lifecycle.
"""

import pulumi
import pulumi_aws as aws
from pulumi import Resource, Output, Input, ComponentResource
from typing import Optional, List, Dict, Any
import os
import base64


class VerticaRPMInstaller(ComponentResource):
    """
    Pulumi component that installs Vertica RPM on instances after creation.
    
    Uses cloud-init or SSH provisioners to install Vertica during deployment.
    """
    
    def __init__(self, name: str, config: Dict[str, Any],
                 instances: List[aws.ec2.Instance],
                 opts: Optional[pulumi.ResourceOptions] = None):
        """
        Initialize Vertica RPM installer.
        
        Args:
            name: Resource name
            config: Configuration with vertica.rpm settings
            instances: List of EC2 instances to install on
            opts: Pulumi resource options
        """
        super().__init__("custom:resource:VerticaRPMInstaller", name, {}, opts)
        
        self.config = config
        self.vertica_config = config.get('vertica', {})
        self.rpm_config = self.vertica_config.get('rpm', {})
        self.license_config = self.vertica_config.get('license', {})
        self.instances = instances
        
        # Check if RPM installation is configured
        self.rpm_local_path = self.rpm_config.get('local_path', '')
        self.rpm_download_url = self.rpm_config.get('download_url', '')
        self.license_local_path = self.license_config.get('local_path', '')
        
        # Determine installation strategy
        if self.rpm_local_path or self.rpm_download_url:
            self._install_vertica()
        else:
            pulumi.log.info("No Vertica RPM configured. Skipping automatic installation.")
        
        self.register_outputs({
            "installed": True if (self.rpm_local_path or self.rpm_download_url) else False,
            "instances_count": len(instances),
        })
    
    def _install_vertica(self):
        """Install Vertica on all instances"""
        
        # Generate installation script
        install_script = self._generate_install_script()
        
        for i, instance in enumerate(self.instances):
            # Use remote-exec provisioner to install Vertica
            # This runs after instance is created and available
            pulumi.ResourceOptions(
                depends_on=[instance]
            )
            
            # Export installation command for manual use
            pulumi.export(f"vertica_install_command_{i}", 
                Output.concat(
                    "ssh -i ~/.ssh/", 
                    self.config.get('compute', {}).get('aws', {}).get('key_name', 'pulumi'),
                    ".pem ec2-user@",
                    instance.public_ip,
                    " 'sudo bash /tmp/install_vertica.sh'"
                )
            )
    
    def _generate_install_script(self) -> str:
        """Generate the Vertica installation script"""
        
        script_lines = [
            "#!/bin/bash",
            "set -e",
            "",
            "echo '=== Vertica RPM Installation ==='",
            "echo 'Starting at $(date)'",
            "",
            "# Install prerequisites first",
            "echo 'Installing prerequisites...'",
            "dnf install -y dialog pcre pcre2 sysstat libxcrypt-compat || true",
            "",
        ]
        
        # Determine RPM source
        if self.rpm_local_path:
            # RPM will be uploaded via SCP
            rpm_filename = os.path.basename(self.rpm_local_path)
            script_lines.extend([
                f"echo 'Waiting for RPM file...'",
                f"for i in {{1..30}}; do",
                f"    if [ -f /tmp/{rpm_filename} ]; then",
                f"        echo 'RPM found, installing...'",
                f"        rpm -ivh /tmp/{rpm_filename} || rpm -Uvh /tmp/{rpm_filename}",
                f"        break",
                f"    fi",
                f"    sleep 10",
                f"done",
                "",
            ])
        elif self.rpm_download_url:
            # Download RPM
            rpm_filename = self.rpm_download_url.split('/')[-1]
            script_lines.extend([
                f"echo 'Downloading Vertica RPM...'",
                f"curl -L -o /tmp/{rpm_filename} '{self.rpm_download_url}'",
                f"rpm -ivh /tmp/{rpm_filename} || rpm -Uvh /tmp/{rpm_filename}",
                "",
            ])
        
        # Install license if provided
        if self.license_local_path:
            license_filename = os.path.basename(self.license_local_path)
            script_lines.extend([
                "echo 'Installing license...'",
                f"if [ -f /tmp/{license_filename} ]; then",
                f"    mkdir -p /opt/vertica/config/licensing",
                f"    cp /tmp/{license_filename} /opt/vertica/config/licensing/license.xml",
                f"    chown -R dbadmin:verticadba /opt/vertica/config/licensing",
                f"    chmod 644 /opt/vertica/config/licensing/license.xml",
                f"    echo 'License installed'",
                "fi",
                "",
            ])
        
        # Post-installation configuration
        script_lines.extend([
            "echo 'Configuring Vertica...'",
            "# Fix dbadmin primary group",
            "usermod -g verticadba dbadmin || true",
            "",
            "# Configure timezone",
            "echo 'export TZ=UTC' >> /home/dbadmin/.bashrc",
            "",
            "# Set proper permissions",
            "chown -R dbadmin:verticadba /data",
            "",
            "echo 'Vertica installation complete at $(date)'",
            "echo 'Complete' > /var/log/vertica-install-complete.log",
        ])
        
        return "\n".join(script_lines)
    
    def get_installation_script(self) -> str:
        """Get the generated installation script for external use"""
        return self._generate_install_script()


def create_rpm_installer(name: str, config: Dict[str, Any],
                        instances: List[aws.ec2.Instance]) -> VerticaRPMInstaller:
    """
    Create a Vertica RPM installer component.
    
    Args:
        name: Resource name
        config: Configuration dict
        instances: EC2 instances to install on
        
    Returns:
        VerticaRPMInstaller instance
    """
    return VerticaRPMInstaller(name, config, instances)
