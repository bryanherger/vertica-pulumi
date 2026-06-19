#!/usr/bin/env python3
"""
Vertica NMA (Node Management Agent) Certificate Generator.

Generates a CA + server certificate chain for NMA HTTPS communication
across all nodes in the cluster, then deploys them to the Vertica default
HTTPS certificate directory on each node.

Usage:
    python scripts/generate_nma_certs.py \
        --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
        --ssh-key ~/.ssh/pulumi.pem \
        --output-dir ./certs

The generated certificates are used by:
- NMA (Node Management Agent) for HTTPS communication on port 5554
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

    def generate_certificates(self) -> Tuple[str, str, str]:
        """
        Generate CA and server certificates for NMA HTTPS.

        Returns:
            Tuple of (server_cert_path, server_key_path, ca_cert_path)
        """
        print(f"Generating RSA key pair and certificates...")
        print(f"  Country: {self.country}")
        print(f"  Organization: {self.organization}")
        print(f"  Common Name: {self.common_name}")
        print(f"  Validity: {self.validity_days} days")

        out_dir = self.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        ca_key_path = out_dir / "nma_ca_key.pem"
        ca_cert_path = out_dir / "nma_ca_cert.pem"
        server_key_path = out_dir / "nma_key.pem"
        server_csr_path = out_dir / "nma_csr.pem"
        server_cert_path = out_dir / "nma_cert.pem"

        # Generate CA private key
        print("  Generating 2048-bit CA private key...")
        subprocess.run([
            "openssl", "genrsa",
            "-out", str(ca_key_path),
            "2048"
        ], check=True, capture_output=True)

        # Generate self-signed CA certificate
        print("  Generating CA certificate...")
        subprocess.run([
            "openssl", "req", "-new", "-x509",
            "-key", str(ca_key_path),
            "-out", str(ca_cert_path),
            "-days", str(self.validity_days),
            "-subj", f"/C={self.country}/O={self.organization}/CN={self.common_name}-ca"
        ], check=True, capture_output=True)

        # Generate server private key
        print("  Generating 2048-bit server private key...")
        subprocess.run([
            "openssl", "genrsa",
            "-out", str(server_key_path),
            "2048"
        ], check=True, capture_output=True)

        # Generate server CSR
        print("  Generating server certificate signing request...")
        subprocess.run([
            "openssl", "req", "-new",
            "-key", str(server_key_path),
            "-out", str(server_csr_path),
            "-subj", f"/C={self.country}/O={self.organization}/CN={self.common_name}"
        ], check=True, capture_output=True)

        # Sign server certificate with CA
        print("  Signing server certificate with CA...")
        subprocess.run([
            "openssl", "x509", "-req",
            "-in", str(server_csr_path),
            "-CA", str(ca_cert_path),
            "-CAkey", str(ca_key_path),
            "-CAcreateserial",
            "-out", str(server_cert_path),
            "-days", str(self.validity_days),
            "-sha256"
        ], check=True, capture_output=True)

        # Clean up CSR and serial file
        server_csr_path.unlink(missing_ok=True)
        (out_dir / "nma_ca_cert.srl").unlink(missing_ok=True)

        # Set proper permissions
        os.chmod(ca_key_path, 0o600)
        os.chmod(ca_cert_path, 0o644)
        os.chmod(server_key_path, 0o600)
        os.chmod(server_cert_path, 0o644)

        print(f"  CA certificate saved: {ca_cert_path}")
        print(f"  Server certificate saved: {server_cert_path}")
        print(f"  Server key saved: {server_key_path}")

        return str(server_cert_path), str(server_key_path), str(ca_cert_path)

    def deploy_certificates(self, cert_path: str, key_path: str, ca_path: str) -> bool:
        """
        Deploy certificates to all nodes in the cluster.

        Args:
            cert_path: Path to server certificate file
            key_path: Path to server private key file
            ca_path: Path to CA certificate file

        Returns:
            True if all deployments succeeded
        """
        print(f"\nDeploying certificates to {len(self.hosts)} nodes...")

        # Vertica NMA looks in /opt/vertica/config/https_certs by default
        cert_dir = "/opt/vertica/config/https_certs"
        cert_dest = f"{cert_dir}/dbadmin.pem"
        key_dest = f"{cert_dir}/dbadmin.key"
        ca_dest = f"{cert_dir}/rootca.pem"

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
                    f"(getent group verticadba >/dev/null || sudo groupadd -g 10000 verticadba) && "
                    f"(getent passwd dbadmin >/dev/null || sudo useradd -u 10000 -g verticadba -m -s /bin/bash dbadmin) && "
                    f"sudo mkdir -p {cert_dir} && "
                    f"sudo chown dbadmin:verticadba {cert_dir} && "
                    f"sudo chmod 755 {cert_dir}"
                ]

                result = subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    print(f"    WARNING: Failed to create cert directory: {result.stderr.strip()}")

                # Upload files via scp
                for local, remote_name in [
                    (cert_path, "nma_cert.pem"),
                    (key_path, "nma_key.pem"),
                    (ca_path, "nma_ca_cert.pem"),
                ]:
                    scp_cmd = [
                        "scp",
                        "-i", self.ssh_key_path,
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=30",
                        local,
                        f"{self.ssh_user}@{host}:/tmp/{remote_name}"
                    ]
                    result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode != 0:
                        print(f"    ERROR: Failed to upload {remote_name}: {result.stderr.strip()}")
                        all_success = False
                        break
                else:
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
                        f"sudo mv /tmp/nma_ca_cert.pem {ca_dest} && "
                        f"sudo chown dbadmin:verticadba {cert_dest} {key_dest} {ca_dest} && "
                        f"sudo chmod 644 {cert_dest} {ca_dest} && "
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
                    "sudo -u dbadmin /opt/vertica/bin/manage_node_agent.sh stop node_management_agent 2>/dev/null || true; "
                    "sleep 2; "
                    "sudo -u dbadmin /opt/vertica/bin/manage_node_agent.sh start node_management_agent"
                ]

                result = subprocess.run(restart_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0:
                    print(f"    SUCCESS: NMA restart script executed on {host}")
                else:
                    print(f"    WARNING: NMA restart may require manual intervention on {host}")
                    print(f"    Output: {result.stdout.strip()}")
                    print(f"    Stderr: {result.stderr.strip()}")
                    all_success = False

            except Exception as e:
                print(f"    ERROR: Failed to restart NMA on {host}: {e}")
                all_success = False

        return all_success

    def verify_nma_health(self) -> bool:
        """
        Verify NMA service health on all nodes via the HTTPS health endpoint.

        Returns:
            True if all NMA services are healthy
        """
        print(f"\nVerifying NMA health on {len(self.hosts)} nodes...")

        all_healthy = True

        for host in self.hosts:
            print(f"  Checking NMA on {host}...")

            try:
                check_cmd = [
                    "ssh",
                    "-i", self.ssh_key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=30",
                    "-o", "BatchMode=yes",
                    f"{self.ssh_user}@{host}",
                    "sleep 3; "
                    "curl -fsSk https://localhost:5554/v1/health 2>&1 || echo 'NMA_NOT_HEALTHY'"
                ]

                result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=30)
                output = result.stdout.strip()

                if 'healthy' in output.lower():
                    print(f"    HEALTHY: NMA is running on {host}")
                else:
                    print(f"    WARNING: NMA may not be running on {host}")
                    print(f"    Output: {output}")
                    if result.stderr.strip():
                        print(f"    Stderr: {result.stderr.strip()}")
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
        cert_path, key_path, ca_path = self.generate_certificates()

        # Deploy to all nodes
        deploy_success = self.deploy_certificates(cert_path, key_path, ca_path)
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
        print(f"Server Certificate: {cert_path}")
        print(f"Server Key: {key_path}")
        print(f"CA Certificate: {ca_path}")
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
