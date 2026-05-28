#!/bin/bash
# Vertica Cluster Bootstrap Script
# This script runs on each node during initial setup

set -e

echo "=== Vertica Node Bootstrap ==="

# Determine OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    VER=$VERSION_ID
else
    echo "Cannot determine OS"
    exit 1
fi

echo "OS: $OS $VER"

# Install common dependencies
echo "Installing dependencies..."
if [[ "$OS" == *"Amazon Linux"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
    dnf update -y
    dnf install -y wget curl net-tools dialog psmisc lsof
elif [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
    apt-get update
    apt-get install -y wget curl net-tools dialog psmisc lsof
fi

# System configuration for Vertica
echo "Configuring system for Vertica..."

# Kernel parameters
cat >> /etc/sysctl.conf << 'EOF'
vm.max_map_count=262144
vm.swappiness=1
kernel.shmmax=2147483648
kernel.shmall=536870912
EOF
sysctl -p

# User limits
cat >> /etc/security/limits.conf << 'EOF'
# Vertica requirements
vertica soft nofile 65536
vertica hard nofile 65536
vertica soft nproc 65536
vertica hard nproc 65536
vertica soft core unlimited
vertica hard core unlimited
vertica soft memlock unlimited
vertica hard memlock unlimited

# dbadmin user (Vertica default admin)
dbadmin soft nofile 65536
dbadmin hard nofile 65536
dbadmin soft nproc 65536
dbadmin hard nproc 65536
EOF

# Create vertica user if not exists
if ! id "vertica" &> /dev/null; then
    useradd -m -s /bin/bash vertica
fi

# Create dbadmin user if not exists
if ! id "dbadmin" &> /dev/null; then
    useradd -m -s /bin/bash dbadmin
fi

# Create data directories
echo "Creating data directories..."
mkdir -p /data/vertica /data/catalog /data/depot
chown -R dbadmin:verticadba /data
chmod 755 /data
chmod 700 /data/vertica /data/catalog /data/depot

# Disable SELinux (if enforcing)
if command -v getenforce &> /dev/null; then
    if [ "$(getenforce)" == "Enforcing" ]; then
        echo "Disabling SELinux..."
        setenforce 0
        sed -i 's/SELINUX=enforcing/SELINUX=permissive/g' /etc/selinux/config
    fi
fi

# Configure firewall
if command -v firewall-cmd &> /dev/null; then
    echo "Configuring firewall..."
    systemctl enable firewalld
    systemctl start firewalld
    
    # Vertica ports
    firewall-cmd --permanent --add-port=5433/tcp  # Client port
    firewall-cmd --permanent --add-port=5434/tcp  # Spread
    firewall-cmd --permanent --add-port=5444/tcp  # REST API
    firewall-cmd --permanent --add-port=4803/tcp  # Spread
    firewall-cmd --permanent --add-port=4804/tcp  # Spread
    firewall-cmd --permanent --add-port=6543/tcp  # Agent
    
    firewall-cmd --reload
fi

# Setup SSH key for dbadmin (if key provided)
if [ -n "$VERTICA_SSH_KEY" ]; then
    echo "Setting up SSH key for dbadmin..."
    mkdir -p /home/dbadmin/.ssh
    echo "$VERTICA_SSH_KEY" > /home/dbadmin/.ssh/authorized_keys
    chmod 700 /home/dbadmin/.ssh
    chmod 600 /home/dbadmin/.ssh/authorized_keys
    chown -R dbadmin:dbadmin /home/dbadmin/.ssh
fi

# Mount additional volumes if present
for disk in /dev/nvme1n1 /dev/xvdb /dev/sdb; do
    if [ -b "$disk" ]; then
        echo "Found additional disk: $disk"
        
        # Check if already mounted
        if ! mountpoint -q /data; then
            # Create filesystem if needed
            if ! file -s "$disk" | grep -q filesystem; then
                mkfs.ext4 "$disk"
            fi
            
            # Mount to /data
            echo "$disk /data ext4 defaults,noatime 0 0" >> /etc/fstab
            mount /data
        fi
        
        break
    fi
done

# Log completion
echo "Bootstrap complete at $(date)" > /var/log/vertica-bootstrap.log

echo "=== Bootstrap Complete ==="
