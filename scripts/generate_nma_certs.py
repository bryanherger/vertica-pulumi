#!/usr/bin/env python3
"""
Vertica NMA (Node Management Agent) Certificate Generator.

Generates RSA key pairs and self-signed certificates for NMA HTTPS
communication across all nodes in the cluster. Deploys certificates
to all nodes via SSH.

Usage:
    python scripts/generate_nma_certs.py \
        --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
        --ssh-key ~/.ssh/pulumi.pem \
        --output-dir ./certs

The generated certificates are used by:
- NMA (Node Management Agent) for HTTPS communication
- vcluster for secure node management operations
- Database creation and management commands

Prerequisites:
    - OpenSSL installed locally
    - SSH access to all nodes
    - Python 3.6+
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional


class NMACertificateGenerator:
    """Generates and deploys NMA certificates for Vertica Eon Mode cluster"""
    
    def __init__(self, hosts: List[str], ssh_key_path: str, ssh_user: str = "ec2-user",
                 output_dir: str = "./certs", cert_config: Optional[dict] = None):
        """
        Initialize certificate generator.
        
        Args:
            hosts: List of host IPs or hostnames
            ssh_key_path: Path to SSH private key
            ssh_user: SSH username (default: ec2-user)
            output_dir: Directory to store generated certificates
            cert_config: Certificate configuration (country, org, cn, validity_days)
        """
        self.hosts = hosts
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self.ssh_user = ssh_user
        self.output_dir = Path(output_dir)
        self.cert_config = cert_config or {}
        
        # Certificate parameters
        self.country = self.cert_config.get('country', 'US')
        self.organization = self.cert_config.get('organization', 'Vertica')
        self.common_name = self.cert_config.get('common_name', 'vertica-nma')
        self.validity_days = self.cert_config.get('validity_days', 365)
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def generate_certificates(self) -> Tuple[str, str]:
        """
        Generate RSA private key and self-signed certificate.
        
        Returns:
            Tuple of (cert_path, key_path)
        """
        print(f"Generating RSA key pair and certificate...")
        print(f"  Country: {self.country}")
        print(f"  Organization: {self.organization}")
        print(f"  Common Name: {self.common_name}")
        print(f"  Validity: {self.validity_days} days")
        
        key_path = self.output_dir / "nma_key.pem"
        cert_path = self.output_dir / "nma_cert.pem"
        csr_path = self.output_dir / "nma_csr.pem"
        
        # Generate private key (RSA 2048)
        print("  Generating 2048-bit RSA private key...")
        subprocess.run([
            "openssl", "genrsa",
            "-out", str(key_path),
            "2048"
        ], check=True, capture_output=True)
        
        # Generate CSR
        print("  Generating certificate signing request...")
        subprocess.run([
            "openssl", "req", "-new",
            "-key", str(key_path),
            "-out", str(csr_path),
            "-subj", f"/C={self.country}/O={self.organization}/CN={self.common_name}"
        ], check=True, capture_output=True)
        
        # Generate self-signed certificate
        print("  Generating self-signed certificate...")
        subprocess.run([
            "openssl", "x509", "-req",
            "-in", str(csr_path),
            "-signkey", str(key_path),
            "-out", str(cert_path),
            "-days", str(self.validity_days),
            "-sha256"
        ], check=True, capture_output=True)
        
        # Clean up CSR
        csr_path.unlink()
        
        # Set proper permissions
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
        
        print(f"  Certificate saved: {cert_path}")
        print(f"  Key saved: {key_path}")
        
        return str(cert_path), str(key_path)
    
    def deploy_certificates(self, cert_path: str, key_path: str) -> bool:
        """
        Deploy certificates to all nodes in the cluster.
        
        Args:
            cert_path: Path to certificate file
            key_path: Path to private key file
            
        Returns:
            True if all deployments succeeded
        """
        print(f"\nDeploying certificates to {len(self.hosts)} nodes...")
        
        # Vertica certificate locations
        cert_dest = "/opt/vertica/config/share/nma_cert.pem"
        key_dest = "/opt/vertica/config/share/nma_key.pem"
        cert_dir = "/opt/vertica/config/share"
        
        all_success = True
        
        for host in self.hosts:
            print(f"\n  Deploying to {host}...")
            
            try:
                # Create certificate directory on remote host
                mkdir_cmd = [
                    "ssh",
                    "-i", self.ssh_key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=30",
                    "-o", "BatchMode=yes",
                    f"{self.ssh_user}@{host}",
                    f"sudo mkdir -p {cert_dir} && sudo chown dbadmin:verticadba {cert_dir} && sudo chmod 755 {cert_dir}"
                ]
                
                result = subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    print(f"    WARNING: Failed to create cert directory: {result.stderr.strip()}")
                
                # Upload certificate
                scp_cert_cmd = [
                    "scp",
                    "-i", self.ssh_key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=30",
                    cert_path,
                    f"{self.ssh_user}@{host}:/tmp/nma_cert.pem"
                ]
                
                result = subprocess.run(scp_cert_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    print(f"    ERROR: Failed to upload certificate: {result.stderr.strip()}")
                    all_success = False
                    continue
                
                # Upload key
                scp_key_cmd = [
                    "scp",
                    "-i", self.ssh_key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=30",
                    key_path,
                    f"{self.ssh_user}@{host}:/tmp/nma_key.pem"
                ]
                
                result = subprocess.run(scp_key_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    print(f"    ERROR: Failed to upload key: {result.stderr.strip()}")
                    all_success = False
                    continue
                
                # Move files to final location with correct permissions
                move_cmd = [
                    "ssh",
                    "-i", self.ssh_key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=30",
                    "-o", "BatchMode=yes",
                    f"{self.ssh_user}@{host}",
                    f"sudo mv /tmp/nma_cert.pem {cert_dest} && "
                    f"sudo mv /tmp/nma_key.pem {key_dest} && "
                    f"sudo chown dbadmin:verticadba {cert_dest} {key_dest} && "
                    f"sudo chmod 644 {cert_dest} && "
                    f"sudo chmod 600 {key_dest} && "
                    f"echo 'Certificates deployed successfully'"
                ]
                
                result = subprocess.run(move_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    print(f"    ERROR: Failed to move certificates: {result.stderr.strip()}")
                    all_success = False
                    continue
                
                print(f"    SUCCESS: Certificates deployed to {host}")
                
            except subprocess.TimeoutExpired:
                print(f"    ERROR: Timeout deploying to {host}")
                all_success = False
            except Exception as e:
                print(f"    ERROR: Exception deploying to {host}: {e}")
                all_success = False
        
        return all_success
    
    def restart_nma_services(self) -> bool:
        """
        Restart NMA services on all nodes after certificate deployment.
        
        Returns:
            True if all restarts succeeded
        """
        print(f"\nRestarting NMA services on {len(self.hosts)} nodes...")
        
        all_success = True
        
        for host in self.hosts:
            print(f"  Restarting NMA on {host}...")
            
            try:
                restart_cmd = [
                    "ssh",
                    "-i", self.ssh_key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=30",
                    "-o", "BatchMode=yes",
                    f"{self.ssh_user}@{host}",
                    "sudo systemctl restart vertica-nma || "
                    "sudo /opt/vertica/sbin/vertica-nma restart || "
                    "echo 'NMA restart attempted - may need manual restart'"
                ]
                
                result = subprocess.run(restart_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0:
                    print(f"    SUCCESS: NMA restarted on {host}")
                else:
                    print(f"    WARNING: NMA restart may require manual intervention on {host}")
                    print(f"    Output: {result.stdout.strip()}")
                    
            except Exception as e:
                print(f"    ERROR: Failed to restart NMA on {host}: {e}")
                all_success = False
        
        return all_success
    
    def verify_nma_health(self) -> bool:
        """
        Verify NMA service health on all nodes.
        
        Returns:
            True if all NMA services are healthy
        """
        print(f"\nVerifying NMA health on {len(self.hosts)} nodes...")
        
        all_healthy = True
        
        for host in self.hosts:
            print(f"  Checking NMA on {host}...")
            
            try:
                # Try to check NMA status via systemctl
                check_cmd = [
                    "ssh",
                    "-i", self.ssh_key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=30",
                    "-o", "BatchMode=yes",
                    f"{self.ssh_user}@{host}",
                    "sudo systemctl is-active vertica-nma || "
                    "sudo /opt/vertica/sbin/vertica-nma status || "
                    "ps aux | grep -q 'vertica-nma' && echo 'running' || echo 'not running'"
                ]
                
                result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=30)
                output = result.stdout.strip().lower()
                
                if 'active' in output or 'running' in output:
                    print(f"    HEALTHY: NMA is running on {host}")
                else:
                    print(f"    WARNING: NMA may not be running on {host}")
                    print(f"    Output: {output}")
                    all_healthy = False
                    
            except Exception as e:
                print(f"    ERROR: Failed to check NMA on {host}: {e}")
                all_healthy = False
        
        return all_healthy
    
    def run(self) -> bool:
        """
        Execute full certificate generation and deployment workflow.
        
        Returns:
            True if all operations succeeded
        """
        print("=" * 60)
        print("Vertica NMA Certificate Generator")
        print("=" * 60)
        
        # Generate certificates
        cert_path, key_path = self.generate_certificates()
        
        # Deploy to all nodes
        deploy_success = self.deploy_certificates(cert_path, key_path)
        if not deploy_success:
            print("\nWARNING: Some nodes failed certificate deployment")
        
        # Restart NMA services
        restart_success = self.restart_nma_services()
        if not restart_success:
            print("\nWARNING: Some NMA services failed to restart")
        
        # Verify health
        health_success = self.verify_nma_health()
        if not health_success:
            print("\nWARNING: Some NMA services may not be healthy")
        
        # Summary
        print("\n" + "=" * 60)
        print("Certificate Deployment Summary")
        print("=" * 60)
        print(f"Nodes: {len(self.hosts)}")
        print(f"Certificate: {cert_path}")
        print(f"Key: {key_path}")
        print(f"Deploy: {'SUCCESS' if deploy_success else 'PARTIAL FAILURE'}")
        print(f"Restart: {'SUCCESS' if restart_success else 'PARTIAL FAILURE'}")
        print(f"Health: {'ALL HEALTHY' if health_success else 'SOME ISSUES'}")
        
        return deploy_success and restart_success and health_success


def main():
    parser = argparse.ArgumentParser(
        description="Generate and deploy NMA certificates for Vertica Eon Mode"
    )
    parser.add_argument(
        "--hosts",
        required=True,
        help="Comma-separated list of host IPs or hostnames"
    )
    parser.add_argument(
        "--ssh-key",
        required=True,
        help="Path to SSH private key"
    )
    parser.add_argument(
        "--ssh-user",
        default="ec2-user",
        help="SSH username (default: ec2-user)"
    )
    parser.add_argument(
        "--output-dir",
        default="./certs",
        help="Output directory for certificates (default: ./certs)"
    )
    parser.add_argument(
        "--country",
        default="US",
        help="Certificate country code (default: US)"
    )
    parser.add_argument(
        "--organization",
        default="Vertica",
        help="Certificate organization (default: Vertica)"
    )
    parser.add_argument(
        "--common-name",
        default="vertica-nma",
        help="Certificate common name (default: vertica-nma)"
    )
    parser.add_argument(
        "--validity-days",
        type=int,
        default=365,
        help="Certificate validity in days (default: 365)"
    )
    
    args = parser.parse_args()
    
    hosts = [h.strip() for h in args.hosts.split(",")]
    
    cert_config = {
        'country': args.country,
        'organization': args.organization,
        'common_name': args.common_name,
        'validity_days': args.validity_days
    }
    
    generator = NMACertificateGenerator(
        hosts=hosts,
        ssh_key_path=args.ssh_key,
        ssh_user=args.ssh_user,
        output_dir=args.output_dir,
        cert_config=cert_config
    )
    
    success = generator.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
