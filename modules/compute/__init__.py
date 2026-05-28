"""
Compute abstraction layer for cloud-agnostic infrastructure provisioning.

This module provides a unified interface for creating and managing compute
resources across different cloud providers and bare metal.
"""

from .base import ComputeProvider, ComputeInstance, ComputeCluster
from .aws import AWSComputeProvider
from .baremetal import BareMetalProvider

__all__ = [
    'ComputeProvider',
    'ComputeInstance', 
    'ComputeCluster',
    'AWSComputeProvider',
    'BareMetalProvider',
]
