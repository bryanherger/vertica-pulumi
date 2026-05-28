"""
Base compute abstraction - defines the interface that all compute providers must implement.

This allows the Vertica provisioning layer to work with any infrastructure
without knowing the details of the underlying cloud provider.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class ComputeInstance:
    """Represents a single compute instance (VM, bare metal server, etc.)"""
    id: str                          # Provider-specific ID
    name: str                        # Human-readable name
    public_ip: Optional[str]         # Public IP address (if applicable)
    private_ip: str                  # Private IP address
    hostname: str                    # DNS hostname
    ssh_user: str                    # SSH username
    ssh_key_path: Optional[str] = None  # Path to SSH private key
    ssh_port: int = 22               # SSH port
    status: str = "unknown"           # running, stopped, etc.
    tags: Dict[str, str] = None     # Metadata tags
    provider_specific: Dict[str, Any] = None  # Provider-specific data
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = {}
        if self.provider_specific is None:
            self.provider_specific = {}


@dataclass
class ComputeCluster:
    """Represents a cluster of compute instances"""
    name: str
    instances: List[ComputeInstance]
    provider: str                     # aws, azure, gcp, baremetal
    vpc_id: Optional[str] = None     # Network identifier
    subnet_id: Optional[str] = None # Subnet identifier
    tags: Dict[str, str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = {}
    
    @property
    def instance_count(self) -> int:
        return len(self.instances)
    
    @property
    def primary_instance(self) -> Optional[ComputeInstance]:
        """Returns the first instance, typically the primary/coordinator"""
        return self.instances[0] if self.instances else None
    
    def get_instance_by_name(self, name: str) -> Optional[ComputeInstance]:
        """Find an instance by its name"""
        for instance in self.instances:
            if instance.name == name:
                return instance
        return None


class ComputeProvider(ABC):
    """
    Abstract base class for compute providers.
    
    All cloud provider implementations must inherit from this class
    and implement all abstract methods.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the provider with configuration.
        
        Args:
            config: Provider-specific configuration from the user config file
        """
        self.config = config
        self.provider_name = "base"
    
    @abstractmethod
    def create_cluster(self, name: str, node_count: int, 
                      **kwargs) -> ComputeCluster:
        """
        Create a new cluster of compute instances.
        
        Args:
            name: Cluster name
            node_count: Number of instances to create
            **kwargs: Provider-specific options
            
        Returns:
            ComputeCluster object representing the created cluster
        """
        pass
    
    @abstractmethod
    def import_cluster(self, instance_ids: List[str], 
                      **kwargs) -> ComputeCluster:
        """
        Import existing compute resources into management.
        
        Args:
            instance_ids: List of provider-specific instance IDs or IP addresses
            **kwargs: Provider-specific options
            
        Returns:
            ComputeCluster object representing the imported cluster
        """
        pass
    
    @abstractmethod
    def get_instance(self, instance_id: str) -> Optional[ComputeInstance]:
        """
        Get details of a specific instance.
        
        Args:
            instance_id: Provider-specific instance identifier
            
        Returns:
            ComputeInstance if found, None otherwise
        """
        pass
    
    @abstractmethod
    def terminate_instance(self, instance_id: str) -> bool:
        """
        Terminate/delete a specific instance.
        
        Args:
            instance_id: Provider-specific instance identifier
            
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def resize_cluster(self, cluster: ComputeCluster, 
                      new_node_count: int) -> ComputeCluster:
        """
        Scale the cluster up or down.
        
        Args:
            cluster: Existing cluster to resize
            new_node_count: Desired number of nodes
            
        Returns:
            Updated ComputeCluster
        """
        pass
    
    @abstractmethod
    def get_ssh_config(self, instance: ComputeInstance) -> Dict[str, Any]:
        """
        Get SSH connection configuration for an instance.
        
        Args:
            instance: ComputeInstance to connect to
            
        Returns:
            Dictionary with host, port, user, key_path, etc.
        """
        pass
    
    def wait_for_instance(self, instance: ComputeInstance, 
                         timeout: int = 300) -> bool:
        """
        Wait for an instance to become accessible via SSH.
        
        Args:
            instance: ComputeInstance to wait for
            timeout: Maximum wait time in seconds
            
        Returns:
            True if instance is ready, False if timeout
        """
        import time
        import socket
        
        ssh_config = self.get_ssh_config(instance)
        host = ssh_config['host']
        port = ssh_config['port']
        
        print(f"Waiting for {instance.name} ({host}:{port}) to be accessible...")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
                print(f"{instance.name} is accessible!")
                return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                time.sleep(5)
        
        print(f"Timeout waiting for {instance.name}")
        return False
    
    def execute_on_instance(self, instance: ComputeInstance, 
                           command: str, timeout: int = 300) -> tuple:
        """
        Execute a command on an instance via SSH.
        
        Args:
            instance: ComputeInstance to execute on
            command: Shell command to execute
            timeout: Command timeout in seconds
            
        Returns:
            Tuple of (stdout, stderr, exit_code)
        """
        import paramiko
        
        ssh_config = self.get_ssh_config(instance)
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            pkey = None
            if ssh_config.get('key_path'):
                pkey = paramiko.RSAKey.from_private_key_file(ssh_config['key_path'])
            
            client.connect(
                hostname=ssh_config['host'],
                port=ssh_config['port'],
                username=ssh_config['user'],
                pkey=pkey,
                password=ssh_config.get('password'),
                timeout=30
            )
            
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            
            return stdout.read().decode(), stderr.read().decode(), exit_code
            
        finally:
            client.close()
    
    def upload_to_instance(self, instance: ComputeInstance,
                        local_path: str, remote_path: str) -> bool:
        """
        Upload a file to an instance via SFTP.
        
        Args:
            instance: ComputeInstance to upload to
            local_path: Local file path
            remote_path: Remote destination path
            
        Returns:
            True if successful
        """
        import paramiko
        
        ssh_config = self.get_ssh_config(instance)
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            pkey = None
            if ssh_config.get('key_path'):
                pkey = paramiko.RSAKey.from_private_key_file(ssh_config['key_path'])
            
            client.connect(
                hostname=ssh_config['host'],
                port=ssh_config['port'],
                username=ssh_config['user'],
                pkey=pkey,
                timeout=30
            )
            
            sftp = client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            return True
            
        finally:
            client.close()
