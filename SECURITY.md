# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

**Please do not file public issues for security vulnerabilities.**

Instead, please report vulnerabilities by emailing: **security@seocho-project.org**

Include:
- Description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact
- Suggested fix (if any)

We will respond within 48 hours and work with you to coordinate disclosure.

## Security Best Practices

### Data Protection
- All data lineage information is encrypted at rest
- Neo4j connections use TLS encryption
- Sensitive configuration values use environment variables
- No hardcoded credentials in source code

### Container Security
- Use official base images only
- Regular security scanning with Trivy
- Minimal container images (distroless where possible)
- Non-root user execution

### Network Security
- All services communicate over internal Docker networks
- No exposed ports beyond necessary endpoints
- Rate limiting on API endpoints
- Input validation on all data ingestion points

### Access Control
- Default credentials must be changed in production
- Role-based access for DataHub
- Neo4j authentication enabled
- Regular access reviews

## Security Scanning

### Automated Checks
- GitHub Actions security scanning on PRs
- Docker image vulnerability scanning
- Dependency vulnerability checks (Dependabot)
- CodeQL static analysis

### Manual Reviews
- Security review required for new data connectors
- Penetration testing before major releases
- Security architecture review for new features

## Compliance

Seocho is designed to support:
- SOC 2 Type II compliance
- GDPR data lineage requirements
- HIPAA audit trails
- ISO 27001 information security management

## Security Configuration Examples

### Production Hardening
```yaml
# docker-compose.prod.yml
services:
  dozerdb:
    environment:
      - NEO4J_dbms_security_procedures_unrestricted=gds.*,apoc.*
      - NEO4J_dbms_security_auth__minimum__password__length=12
      - NEO4J_dbms_logs_security__level=DEBUG
```

### TLS Configuration
```bash
# Generate certificates
openssl req -x509 -newkey rsa:4096 -keyout neo4j.key -out neo4j.crt -days 365 -nodes
```

## Incident Response

1. **Detection**: Security alerts from monitoring tools
2. **Assessment**: Impact analysis and severity classification
3. **Containment**: Isolate affected systems
4. **Recovery**: Patch and restore services
5. **Post-incident**: Review and improve security measures

## Security Updates

Security patches are released as:
- **Critical**: Within 24 hours
- **High**: Within 72 hours
- **Medium**: Next scheduled release
- **Low**: Quarterly security updates

Subscribe to security announcements: security@seocho-project.org