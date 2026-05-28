"""
Vertica REST API client for cluster management.

Provides Python interface to Vertica's REST API for
monitoring and administrative operations.
"""

import requests
import json
from typing import Dict, Any, Optional, List
from urllib.parse import urljoin


class VerticaRestApi:
    """
    Client for Vertica's REST API.
    
    Used for monitoring, status checks, and some administrative
    operations that can be done via HTTP.
    """
    
    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = False):
        """
        Initialize REST API client.
        
        Args:
            base_url: Base URL of Vertica REST API (e.g., https://host:5444)
            username: Admin username
            password: Admin password
            verify_ssl: Whether to verify SSL certificates
        """
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        
        self.session = requests.Session()
        self.session.verify = verify_ssl
        
        # Authenticate
        self._authenticate()
    
    def _authenticate(self):
        """Authenticate with the Vertica REST API"""
        auth_url = urljoin(self.base_url, '/v1/authenticate')
        
        response = self.session.post(
            auth_url,
            json={
                'username': self.username,
                'password': self.password,
            }
        )
        
        if response.status_code == 200:
            # Store session cookie or token
            if 'Set-Cookie' in response.headers:
                self.session.headers.update({
                    'Cookie': response.headers['Set-Cookie']
                })
        else:
            raise ConnectionError(
                f"Authentication failed: {response.status_code} - {response.text}"
            )
    
    def get_cluster_status(self) -> Dict[str, Any]:
        """
        Get cluster status via REST API.
        
        Returns:
            Dictionary with cluster status
        """
        url = urljoin(self.base_url, '/v1/cluster')
        
        response = self.session.get(url)
        response.raise_for_status()
        
        return response.json()
    
    def get_nodes_status(self) -> List[Dict[str, Any]]:
        """
        Get status of all nodes in the cluster.
        
        Returns:
            List of node status dictionaries
        """
        url = urljoin(self.base_url, '/v1/nodes')
        
        response = self.session.get(url)
        response.raise_for_status()
        
        data = response.json()
        return data.get('nodes', [])
    
    def get_database_status(self) -> Dict[str, Any]:
        """
        Get database status.
        
        Returns:
            Dictionary with database status
        """
        url = urljoin(self.base_url, '/v1/databases')
        
        response = self.session.get(url)
        response.raise_for_status()
        
        return response.json()
    
    def get_resource_usage(self) -> Dict[str, Any]:
        """
        Get resource usage statistics.
        
        Returns:
            Dictionary with CPU, memory, disk usage
        """
        url = urljoin(self.base_url, '/v1/system/resource_usage')
        
        response = self.session.get(url)
        response.raise_for_status()
        
        return response.json()
    
    def get_sessions(self) -> List[Dict[str, Any]]:
        """
        Get active sessions.
        
        Returns:
            List of session dictionaries
        """
        url = urljoin(self.base_url, '/v1/sessions')
        
        response = self.session.get(url)
        response.raise_for_status()
        
        data = response.json()
        return data.get('sessions', [])
    
    def get_queries(self, running_only: bool = True) -> List[Dict[str, Any]]:
        """
        Get query information.
        
        Args:
            running_only: Only return running queries
            
        Returns:
            List of query dictionaries
        """
        url = urljoin(self.base_url, '/v1/queries')
        
        params = {}
        if running_only:
            params['status'] = 'running'
        
        response = self.session.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        return data.get('queries', [])
    
    def reload_config(self) -> bool:
        """
        Reload configuration files.
        
        Returns:
            True if successful
        """
        url = urljoin(self.base_url, '/v1/system/reload_config')
        
        response = self.session.post(url)
        return response.status_code == 200
    
    def close(self):
        """Close the HTTP session"""
        self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
