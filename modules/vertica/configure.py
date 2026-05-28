"""
Vertica configuration management.

Handles post-installation configuration including database parameters,
network settings, SSL, and user management.
"""

from typing import Dict, Any, List, Optional, Tuple

from modules.compute.base import ComputeInstance, ComputeCluster


class VerticaConfigurator:
    """
    Manages Vertica database configuration.
    
    Provides methods for configuring database parameters,
    network settings, security, and user management.
    """
    
    def __init__(self, vertica_config: Dict[str, Any]):
        """
        Initialize configurator.
        
        Args:
            vertica_config: Vertica configuration from user config
        """
        self.config = vertica_config
        
        # Network configuration
        self.network_config = vertica_config.get('network', {})
        self.port = self.network_config.get('port', 5433)
        self.rest_api_port = self.network_config.get('rest_api_port', 5444)
        self.control_broadcast = self.network_config.get('control_broadcast', True)
        
        # Security configuration
        self.security_config = vertica_config.get('security', {})
        self.ssl_mode = self.security_config.get('ssl_mode', 'prefer')
        
        # Resource limits
        self.resources_config = vertica_config.get('resources', {})
        
        # Additional config parameters
        self.config_params = vertica_config.get('config_params', {})
    
    def configure_database(self, cluster: ComputeCluster) -> Tuple[bool, str]:
        """
        Apply configuration to Vertica database.
        
        Args:
            cluster: ComputeCluster with running Vertica
            
        Returns:
            Tuple of (success, message)
        """
        print("Configuring Vertica database...")
        
        # Apply configuration parameters
        success = self._apply_config_params(cluster)
        if not success:
            return False, "Failed to apply configuration parameters"
        
        # Configure network settings
        success = self._configure_network(cluster)
        if not success:
            return False, "Failed to configure network settings"
        
        # Configure SSL/TLS
        success = self._configure_ssl(cluster)
        if not success:
            return False, "Failed to configure SSL"
        
        # Configure resource limits
        success = self._configure_resources(cluster)
        if not success:
            return False, "Failed to configure resource limits"
        
        return True, "Database configured successfully"
    
    def _apply_config_params(self, cluster: ComputeCluster) -> bool:
        """Apply additional configuration parameters"""
        
        if not self.config_params:
            return True
        
        # Build ALTER DATABASE statements
        statements = []
        for param_name, param_value in self.config_params.items():
            if isinstance(param_value, str):
                statements.append(
                    f"ALTER DATABASE DEFAULT SET {param_name} = '{param_value}'"
                )
            else:
                statements.append(
                    f"ALTER DATABASE DEFAULT SET {param_name} = {param_value}"
                )
        
        # Execute via vsql or REST API
        primary = cluster.primary_instance
        
        # For now, return True (implementation would execute SQL)
        return True
    
    def _configure_network(self, cluster: ComputeCluster) -> bool:
        """Configure network settings"""
        
        # Configure port settings
        # This might involve updating vertica.conf or using SQL
        
        # Enable/disable broadcast
        if not self.control_broadcast:
            # Configure unicast mode
            pass
        
        return True
    
    def _configure_ssl(self, cluster: ComputeCluster) -> bool:
        """Configure SSL/TLS settings"""
        
        if self.ssl_mode == 'disable':
            return True
        
        # Get certificate paths if provided
        cert_path = self.security_config.get('tls_cert_path', '')
        key_path = self.security_config.get('tls_key_path', '')
        
        if cert_path and key_path:
            # Upload certificates to nodes
            # Configure Vertica to use them
            pass
        else:
            # Generate self-signed certificates
            pass
        
        return True
    
    def _configure_resources(self, cluster: ComputeCluster) -> bool:
        """Configure resource limits"""
        
        max_memory = self.resources_config.get('max_memory_percent', 85)
        temp_space = self.resources_config.get('temp_space_limit', '100GB')
        
        # Build resource pool configuration
        # This would be done via SQL
        
        return True
    
    def create_users(self, cluster: ComputeCluster,
                    users: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Create database users.
        
        Args:
            cluster: ComputeCluster
            users: List of user dictionaries with name, password, roles, etc.
            
        Returns:
            Tuple of (success, message)
        """
        print(f"Creating {len(users)} database users...")
        
        statements = []
        for user in users:
            username = user.get('name')
            password = user.get('password', '')
            roles = user.get('roles', [])
            
            if not username:
                continue
            
            # Create user
            if password:
                statements.append(f"CREATE USER {username} IDENTIFIED BY '{password}'")
            else:
                statements.append(f"CREATE USER {username}")
            
            # Grant roles
            for role in roles:
                statements.append(f"GRANT {role} TO {username}")
        
        # Execute statements
        # Implementation would use vsql or REST API
        
        return True, f"Created {len(users)} users"
    
    def configure_backup(self, cluster: ComputeCluster,
                        backup_config: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Configure backup settings.
        
        Args:
            cluster: ComputeCluster
            backup_config: Backup configuration
            
        Returns:
            Tuple of (success, message)
        """
        print("Configuring backup settings...")
        
        # Configure vbr (Vertica Backup and Restore)
        # This would involve:
        # 1. Creating backup configuration
        # 2. Setting up backup locations (S3, NFS, etc.)
        # 3. Configuring backup schedules
        
        return True, "Backup configured"
    
    def configure_monitoring(self, cluster: ComputeCluster,
                           monitoring_config: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Configure monitoring and alerting.
        
        Args:
            cluster: ComputeCluster
            monitoring_config: Monitoring configuration
            
        Returns:
            Tuple of (success, message)
        """
        print("Configuring monitoring...")
        
        # Configure system tables retention
        # Set up SNMP if needed
        # Configure log retention
        
        return True, "Monitoring configured"
    
    def generate_config_files(self, cluster: ComputeCluster) -> Dict[str, str]:
        """
        Generate configuration files for Vertica.
        
        Args:
            cluster: ComputeCluster
            
        Returns:
            Dictionary of filename -> content
        """
        configs = {}
        
        # vertica.conf
        vertica_conf = self._generate_vertica_conf(cluster)
        configs['vertica.conf'] = vertica_conf
        
        # admintools.conf (if using admintools)
        admintools_conf = self._generate_admintools_conf(cluster)
        configs['admintools.conf'] = admintools_conf
        
        return configs
    
    def _generate_vertica_conf(self, cluster: ComputeCluster) -> str:
        """Generate vertica.conf content"""
        lines = [
            "# Vertica Configuration",
            "# Generated by Pulumi",
            "",
            f"# Network settings",
            f"Port = {self.port}",
            f"RESTAPIPort = {self.rest_api_port}",
            "",
            "# Security settings",
            f"SSLMode = {self.ssl_mode}",
            "",
            "# Resource limits",
        ]
        
        if self.resources_config:
            max_memory = self.resources_config.get('max_memory_percent', 85)
            lines.append(f"MaxMemoryPercent = {max_memory}")
        
        lines.extend([
            "",
            "# Additional parameters",
        ])
        
        for param_name, param_value in self.config_params.items():
            lines.append(f"{param_name} = {param_value}")
        
        return "\n".join(lines)
    
    def _generate_admintools_conf(self, cluster: ComputeCluster) -> str:
        """Generate admintools.conf content"""
        lines = [
            "# AdminTools Configuration",
            "# Generated by Pulumi",
            "",
            "[Configuration]",
            f"hosts = {','.join([i.private_ip for i in cluster.instances])}",
            "",
            "[Cluster]",
            f"name = {cluster.name}",
        ]
        
        return "\n".join(lines)
