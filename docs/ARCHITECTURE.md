# Technical Architecture

## Overview

The Vertica Cluster Infrastructure project uses a layered architecture to provide cloud-agnostic database deployment and management.

## Architecture Layers

```
┌─────────────────────────────────────────┐
│         User Configuration               │
│    (YAML files, Pulumi config)          │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│       Deployment Orchestration           │
│  (High-level deployment automation)     │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│      Compute Abstraction Layer          │
│  (AWS, Azure, GCP, Bare Metal)          │
│                                         │
│  ┌────────┐ ┌────────┐ ┌────────┐     │
│  │  AWS   │ │ Azure  │ │ Bare   │     │
│  │  EC2   │ │   VM   │ │ Metal  │     │
│  └────────┘ └────────┘ └────────┘     │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│     Vertica Provisioning Layer          │
│                                         │
│  ┌────────────┐  ┌────────────────┐    │
│  │  vcluster  │  │    REST API    │    │
│  │    CLI     │  │   (Monitoring) │    │
│  └────────────┘  └────────────────┘    │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│      Cluster Management Layer           │
│  (Scaling, Health, Backup)              │
└─────────────────────────────────────────┘
```

## Key Components

### Compute Abstraction Layer

**Purpose**: Provide unified interface for different infrastructure providers.

**Design Pattern**: Strategy Pattern with Abstract Base Class

**Key Classes**:
- `ComputeProvider` (ABC) - Base interface
- `AWSComputeProvider` - AWS EC2 implementation
- `BareMetalProvider` - Existing hardware import

**Benefits**:
- Same Vertica provisioning code works on any provider
- Easy to add new providers (Azure, GCP)
- Consistent SSH execution and file transfer

### Vertica Provisioning Layer

**Purpose**: Install and configure Vertica database software.

**Key Classes**:
- `VClusterManager` - Wraps vcluster CLI
- `VerticaInstaller` - Package installation
- `VerticaConfigurator` - Post-install configuration
- `VerticaRestApi` - HTTP API client

**Workflow**:
1. Upload installer to nodes
2. Install Vertica packages
3. Create database using vcluster
4. Apply configuration settings
5. Verify installation

### Cluster Management Layer

**Purpose**: Ongoing cluster operations.

**Key Classes**:
- `ClusterScaler` - Add/remove nodes
- `ClusterHealthMonitor` - Status monitoring
- `ClusterManager` - Combined operations

## Data Flow

### Deployment Flow

```
User Config → Pulumi → Compute Provider → Cloud API
                                              │
                                              ▼
                                    Infrastructure Created
                                              │
                                              ▼
                                    Bootstrap Script (cloud-init)
                                              │
                                              ▼
                                    Vertica Installation
                                              │
                                              ▼
                                    Database Configuration
```

### Scaling Flow

```
Scale Request → ClusterScaler → VClusterManager → vcluster CLI
                                                    │
                                                    ▼
                                         Vertica Nodes Added/Removed
                                                    │
                                                    ▼
                                         Compute Resources Updated
```

## Configuration Management

Configuration is handled through multiple layers:

1. **Default Configuration** - Built into code
2. **Config File** - YAML file with user settings
3. **Pulumi Config** - Stack-specific overrides
4. **Environment Variables** - Runtime overrides
5. **Secrets** - Encrypted sensitive data

Priority (highest first):
- Pulumi secrets
- Pulumi config
- Environment variables
- Config file
- Defaults

## Error Handling

- **Provisioning Failures**: Rollback to previous state
- **Partial Failures**: Mark resources for cleanup
- **Health Check Failures**: Alert and attempt recovery
- **Connection Issues**: Retry with exponential backoff

## Security Considerations

1. **SSH Keys**: Stored in Pulumi secrets
2. **Database Passwords**: Encrypted in config
3. **Network**: Security groups restrict access
4. **SSL/TLS**: Supported for REST API
5. **Encryption**: EBS volumes encrypted by default

## Extensibility

### Adding a New Compute Provider

1. Create new file in `modules/compute/`
2. Inherit from `ComputeProvider`
3. Implement all abstract methods
4. Register in `__init__.py`

### Adding New Vertica Operations

1. Add methods to `VClusterManager`
2. Update REST API client if applicable
3. Add tests

## Performance Considerations

- **Parallel Operations**: Install Vertica on multiple nodes simultaneously
- **Lazy Connections**: SSH connections established only when needed
- **Output Streaming**: Real-time output for long-running operations

## Monitoring and Observability

- **Pulumi Outputs**: Resource IDs and endpoints
- **CloudWatch**: AWS metrics (if configured)
- **Vertica REST API**: Database-specific metrics
- **Custom Health Checks**: Application-level monitoring

## Future Enhancements

1. **Multi-Region Support**: Deploy across AWS regions
2. **Auto-Scaling**: Based on CPU/memory metrics
3. **Disaster Recovery**: Automated backup/restore
4. **Kubernetes**: EKS/GKE deployment option
5. **GUI**: Web-based management interface
