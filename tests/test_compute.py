import unittest
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.compute.base import ComputeInstance, ComputeCluster, ComputeProvider
from modules.compute.aws import AWSComputeProvider
from modules.compute.baremetal import BareMetalProvider


class TestComputeInstance(unittest.TestCase):
    """Test ComputeInstance dataclass"""
    
    def test_basic_creation(self):
        instance = ComputeInstance(
            id="i-12345",
            name="test-node",
            public_ip="1.2.3.4",
            private_ip="10.0.1.5",
            hostname="test-node.internal",
            ssh_user="ec2-user",
        )
        
        self.assertEqual(instance.id, "i-12345")
        self.assertEqual(instance.name, "test-node")
        self.assertEqual(instance.ssh_port, 22)  # Default
        self.assertEqual(instance.status, "unknown")  # Default
    
    def test_empty_tags(self):
        instance = ComputeInstance(
            id="i-12345",
            name="test-node",
            public_ip="1.2.3.4",
            private_ip="10.0.1.5",
            hostname="test-node.internal",
            ssh_user="ec2-user",
        )
        
        self.assertIsNotNone(instance.tags)
        self.assertIsInstance(instance.tags, dict)


class TestComputeCluster(unittest.TestCase):
    """Test ComputeCluster dataclass"""
    
    def test_cluster_creation(self):
        instances = [
            ComputeInstance(
                id="i-1", name="node-1", public_ip="1.2.3.4",
                private_ip="10.0.1.1", hostname="node-1", ssh_user="root"
            ),
            ComputeInstance(
                id="i-2", name="node-2", public_ip="1.2.3.5",
                private_ip="10.0.1.2", hostname="node-2", ssh_user="root"
            ),
        ]
        
        cluster = ComputeCluster(
            name="test-cluster",
            instances=instances,
            provider="aws",
        )
        
        self.assertEqual(cluster.instance_count, 2)
        self.assertIsNotNone(cluster.primary_instance)
        self.assertEqual(cluster.primary_instance.name, "node-1")
    
    def test_get_instance_by_name(self):
        instances = [
            ComputeInstance(
                id="i-1", name="node-1", public_ip="1.2.3.4",
                private_ip="10.0.1.1", hostname="node-1", ssh_user="root"
            ),
        ]
        
        cluster = ComputeCluster(
            name="test-cluster",
            instances=instances,
            provider="aws",
        )
        
        found = cluster.get_instance_by_name("node-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, "i-1")
        
        not_found = cluster.get_instance_by_name("nonexistent")
        self.assertIsNone(not_found)


class TestBareMetalProvider(unittest.TestCase):
    """Test BareMetalProvider"""
    
    def test_import_cluster(self):
        config = {
            'baremetal': {
                'hosts': [
                    {
                        'hostname': 'server-1',
                        'ip': '192.168.1.101',
                        'ssh_user': 'admin',
                        'ssh_key_path': '~/.ssh/id_rsa',
                    },
                    {
                        'hostname': 'server-2',
                        'ip': '192.168.1.102',
                        'ssh_user': 'admin',
                        'ssh_key_path': '~/.ssh/id_rsa',
                    },
                ]
            }
        }
        
        provider = BareMetalProvider(config)
        cluster = provider.import_cluster(['192.168.1.101', '192.168.1.102'])
        
        self.assertEqual(cluster.instance_count, 2)
        self.assertEqual(cluster.provider, "baremetal")
        self.assertEqual(cluster.instances[0].name, "server-1")
        self.assertEqual(cluster.instances[1].name, "server-2")
    
    def test_create_cluster_raises(self):
        provider = BareMetalProvider({})
        
        with self.assertRaises(NotImplementedError):
            provider.create_cluster("test", 2)
    
    def test_terminate_instance(self):
        provider = BareMetalProvider({})
        result = provider.terminate_instance("192.168.1.101")
        
        self.assertFalse(result)  # Cannot terminate bare metal


class TestAWSProvider(unittest.TestCase):
    """Test AWSComputeProvider"""
    
    def test_provider_name(self):
        config = {'aws': {'region': 'us-east-1'}}
        provider = AWSComputeProvider(config)
        
        self.assertEqual(provider.provider_name, "aws")
        self.assertEqual(provider.region, "us-east-1")
    
    def test_get_ssh_config(self):
        config = {'aws': {'region': 'us-east-1', 'key_name': 'my-key'}}
        provider = AWSComputeProvider(config)
        
        instance = ComputeInstance(
            id="i-123", name="test", public_ip="1.2.3.4",
            private_ip="10.0.1.1", hostname="test", ssh_user="ec2-user",
            ssh_key_path="my-key",
        )
        
        ssh_config = provider.get_ssh_config(instance)
        
        self.assertEqual(ssh_config['host'], "1.2.3.4")
        self.assertEqual(ssh_config['port'], 22)
        self.assertEqual(ssh_config['user'], "ec2-user")


if __name__ == '__main__':
    unittest.main()
