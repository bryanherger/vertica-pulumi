#!/usr/bin/env python3
"""
Vertica Eon Mode Installation Script.

Automates the installation and configuration of Vertica in Eon Mode
on AWS EC2 instances. Handles RPM distribution, communal storage setup,
certificate generation, NMA service startup, and database creation.

Usage:
    # Full automated deployment
    python scripts/install_vertica_eon.py \
        --config config/config_eon.yaml \
        --rpm-path /path/to/vertica.rpm \
        --license-path /path/to/license.xml

    # Or manual host specification
    python scripts/install_vertica_eon.py \
        --hosts 10.0.1.10,10.0.1.11,10.0.1.12 \
        --ssh-key ~/.ssh/pulumi.pem \
        --rpm-path /path/to/vertica.rpm \
        --communal-storage s3://my-bucket/analytics \
        --shard-count 3

Prerequisites:
    - AWS infrastructure deployed via Pulumi
    - SSH access to all instances
    - Vertica RPM and license file
    - S3 bucket for communal storage
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import yaml
from pathlib import Path
from typing import List, Optional, Tuple


class VerticaEonInstaller:
    """Handles Vertica Eon Mode installation on cluster nodes"""

    def __init__(self, config: dict, ssh_key_path: str, ssh_user: str = "ec2-user"):
        """
        Initialize Eon Mode installer.

        Args:
            config: Configuration dictionary
            ssh_key_path: Path to SSH private key
            ssh_user: SSH username
        """
        self.config = config
        self.vertica_config = config.get('vertica', {})
        self.eon_config = self.vertica_config.get('eon', {})
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self.ssh_user = ssh_user
        self.compute_config = config.get('compute', {})

        # Get instance IPs
        if config.get('_instance_ips'):
            self.instance_ips = config['_instance_ips']
        else:
            self.instance_ips = self._get_instance_ips()

        # Get private IPs for internal Vertica communication (vcluster hosts)
        if config.get('_instance_private_ips'):
            self.instance_private_ips = config['_instance_private_ips']
        else:
            self.instance_private_ips = self._get_instance_private_ips()

        # If no private IPs found, fall back to public IPs for vcluster hosts
        if not self.instance_private_ips:
            self.instance_private_ips = self.instance_ips

        # Eon Mode settings
        self.communal_storage = self.eon_config.get('communal_storage_location', '')
        self.shard_count = self.eon_config.get('shard_count', 3)
        self.depot_path = self.eon_config.get('depot_path', '/data/depot')
        self.depot_size = self.eon_config.get('depot_size', '80%')

        # Database settings
        self.db_config = self.vertica_config.get('database', {})
        self.db_name = self.db_config.get('name', 'analytics')
        self.admin_username = self.db_config.get('admin_username', 'dbadmin')
        self.admin_password = self.db_config.get('admin_password', '')

        # Database initialization action (Create or Revive)
        self.db_init = self.eon_config.get('dbinit', 'Create').lower()
        if self.db_init not in ('create', 'revive'):
            print(f"WARNING: Invalid dbinit '{self.db_init}', defaulting to 'Create'")
            self.db_init = 'create'

        # AWS settings for S3
        self.aws_access_key = self.eon_config.get('aws_access_key_id', '')
        self.aws_secret_key = self.eon_config.get('aws_secret_access_key', '')
        self.aws_region = self.eon_config.get('aws_region', 'us-east-2')
        self.s3_endpoint = self.eon_config.get('s3_endpoint', '')
        self.aws_enable_https = self.eon_config.get('aws_enable_https', True)
        self.enable_s3_encryption = self.eon_config.get('enable_s3_encryption', True)
        self.s3_auth_mode = self.compute_config.get('aws', {}).get('s3_auth_mode', 'iam_role')

        # Certificate settings
        self.security_config = self.vertica_config.get('security', {})
        self.generate_certs = self.security_config.get('generate_nma_certs', True)

    def _get_instance_ips(self) -> List[str]:
        """Get instance public IPs from Pulumi outputs or config"""
        # Try the aggregated instance_ips output first
        try:
            result = subprocess.run(
                ["pulumi", "stack", "output", "instance_ips", "--json"],
                capture_output=True, text=True, check=True
            )
            ips = json.loads(result.stdout)
            if isinstance(ips, list) and len(ips) > 0:
                return ips
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            pass

        # Fallback: try individual node_*_public_ip / node_*_private_ip outputs
        for suffix in ("public_ip", "private_ip"):
            ips = []
            node = 1
            while True:
                try:
                    result = subprocess.run(
                        ["pulumi", "stack", "output", f"node_{node}_{suffix}", "--json"],
                        capture_output=True, text=True, check=True
                    )
                    ip = json.loads(result.stdout)
                    if ip:
                        ips.append(ip)
                        node += 1
                    else:
                        break
                except (subprocess.CalledProcessError, json.JSONDecodeError):
                    break
            if ips:
                return ips

        # Fallback: try to read from config (for bare metal)
        hosts = self.compute_config.get('baremetal', {}).get('hosts', [])
        if hosts:
            return [h['ip'] for h in hosts]

        return []

    def _get_instance_private_ips(self) -> List[str]:
        """Get instance private IPs from Pulumi outputs for internal Vertica communication."""
        # Try the aggregated instance_private_ips output first
        try:
            result = subprocess.run(
                ["pulumi", "stack", "output", "instance_private_ips", "--json"],
                capture_output=True, text=True, check=True
            )
            ips = json.loads(result.stdout)
            if isinstance(ips, list) and len(ips) > 0:
                return ips
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            pass

        # Fallback: try individual node_*_private_ip outputs
        ips = []
        node = 1
        while True:
            try:
                result = subprocess.run(
                    ["pulumi", "stack", "output", f"node_{node}_private_ip", "--json"],
                    capture_output=True, text=True, check=True
                )
                ip = json.loads(result.stdout)
                if ip:
                    ips.append(ip)
                    node += 1
                else:
                    break
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                break
        if ips:
            return ips

        # Fallback to public IPs if no private IPs found
        return []

    def _ssh(self, ip: str, command: str, timeout: int = 300,
             sudo: bool = False) -> Tuple[int, str, str]:
        """Execute SSH command on remote host"""
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

        try:
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -1, "", str(e)

    def _scp(self, local_path: str, remote_path: str, ip: str,
             timeout: int = 1800) -> Tuple[int, str, str]:
        """Copy file to remote host via SCP

        SECURITY NOTE: For large files (like Vertica RPM), SCP can be very slow.
        Consider using S3 as an intermediary instead of direct SCP:
        1. Upload RPM to S3: aws s3 cp local.rpm s3://bucket/
        2. Download on instances: aws s3 cp s3://bucket/local.rpm /tmp/
        This is faster and more reliable for multi-node deployments.
        """
        scp_cmd = [
            "scp",
            "-i", self.ssh_key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            "-o", "BatchMode=yes",
            local_path,
            f"{self.ssh_user}@{ip}:{remote_path}",
        ]

        try:
            # Use Popen for large files with long timeout
            process = subprocess.Popen(
                scp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate(timeout=timeout)
            return process.returncode, stdout.decode() if stdout else "", stderr.decode() if stderr else ""
        except subprocess.TimeoutExpired:
            process.kill()
            return -1, "", f"SCP timed out after {timeout} seconds"
        except Exception as e:
            return -1, "", str(e)

    def upload_rpm(self, rpm_path: str) -> bool:
        """Upload Vertica RPM to all nodes"""
        if not os.path.exists(rpm_path):
            print(f"ERROR: RPM file not found: {rpm_path}")
            return False

        rpm_name = os.path.basename(rpm_path)
        print(f"Uploading {rpm_name} to {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"  Uploading to {ip}...")
            rc, _, err = self._scp(rpm_path, f"/tmp/{rpm_name}", ip)
            if rc != 0:
                print(f"    FAILED: {err}")
                all_success = False
            else:
                print(f"    SUCCESS")

        return all_success

    def upload_license(self, license_path: str) -> bool:
        """Upload license file to all nodes"""
        if not license_path or not os.path.exists(license_path):
            print("WARNING: No license file provided. Vertica 26.1+ requires a valid license.")
            return False

        license_name = os.path.basename(license_path)
        print(f"Uploading {license_name} to {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"  Uploading to {ip}...")
            rc, _, err = self._scp(license_path, f"/tmp/{license_name}", ip)
            if rc != 0:
                print(f"    FAILED: {err}")
                all_success = False
            else:
                print(f"    SUCCESS")

        return all_success

    def _ensure_vertica_user(self, ip: str) -> bool:
        """Ensure the verticadba group and dbadmin user exist on the node."""
        cmd = (
            "getent group verticadba >/dev/null || sudo groupadd -g 10000 verticadba; "
            "getent passwd dbadmin >/dev/null || sudo useradd -u 10000 -g verticadba -m -s /bin/bash dbadmin"
        )
        rc, out, err = self._ssh(ip, cmd, timeout=30)
        if rc != 0:
            print(f"    WARNING: Could not ensure dbadmin/verticadba: {err}")
            return False
        return True

    def install_vertica(self, rpm_path: str, license_path: str = "") -> bool:
        """Install Vertica RPM on all nodes"""
        rpm_name = os.path.basename(rpm_path)
        license_name = os.path.basename(license_path) if license_path else ""

        print(f"\nInstalling Vertica on {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"\n  Installing on {ip}...")

            # Install prerequisites
            print("    Installing prerequisites (dialog, psmisc, which, net-tools)...")
            prereq_cmd = (
                "if command -v dnf >/dev/null 2>&1; then "
                "  sudo dnf install -y dialog psmisc which net-tools; "
                "elif command -v yum >/dev/null 2>&1; then "
                "  sudo yum install -y dialog psmisc which net-tools; "
                "elif command -v apt-get >/dev/null 2>&1; then "
                "  sudo apt-get install -y dialog psmisc net-tools; "
                "else "
                "  echo 'WARNING: no supported package manager found'; "
                "fi"
            )
            self._ssh(ip, prereq_cmd, sudo=False, timeout=180)

            # Try package-manager based install first (resolves dependencies)
            install_cmd = (
                "if command -v dnf >/dev/null 2>&1; then "
                f"  sudo dnf localinstall -y /tmp/{rpm_name}; "
                "elif command -v yum >/dev/null 2>&1; then "
                f"  sudo yum localinstall -y /tmp/{rpm_name}; "
                "else "
                f"  sudo rpm -ivh --nodeps /tmp/{rpm_name} || sudo rpm -Uvh --nodeps /tmp/{rpm_name}; "
                "fi"
            )
            rc, out, err = self._ssh(ip, install_cmd, sudo=False, timeout=300)
            if rc != 0:
                print(f"    ERROR: RPM install failed: {err}")
                print("    Attempting fallback install with --nodeps...")
                fallback_cmd = f"sudo rpm -ivh --nodeps /tmp/{rpm_name} || sudo rpm -Uvh --nodeps /tmp/{rpm_name}"
                rc, out, err = self._ssh(ip, fallback_cmd, sudo=False, timeout=300)
                if rc != 0:
                    print(f"    ERROR: Fallback RPM install also failed: {err}")
                    all_success = False
                    continue
                else:
                    print(f"    RPM installed successfully with --nodeps")
            else:
                print(f"    RPM installed successfully")

            # Ensure dbadmin user exists (RPM normally creates it, but fallback may not)
            self._ensure_vertica_user(ip)

            # Store RPM path for later use by install_vertica TLS generation
            self.rpm_path = rpm_path

            # Copy license if provided
            if license_name:
                license_cmd = (
                    f"sudo bash -c 'mkdir -p /opt/vertica/config/licensing && "
                    f"cp /tmp/{license_name} /opt/vertica/config/licensing/license.xml && "
                    f"chown -R dbadmin:verticadba /opt/vertica/config/licensing && "
                    f"chmod 644 /opt/vertica/config/licensing/license.xml'"
                )
                rc, out, err = self._ssh(ip, license_cmd, sudo=False, timeout=60)
                if rc != 0:
                    print(f"    WARNING: License install failed: {err}")
                else:
                    print(f"    License installed")

            # Fix permissions and create Vertica data directories
            perm_cmd = (
                f"sudo bash -c 'chown -R dbadmin:verticadba /opt/vertica/config 2>/dev/null; "
                f"chmod 755 /opt/vertica/config 2>/dev/null; "
                f"mkdir -p /opt/vertica/log && chown dbadmin:verticadba /opt/vertica/log && chmod 755 /opt/vertica/log; "
                f"mkdir -p /data/catalog /data/vertica {self.depot_path} && "
                f"chown dbadmin:verticadba /data/catalog /data/vertica {self.depot_path} && "
                f"chmod 755 /data/catalog /data/vertica {self.depot_path}'"
            )
            rc, out, err = self._ssh(ip, perm_cmd, sudo=False, timeout=60)
            if rc != 0:
                print(f"    WARNING: Permission setup failed: {err}")
            else:
                print(f"    Permissions set")

            # Verify vcluster exists
            check_cmd = "ls -la /opt/vertica/bin/vcluster 2>/dev/null || echo 'vcluster NOT FOUND'"
            rc, out, err = self._ssh(ip, check_cmd, sudo=False, timeout=30)
            if "vcluster NOT FOUND" in out:
                print(f"    ERROR: Vertica did not install correctly (vcluster missing)")
                all_success = False
            else:
                print(f"    Verified: {out.strip()}")

        return all_success

    def _setup_root_ssh_between_nodes(self) -> bool:
        """
        Configure passwordless root SSH between all cluster nodes so that
        install_vertica can run in multi-node mode and generate per-node certs.
        """
        if not self.instance_ips:
            return False

        print("\n  Setting up root SSH between cluster nodes...")

        with open(self.ssh_key_path, 'r') as f:
            key_material = f.read().strip()
        pub_key_material = None
        pub_key_path = self.ssh_key_path + '.pub'
        if os.path.exists(pub_key_path):
            with open(pub_key_path, 'r') as f:
                pub_key_material = f.read().strip()
        else:
            # Derive public key from private key
            result = subprocess.run(
                ["ssh-keygen", "-y", "-f", self.ssh_key_path],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                pub_key_material = result.stdout.strip()
            else:
                print(f"    ERROR: Could not derive public key: {result.stderr}")
                return False

        all_ok = True
        for ip in self.instance_ips:
            print(f"    Configuring {ip} for root SSH...")

            # Build the root-SSH setup script locally and run it via base64 to avoid quoting hell.
            setup_script = f"""#!/bin/bash
set -e
mkdir -p /root/.ssh
chmod 700 /root/.ssh
cat > /root/.ssh/id_rsa <<'KEYEOF'
{key_material}
KEYEOF
chmod 600 /root/.ssh/id_rsa
if [ ! -f /root/.ssh/authorized_keys ] || ! grep -qF '{pub_key_material}' /root/.ssh/authorized_keys; then
    echo '{pub_key_material}' >> /root/.ssh/authorized_keys
fi
chmod 600 /root/.ssh/authorized_keys
if [ -f /etc/ssh/sshd_config ]; then
    sed -i 's/^#*\\s*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
    sed -i 's/^#*\\s*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
fi
systemctl reload sshd || service sshd reload || true
"""
            encoded = base64.b64encode(setup_script.encode('utf-8')).decode('utf-8')
            cmd = f"echo '{encoded}' | base64 -d | sudo bash"
            rc, out, err = self._ssh(ip, cmd, sudo=False, timeout=60)
            if rc != 0:
                print(f"      FAILED: {err}")
                all_ok = False
            else:
                # Quick connectivity test from each node to every other node
                for other_ip in self.instance_private_ips:
                    if other_ip == self.instance_private_ips[self.instance_ips.index(ip)]:
                        continue
                    test_script = f"""#!/bin/bash
ssh -i /root/.ssh/id_rsa -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@{other_ip} 'echo ROOT_SSH_OK' 2>&1
"""
                    test_encoded = base64.b64encode(test_script.encode('utf-8')).decode('utf-8')
                    test_cmd = f"echo '{test_encoded}' | base64 -d | sudo bash"
                    rc2, out2, err2 = self._ssh(ip, test_cmd, sudo=False, timeout=30)
                    if rc2 != 0 or 'ROOT_SSH_OK' not in out2:
                        print(f"      WARNING: root SSH from {ip} to {other_ip} failed: {err2.strip() or out2.strip()}")
                        all_ok = False
                    else:
                        print(f"      OK: root SSH {ip} -> {other_ip}")

        return all_ok

    def generate_tls_material_no_ssh(self) -> bool:
        """
        Generate Vertica NMA/HTTPS TLS material on the local Pulumi runner and
        deploy it to all cluster nodes.

        This mirrors what install_vertica does internally (root CA, HTTPS server
        cert, dbadmin client cert, httpstls.json) without requiring node-to-node
        SSH.  vcluster is then used for all cluster management.
        """
        if not self.generate_certs:
            print("Certificate generation disabled in config")
            return True

        if not self.instance_ips or not self.instance_private_ips:
            print("ERROR: No instance IPs available for cert generation")
            return False

        print("\nGenerating TLS material locally and deploying to all nodes...")
        print(f"  Nodes: {', '.join(self.instance_private_ips)}")

        certs_dir = Path("./https_certs_gen").resolve()
        certs_dir.mkdir(parents=True, exist_ok=True)

        # Build subjectAltName entries for all node private IPs, hostnames, and localhost
        san_entries = ["DNS:localhost"]
        for i, ip in enumerate(self.instance_private_ips):
            san_entries.append(f"IP:{ip}")
            san_entries.append(f"DNS:vertica-eon-node{i+1}")
            san_entries.append(f"DNS:vertica-eon-node{i+1}.local")
        san = ",".join(san_entries)

        # OpenSSL configuration file matching Vertica's expectations
        openssl_cnf = certs_dir / "vertica_https_openssl.cnf"
        openssl_cnf.write_text(f"""[ ca ]
default_ca = CA_default

[ CA_default ]
dir = {certs_dir}
certificate = $dir/rootca.pem
private_key = $dir/rootca.key
crl_dir = $dir
database = $dir/index.txt
new_certs_dir = $dir
serial = $dir/serial
RANDFILE = $dir/.rand
default_days = 3650
default_crl_days = 30
default_md = sha256
preserve = no
policy = policy_anything
name_opt = ca_default
cert_opt = ca_default
unique_subject = no
copy_extensions = copy

[ req ]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[ req_distinguished_name ]
C = US
ST = Massachusetts
L = Cambridge
O = OpenText
OU = Vertica

[ policy_anything ]
countryName = optional
stateOrProvinceName = optional
localityName = optional
organizationName = optional
organizationalUnitName = optional
commonName = optional
emailAddress = optional

[ root_ca ]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer:always

[ server_cert ]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = {san}

[ usr_cert ]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth, emailProtection
""")

        # CA database files required by openssl ca
        (certs_dir / "index.txt").write_text("")
        (certs_dir / "index.txt.attr").write_text("")
        (certs_dir / "serial").write_text("1000\n")

        # Shell script based on community-provided version, fixed and adapted
        cert_script = certs_dir / "gen_certs.sh"
        cert_script.write_text(f"""#!/bin/bash
set -e
cd "{certs_dir}"

touch index.txt
# Fix typo in original script: index.txt.attr, not tndex.txt.attr
touch index.txt.attr

# Root CA
openssl req -x509 -config vertica_https_openssl.cnf \\
  -out rootca_cert.pem -newkey rsa:4096 -keyout rootca_key.pem \\
  -subj /C=US/ST=Massachusetts/L=Cambridge/O=OpenText/OU=Vertica/CN=rootca \\
  -extensions root_ca -nodes -days 3650

# NMA / HTTPS server cert (signed by root CA)
openssl req -config vertica_https_openssl.cnf \\
  -out nma_csr.pem -newkey rsa:2048 -keyout nma_key.pem \\
  -subj /C=US/ST=Massachusetts/L=Cambridge/O=OpenText/OU=Vertica/CN=NMA \\
  -extensions server_cert -nodes

openssl ca -config vertica_https_openssl.cnf \\
  -in nma_csr.pem -out nma_cert.pem -extensions server_cert \\
  -cert rootca_cert.pem -keyfile rootca_key.pem -notext -batch

# httpstls.json used by the Vertica HTTPS service bootstrap
python3 - <<'PYEOF'
import json, pathlib
base = pathlib.Path("{certs_dir}")
def pem_text(name):
    return base.joinpath(name).read_text().replace('\\n', '\\\\n')
key = pem_text("nma_key.pem")
cert = pem_text("nma_cert.pem")
ca = pem_text("rootca_cert.pem")
cfg = {{"name": "server", "cipher_suites": "", "mode": 2,
        "key": key, "certificate": cert, "chain_certs": [],
        "ca_certificates": [ca]}}
base.joinpath("httpstls.json").write_text(json.dumps(cfg))
PYEOF

# dbadmin client cert (signed by root CA)
USER=dbadmin
openssl req -config vertica_https_openssl.cnf \\
  -out dbadmin_csr.pem -newkey rsa:2048 -keyout dbadmin_key.pem \\
  -subj /C=US/ST=Massachusetts/L=Cambridge/O=OpenText/OU=Vertica/CN=$USER \\
  -extensions usr_cert -nodes

openssl ca -config vertica_https_openssl.cnf \\
  -in dbadmin_csr.pem -out dbadmin_cert.pem -extensions usr_cert \\
  -cert rootca_cert.pem -keyfile rootca_key.pem -notext -batch
""")
        cert_script.chmod(0o755)

        # Run the cert generation script
        print("  Running local certificate generation script...")
        result = subprocess.run(
            ["bash", str(cert_script)],
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            print(f"  ERROR: Cert generation failed")
            print(f"  stdout: {result.stdout}")
            print(f"  stderr: {result.stderr}")
            return False

        # Rename generated files to Vertica's expected default names
        expected_files = {
            "rootca_cert.pem": "rootca.pem",
            "rootca_key.pem": "rootca.key",
            "nma_cert.pem": "vertica_https.pem",
            "nma_key.pem": "vertica_https.key",
            "dbadmin_cert.pem": "dbadmin.pem",
            "dbadmin_key.pem": "dbadmin.key",
            "httpstls.json": "httpstls.json",
        }
        for src_name, dst_name in expected_files.items():
            src = certs_dir / src_name
            dst = certs_dir / dst_name
            if src.exists():
                src.rename(dst)
                print(f"  Created {dst_name}")
            elif not dst.exists():
                print(f"  WARNING: Expected file {src_name} not generated")

        # Verify the essential files are present
        required = ["rootca.pem", "rootca.key", "vertica_https.pem", "vertica_https.key",
                    "dbadmin.pem", "dbadmin.key", "httpstls.json"]
        missing = [f for f in required if not (certs_dir / f).exists()]
        if missing:
            print(f"  ERROR: Missing generated files: {missing}")
            return False

        # Set permissions locally
        for key_file in certs_dir.glob("*.key"):
            key_file.chmod(0o600)
        for pem_file in certs_dir.glob("*.pem"):
            pem_file.chmod(0o644)
        (certs_dir / "httpstls.json").chmod(0o600)

        # Deploy https_certs to every node
        print("\nDeploying TLS material to all nodes...")
        all_deployed = True
        for ip in self.instance_ips:
            print(f"  Deploying to {ip}...")
            # Make a fresh tarball for this node so permissions are clean
            tar_path = certs_dir / "https_certs.tar.gz"
            tar_result = subprocess.run(
                ["tar", "-czf", str(tar_path), "-C", str(certs_dir), "rootca.pem",
                 "rootca.key", "vertica_https.pem", "vertica_https.key",
                 "dbadmin.pem", "dbadmin.key", "httpstls.json"],
                capture_output=True, text=True
            )
            if tar_result.returncode != 0:
                print(f"    FAILED to package certs: {tar_result.stderr}")
                all_deployed = False
                continue

            scp_cmd = [
                "scp",
                "-i", self.ssh_key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                str(tar_path),
                f"{self.ssh_user}@{ip}:/tmp/https_certs.tar.gz",
            ]
            try:
                scp_res = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=180)
                if scp_res.returncode != 0:
                    print(f"    FAILED to copy certs: {scp_res.stderr}")
                    all_deployed = False
                    continue
            except Exception as e:
                print(f"    FAILED to copy certs: {e}")
                all_deployed = False
                continue

            extract_cmd = (
                "sudo rm -rf /opt/vertica/config/https_certs && "
                "sudo mkdir -p /opt/vertica/config/https_certs && "
                "sudo tar -xzf /tmp/https_certs.tar.gz -C /opt/vertica/config/https_certs && "
                "sudo chown -R dbadmin:verticadba /opt/vertica/config/https_certs && "
                "sudo chmod 755 /opt/vertica/config/https_certs && "
                "sudo find /opt/vertica/config/https_certs -type f -name '*.key' -exec chmod 600 {} \\; && "
                "sudo find /opt/vertica/config/https_certs -type f -name '*.pem' -exec chmod 644 {} \\; && "
                "sudo chmod 600 /opt/vertica/config/https_certs/httpstls.json"
            )
            rc, _, err = self._ssh(ip, extract_cmd, sudo=False, timeout=60)
            if rc != 0:
                print(f"    FAILED to install certs: {err}")
                all_deployed = False
            else:
                print(f"    SUCCESS")

        return all_deployed

    def start_nma_services(self) -> bool:
        """Start NMA services on all nodes"""
        print(f"\nStarting NMA services on {len(self.instance_ips)} nodes...")

        all_success = True
        for ip in self.instance_ips:
            print(f"  Starting NMA on {ip}...")

            # Ensure standard HTTPS cert directory exists with correct ownership.
            # Certs are deployed earlier by generate_tls_material_no_ssh; do not
            # overwrite them with fallback files.
            cert_setup_cmd = (
                f"mkdir -p /opt/vertica/config/https_certs && "
                f"chown dbadmin:verticadba /opt/vertica/config/https_certs && "
                f"chmod 755 /opt/vertica/config/https_certs && "
                f"chown -R dbadmin:verticadba /opt/vertica/config/https_certs 2>/dev/null || true && "
                f"chmod 600 /opt/vertica/config/https_certs/*.key 2>/dev/null || true && "
                f"chmod 644 /opt/vertica/config/https_certs/*.pem /opt/vertica/config/https_certs/*.json 2>/dev/null || true"
            )
            self._ssh(ip, cert_setup_cmd, sudo=True, timeout=60)

            # Start NMA using the Vertica-supported script
            start_cmd = (
                f"sudo -u dbadmin /opt/vertica/bin/manage_node_agent.sh start node_management_agent"
            )
            rc, out, err = self._ssh(ip, start_cmd, sudo=True, timeout=60)
            if rc != 0:
                print(f"    WARNING: NMA start may have issues: {err}")
                print(f"    Output: {out.strip()}")
            else:
                print(f"    NMA start script executed")

            # Wait a moment for NMA to initialize
            time.sleep(5)

            # Verify NMA health by checking the HTTPS endpoint from inside the node
            check_cmd = (
                f"curl -fsSk https://localhost:5554/v1/health 2>&1 || "
                f"(sleep 3 && curl -fsSk https://localhost:5554/v1/health 2>&1) || "
                f"echo 'NMA_NOT_HEALTHY'"
            )
            rc, out, err = self._ssh(ip, check_cmd, sudo=True, timeout=30)
            if 'healthy' in out.lower():
                print(f"    NMA is healthy")
            else:
                print(f"    WARNING: NMA may not be running. Output: {out.strip()} {err.strip()}")
                all_success = False

        return all_success

    def create_eon_database(self) -> bool:
        """Create or Revive Eon Mode database using vcluster"""
        if not self.instance_ips:
            print("ERROR: No instance IPs available")
            return False

        if not self.instance_private_ips:
            print("ERROR: No instance private IPs available")
            return False

        if not self.communal_storage:
            print("ERROR: Communal storage location not configured")
            return False

        primary_ip = self.instance_ips[0]
        hosts = ",".join(self.instance_private_ips)

        action = "Create" if self.db_init == "create" else "Revive"
        cmd_action = "create_db" if self.db_init == "create" else "revive_db"

        print(f"\n{action} Eon Mode database '{self.db_name}'...")
        print(f"  Primary node: {primary_ip}")
        print(f"  Hosts: {hosts}")
        print(f"  Communal storage: {self.communal_storage}")
        print(f"  Mode: {self.db_init.upper()}")

        if self.db_init == "create":
            print(f"  Shard count: {self.shard_count}")
        print(f"  Depot path: {self.depot_path}")
        print(f"  Depot size: {self.depot_size}")

        # Build vcluster command
        cmd_parts = [
            "vcluster",
            cmd_action,
            "--db-name", self.db_name,
            "--hosts", hosts,
            "--catalog-path", "/data/catalog",
            "--data-path", "/data/vertica",
            "--communal-storage-location", self.communal_storage,
            "--depot-path", self.depot_path,
            "--depot-size", self.depot_size,
        ]

        # Only add shard-count for create (not revive - existing count is used)
        if self.db_init == "create":
            cmd_parts.extend(["--shard-count", str(self.shard_count)])

        # Add password if configured (vcluster uses --password, not --username)
        if self.admin_password:
            cmd_parts.extend(["--password", self.admin_password])

        # Add license file path
        license_dest = "/opt/vertica/config/licensing/license.xml"
        cmd_parts.extend(["--license", license_dest])

        # Add AWS config parameters
        # IAM role (default): no AWS auth flags; vcluster uses instance metadata service.
        # Access keys: pass AWSAuth via --config-param.
        # Env vars: pass --get-aws-credentials-from-env-vars flag.
        if self.s3_auth_mode == "access_keys" and self.aws_access_key and self.aws_secret_key:
            cmd_parts.extend([
                "--config-param",
                f"AWSAuth={self.aws_access_key}:{self.aws_secret_key}"
            ])
        elif self.s3_auth_mode == "env_vars":
            cmd_parts.append("--get-aws-credentials-from-env-vars")
        # For "iam_role" (or unset), rely on instance profile / IMDS and pass nothing.

        cmd_parts.extend([
            "--config-param",
            f"AWSRegion={self.aws_region},AWSEnableHttps={1 if self.aws_enable_https else 0}"
        ])

        # Add certificate files only if no install_vertica-generated bootstrap certs exist.
        # When install_vertica generates the full TLS material, vcluster defaults to
        # /opt/vertica/config/https_certs/{vertica_https,rootca}.{pem,key}, which is
        # exactly what we want.  Passing explicit --cert-file/--key-file overrides the
        # defaults and can fail if the filenames differ.
        if self.generate_certs:
            bootstrap_cert_exists_cmd = (
                "test -f /opt/vertica/config/https_certs/vertica_https.pem "
                "&& echo GENERATED || echo MISSING"
            )
            rc, out, _ = self._ssh(primary_ip, bootstrap_cert_exists_cmd, sudo=False, timeout=30)
            if rc != 0 or "GENERATED" not in out:
                print("  WARNING: install_vertica bootstrap cert not found; falling back to generated cert file args")
                cmd_parts.extend([
                    "--cert-file", "/opt/vertica/config/https_certs/vertica_https.pem",
                    "--key-file", "/opt/vertica/config/https_certs/vertica_https.key"
                ])
            else:
                print("  Using install_vertica-generated bootstrap certs")

        # Skip package install (we already installed)
        cmd_parts.append("--skip-package-install")

        # Force removal of pre-existing database directories if they exist
        cmd_parts.append("--force-removal-at-creation")

        vcluster_cmd = " ".join(cmd_parts)

        # Build a redacted command for display only
        display_cmd_parts = [p if p != self.admin_password else "***" for p in cmd_parts]
        display_cmd = " ".join(display_cmd_parts)

        # Vertica commands must run as the dbadmin OS user.
        # Write the command to a script and execute it with su to avoid quoting hell.
        # When install_vertica generates the TLS material, vcluster can use the
        # default bootstrap cert locations.  Passing explicit --cert-file / --key-file
        # overrides those defaults, so we omit them here and let vcluster pick the
        # standard names (vertica_https.pem/key in /opt/vertica/config/https_certs).
        script_content = f"""#!/bin/bash
set -e
export PATH=$PATH:/opt/vertica/bin
echo "Running as user: $(whoami), uid: $(id -u), groups: $(id -G)"
/opt/vertica/bin/{vcluster_cmd}
"""
        script_path = "/tmp/vcluster_create_db.sh"
        with open(script_path, "w") as f:
            f.write(script_content)

        print(f"\n  Executing vcluster command...")
        print(f"  Command: /opt/vertica/bin/{display_cmd}")

        # Upload script to primary node
        rc, _, err = self._scp(script_path, f"{script_path}", primary_ip)
        if rc != 0:
            print(f"  ERROR: Failed to upload vcluster script: {err}")
            return False

        # Make executable and run as dbadmin
        exec_cmd = f"chmod +x {script_path} && sudo su - dbadmin -c '{script_path}'"
        rc, out, err = self._ssh(primary_ip, exec_cmd, timeout=600)

        print(f"\n  Output:\n{out}")
        if err:
            print(f"  Errors:\n{err}")

        if rc == 0:
            print(f"\n  SUCCESS: Database '{self.db_name}' {self.db_init}d successfully")

            # For CREATE mode, sync catalog to ensure data is persisted to communal storage.
            # vcluster already synchronizes catalog during create_db; this is an extra safety step.
            if self.db_init == "create":
                print("\n  Syncing catalog to communal storage...")
                sync_sql = "SELECT sync_catalog();"
                sync_cmd = (
                    f"set -x; "
                    f"echo 'DEBUG: running as user $(whoami) on host $(hostname)'; "
                    f"echo 'DEBUG: primary_ip={primary_ip} db_name={self.db_name} user={self.admin_username}'; "
                    f"su - dbadmin -c \"timeout 60 /opt/vertica/bin/vsql -h {primary_ip} -U {self.admin_username} "
                    f"-d {self.db_name} -w '{self.admin_password}' -X -c '{sync_sql}'\""
                )
                print(f"  DEBUG sync command: {sync_cmd}")
                rc_sync, out_sync, err_sync = self._ssh(primary_ip, sync_cmd, timeout=130)
                print(f"  DEBUG sync rc={rc_sync}")
                print(f"  DEBUG sync stdout: {out_sync.strip()}")
                print(f"  DEBUG sync stderr: {err_sync.strip()}")
                if rc_sync == 0:
                    print("  Catalog synced successfully")
                    print("  IMPORTANT: Data is now persisted to S3 communal storage")
                else:
                    print(f"  WARNING: Catalog sync returned non-zero exit code {rc_sync}")

            # Give the database a moment to finish recovery before verification
            print("  Waiting 15 seconds for database to stabilize...")
            time.sleep(15)

            return True
        else:
            print(f"\n  FAILED: Database {self.db_init} failed with exit code {rc}")
            return False

    def verify_database(self) -> bool:
        """Verify database is running and accessible"""
        if not self.instance_ips:
            return False

        primary_ip = self.instance_ips[0]

        print(f"\nVerifying database '{self.db_name}'...")
        print(f"  DEBUG: primary_ip={primary_ip} user={self.admin_username} db_name={self.db_name}")

        # Check if database is up (force TCP via node IP; avoids AWS local socket issues)
        version_sql = "SELECT version();"
        check_cmd = (
            f"set -x; "
            f"echo 'DEBUG: running as user $(whoami) on host $(hostname)'; "
            f"su - dbadmin -c \"timeout 60 /opt/vertica/bin/vsql -h {primary_ip} -U {self.admin_username} "
            f"-d {self.db_name} -w '{self.admin_password}' -X -c '{version_sql}'\""
        )
        print(f"  DEBUG version command: {check_cmd}")
        rc, out, err = self._ssh(primary_ip, check_cmd, timeout=130)
        print(f"  DEBUG version rc={rc}")
        print(f"  DEBUG version stdout: {out.strip()}")
        print(f"  DEBUG version stderr: {err.strip()}")

        if rc == 0 and "Vertica" in out:
            print(f"  Database is running!")
            print(f"  Version: {out.strip()}")

            # Check nodes
            nodes_sql = "SELECT * FROM nodes;"
            nodes_cmd = (
                f"set -x; "
                f"su - dbadmin -c \"timeout 60 /opt/vertica/bin/vsql -h {primary_ip} -U {self.admin_username} "
                f"-d {self.db_name} -w '{self.admin_password}' -X -c '{nodes_sql}'\""
            )
            print(f"  DEBUG nodes command: {nodes_cmd}")
            rc, out, err = self._ssh(primary_ip, nodes_cmd, timeout=130)
            print(f"  DEBUG nodes rc={rc}")
            print(f"  DEBUG nodes stdout: {out.strip()}")
            print(f"  DEBUG nodes stderr: {err.strip()}")
            if rc == 0:
                print(f"\n  Nodes:\n{out}")
            else:
                print(f"  Node query failed: {err.strip()}")

            return True
        else:
            print(f"  Database verification failed")
            print(f"  Output: {out.strip()}")
            print(f"  Error: {err.strip()}")
            return False

    def run(self, rpm_path: str, license_path: str = "") -> bool:
        """
        Execute full Eon Mode installation workflow.

        Args:
            rpm_path: Path to Vertica RPM file
            license_path: Path to license file (optional but recommended)

        Returns:
            True if installation succeeded
        """
        print("=" * 70)
        print("Vertica Eon Mode Installation")
        print("=" * 70)
        print(f"Nodes: {len(self.instance_ips)}")
        print(f"Database: {self.db_name}")
        print(f"Communal Storage: {self.communal_storage}")
        print(f"Action: {self.db_init.upper()}")
        if self.db_init == "create":
            print(f"Shards: {self.shard_count}")
        print(f"Depot: {self.depot_path} ({self.depot_size})")
        print("=" * 70)

        if not self.instance_ips:
            print("ERROR: No target instances found.")
            print("Deploy infrastructure first with 'pulumi up' or specify --hosts")
            return False

        # Step 1: Upload RPM
        if not self.upload_rpm(rpm_path):
            print("\nWARNING: RPM upload had issues, continuing...")

        # Step 2: Upload license
        self.upload_license(license_path)

        # Step 3: Install Vertica RPM
        if not self.install_vertica(rpm_path, license_path):
            print("\nERROR: Vertica RPM installation failed. Aborting.")
            return False

        # Step 4: Generate TLS material locally (no node-to-node SSH) and deploy to nodes
        if not self.generate_tls_material_no_ssh():
            print("\nWARNING: TLS material generation had issues, continuing...")

        # Step 5: Start NMA services
        if not self.start_nma_services():
            print("\nWARNING: NMA service startup had issues, continuing...")

        # Step 6: Create Eon database
        if not self.create_eon_database():
            print("\nERROR: Database creation failed")
            return False

        # Step 7: Verify database
        if not self.verify_database():
            print("\nWARNING: Database verification failed")
            return False

        print("\n" + "=" * 70)
        print("Installation Complete!")
        print("=" * 70)
        print(f"Database: {self.db_name}")
        print(f"Action: {self.db_init.upper()}")
        print(f"Primary Node: {self.instance_ips[0]}")
        print(f"Communal Storage: {self.communal_storage}")
        if self.db_init == "create":
            print(f"Shards: {self.shard_count}")
        print(f"Nodes: {len(self.instance_ips)}")
        print("\nTo connect:")
        print(f"  vsql -U {self.admin_username} -d {self.db_name} -h {self.instance_ips[0]}")

        return True


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Install Vertica in Eon Mode on AWS EC2 instances"
    )
    parser.add_argument(
        "--config",
        help="Path to configuration YAML file"
    )
    parser.add_argument(
        "--hosts",
        help="Comma-separated list of host IPs (overrides config)"
    )
    parser.add_argument(
        "--ssh-key",
        help="Path to SSH private key (overrides config)"
    )
    parser.add_argument(
        "--ssh-user",
        default="ec2-user",
        help="SSH username"
    )
    parser.add_argument(
        "--rpm-path",
        help="Path to Vertica RPM file (or set vertica.rpm.local_path in config)"
    )
    parser.add_argument(
        "--license-path",
        help="Path to Vertica license file (or set vertica.license.local_path in config)"
    )
    parser.add_argument(
        "--communal-storage",
        help="S3 communal storage location (e.g., s3://bucket/path)"
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        help="Number of shards"
    )

    args = parser.parse_args()

    # Load configuration
    if args.config:
        config = load_config(args.config)
    else:
        config = {'vertica': {'eon': {}}, 'compute': {}}

    # Override with command line arguments
    if args.communal_storage:
        config['vertica']['eon']['communal_storage_location'] = args.communal_storage
    if args.shard_count:
        config['vertica']['eon']['shard_count'] = args.shard_count

    # Get SSH key
    ssh_key = args.ssh_key
    if not ssh_key:
        key_name = config.get('compute', {}).get('aws', {}).get('key_name', 'pulumi')
        ssh_key = f"~/.ssh/{key_name}.pem"

    # Get hosts
    if args.hosts:
        config['_instance_ips'] = [h.strip() for h in args.hosts.split(",")]

    installer = VerticaEonInstaller(
        config=config,
        ssh_key_path=ssh_key,
        ssh_user=args.ssh_user
    )

    # Override instance IPs if provided
    if args.hosts:
        installer.instance_ips = [h.strip() for h in args.hosts.split(",")]

    # Get RPM path from CLI or config
    rpm_path = args.rpm_path
    if not rpm_path:
        rpm_path = config.get('vertica', {}).get('rpm', {}).get('local_path', '')
        if not rpm_path:
            print("ERROR: --rpm-path not provided and vertica.rpm.local_path not set in config")
            sys.exit(1)
    rpm_path = os.path.expanduser(rpm_path)

    if not os.path.exists(rpm_path):
        print(f"ERROR: RPM file not found: {rpm_path}")
        sys.exit(1)

    # Get license path from CLI or config
    license_path = args.license_path
    if not license_path:
        license_path = config.get('vertica', {}).get('license', {}).get('local_path', '')
        if license_path:
            license_path = os.path.expanduser(license_path)

    success = installer.run(rpm_path, license_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
