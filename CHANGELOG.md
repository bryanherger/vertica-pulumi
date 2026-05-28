# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- End-to-end documentation for add/remove nodes, version upgrades, backup/restore
- GitHub Actions CI/CD workflow for automated testing

## [1.0.0] - 2026-05-28

### Added

- **Eon Mode support** with `dbinit` configuration option (`Create` or `Revive`)
- `sync_catalog()` automation after database creation to prevent data loss
- Data persistence warnings and documentation
- Revive workflow: restore existing databases from S3 communal storage
- Detailed end-to-end test results documenting `sync_catalog()` requirement

### Fixed

- **Critical**: Data loss prevention - `sync_catalog()` now called automatically after Eon database creation
- Documentation gaps around Eon Mode data persistence behavior
- Added warnings about asynchronous S3 flushing in depot architecture

### Security

- Removed all hardcoded passwords (replaced with `CHANGE_ME_USE_STRONG_PASSWORD`)
- Added `.gitignore` rules to prevent committing credentials, certificates, configs
- Documented IAM instance profile preference over access keys
- Added S3 bucket lifecycle configuration for cost management

## [0.9.0] - 2026-05-28

### Added

- **Eon Mode deployment** with S3 communal storage
- `install_vertica_eon.py` script for automated Eon installation
- `generate_eon_config.py` interactive configuration generator
- `generate_nma_certs.py` certificate generation and deployment
- NMA (Node Management Agent) HTTPS setup
- Depot configuration (size, path)
- AWS credential handling for S3 access

### Changed

- Split documentation into EE and Eon mode guides
- Updated `README.md` with mode selection quick-start

## [0.8.0] - 2026-05-28

### Added

- **Enterprise Edition (EE)** mode deployment working end-to-end
- `install_vertica_ee.py` script for automated installation
- SSH file upload with retry logic
- `adminTools` integration for database creation
- License installation support

### Fixed

- SCP timeout issues with large RPM files
- `Output[T]` handling in Pulumi exports
- SSH readiness polling (up to 5 minutes)
- dbadmin user creation order

## [0.7.0] - 2026-05-27

### Added

- Initial Pulumi infrastructure deployment
- AWS EC2, VPC, subnet, security group provisioning
- EBS volume attachment for data storage
- `__main__.py` with configuration loading
- `aws_deployment.py` module
- Basic Vertica module structure

### Known Issues

- File upload via SCP is slow for large RPM files
- No retry logic for network failures
- Limited error handling
