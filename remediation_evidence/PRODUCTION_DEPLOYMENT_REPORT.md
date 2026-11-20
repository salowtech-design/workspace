# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Production Deployment and Container Architecture Report (Workstream I)

- **Document ID**: `REMED-PROD-DEPLOY-01`
- **Date**: 20 November 2026
- **Status**: PASSED (Container Stack) / BLOCKED (Public DNS & TLS Termination)
- **Security Priority**: P1

---

### 1. Production WSGI Server Stack

In accordance with Workstream I, the single-threaded development server was replaced with a hardened production WSGI stack using **Gunicorn 23.x**:

```dockerfile
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
```

#### Engineering Rationale:
- **Concurrency**: 4 worker processes with 2 threads per worker provide 8 concurrent request slots, preventing thread exhaustion during password hashing (Argon2id).
- **Timeouts**: 60s worker timeout prevents stuck database queries from consuming process slots indefinitely; 30s graceful timeout allows in-flight transactions to commit before worker recycling.
- **Structured Logging**: Direct stdout/stderr streaming captures access and error logs for container aggregation (CloudWatch, Datadog).

---

### 2. Multi-Service Container Architecture (`docker-compose.yml`)

The production deployment divides workloads into dedicated containers:
1. **`web` Service**:
   - Runs Gunicorn WSGI handling incoming HTTP requests from the reverse proxy.
   - Preserves `Host` headers (`app.salowtech.com`, `iam.salowtech.com`).
   - Mounts static files and media assets.
2. **`worker` Service**:
   - Runs the background transactional outbox consumer:
     `python manage.py process_email_outbox --loop --interval 10`
   - Decoupled from web request lifecycles.
   - Reclaims stale leases and dispatches security alerts.

---

### 3. Static Assets & Security Headers

- Static assets are compiled and collected using `collectstatic`.
- Production security middleware enforces:
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Content-Security-Policy`: Default restrictive directive set preventing inline script injection.

---

### 4. External Dependency Blocker Classification

- **Status**: **BLOCKED — EXTERNAL DEPENDENCY**
- **Blockers**:
  1. **Public DNS Delegation**: DNS A/CNAME records mapping `app.salowtech.com` and `iam.salowtech.com` to the edge load balancer / ingress controller.
  2. **Production TLS / SSL Certificates**: Valid Let's Encrypt or enterprise CA certificates terminated at the reverse proxy.
- **Remediation**: The Gunicorn application server, Docker image, Compose topology, WSGI entry points, and worker daemons are 100% complete and operational. Cloud DNS and edge ingress will be provisioned by the operations team.
