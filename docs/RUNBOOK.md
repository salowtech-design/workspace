# Salow Workspace — Operations Runbook

## Emergency Procedures

### 1. Database Outage
- **Symptom**: `/ready` returns HTTP 503 Service Unavailable.
- **Action**: Check TiDB Cloud Console cluster status and network latency. Check TLS certificates.

### 2. Sudo Mode & Break-Glass
- Privileged operations (role elevation, certificate revocation, employee exits) require re-authentication.
- Break-glass administrator access is logged in `AuditLog` with action `core.break_glass.invoke`.
