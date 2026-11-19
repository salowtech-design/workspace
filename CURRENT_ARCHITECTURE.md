# CURRENT ARCHITECTURE RECONSTRUCTION REPORT
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable C — Architecture Reconstructed from Actual Source Code

---

## 1. High-Level System Architecture

Salow Workspace is implemented as a **Python/Django Modular Monolith**. It contains 7 first-party applications unified under a single Django project root, backed by TiDB Cloud Serverless in production and SQLite in testing.

```
                              [ Browser / Client ]
                                        │
                         HTTPS / TLS (Cloudflare Edge)
                                        │
                         [ Gunicorn WSGI Web Server ]
                                        │
           ┌────────────────────────────┴────────────────────────────┐
           ▼                                                         ▼
[ apps/core/middleware.py ]                                [ apps/core/middleware.py ]
  - SecurityHeadersMiddleware                                - RateLimitMiddleware
    (CSP, nosniff, DENY)                                       (Sliding window counters)
           │                                                         │
           └────────────────────────────┬────────────────────────────┘
                                        ▼
                         [ Django Modular Monolith ]
  ├── apps/accounts/      (First-party IAM, Auth, MFA, Sessions, RBAC, SecOps)
  ├── apps/core/          (AuditLog, OutboxEvent, EmailOutbox, Workday Logic)
  ├── apps/attendance/    (Work sessions, check-in/out, fallback queue)
  ├── apps/directory/     (Departments, teams, reporting relationships)
  ├── apps/face/          (Biometric consent, AES-256 encrypted templates)
  ├── apps/posts/         (Daily work logs, standup reports, comment feeds)
  └── apps/certificates/  (Achievement credentials, HMAC-SHA256, public portal)
           │
           ├─► TiDB Cloud Serverless (MySQL 8.0 Protocol, TiKV Raft Storage)
           └─► In-Memory Face Engine (CPU-based, zero raw frame/vector storage)
```

---

## 2. Technology Stack & Framework Components

- **Programming Language**: Python `3.12.15`
- **Web Framework**: Django `5.2.19 LTS`
- **Database Adapters**:
  - Production: `django_tidb` (`mysqlclient` / MySQL 8.0 compatible, TiDB Serverless `v8.5.3`)
  - Test / CI: `django.db.backends.sqlite3` (`test_db.sqlite3` / in-memory)
- **Cryptography**: `cryptography` (AES-256-GCM), `argon2-cffi` (Argon2id password hashing), `pyotp` (RFC 6238 TOTP), `hashlib` (HMAC-SHA256)
- **Frontend Presentation**: Django Server-Side Templates, hand-crafted CSS tokens (`static/css/tokens.css`, `static/css/components.css`)
- **Client Scripting**: Vanilla JavaScript (`static/session-resumption.js`, `static/iam-monitor.js`, `static/sw.js`)

---

## 3. Middleware Pipeline & Execution Order

Configured in `config/settings/base.py` (lines 33–44):

1. `django.middleware.security.SecurityMiddleware`: Standard Django HTTPS redirects and HSTS headers.
2. `apps.core.middleware.SecurityHeadersMiddleware`:
   - `Content-Security-Policy`: `default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: camera=(self), microphone=(), geolocation=()`
3. `apps.core.middleware.RateLimitMiddleware`:
   - Sliding-window in-memory rate limiting.
   - 60 requests/minute on sensitive auth paths (`/auth/login/`, `/auth/activate/`, `/auth/recovery/`).
   - 300 requests/minute on general endpoints.
   - Extracts client IP; currently evaluates leftmost `X-Forwarded-For`.
4. `django.contrib.sessions.middleware.SessionMiddleware`: Manages database-backed HTTP sessions (`django_session`).
5. `django.middleware.common.CommonMiddleware`: URL canonicalization and slash normalization.
6. `django.middleware.csrf.CsrfViewMiddleware`: Enforces CSRF token validation on POST/PUT/DELETE.
7. `django.contrib.auth.middleware.AuthenticationMiddleware`: Binds `request.user` from session backend.
8. `apps.accounts.middleware.AccountSecurityMiddleware`:
   - Checks account status; immediately logs out suspended (`CustomUser.Status.SUSPENDED`) or exited accounts.
9. `django.contrib.messages.middleware.MessageMiddleware`: Cookie/session-based user flash messages.
10. `django.middleware.clickjacking.XFrameOptionsMiddleware`: Backup clickjacking defense.

---

## 4. URL Routing & Boundary Segregation

Configured in `config/urls.py`:

- `/auth/`: Central IAM subsystem routes (Login, activation, MFA, recovery, device approval, SecOps).
  - `/auth/admin/iam/`: Central IAM management console (Restricted to `SUPER_ADMIN` and `ADMIN`).
  - `/auth/admin/shield/`: Administrator Security Shield (Multi-factor step-up verification).
  - `/auth/admin/iam/live-stream/`: Server-Sent Events security event stream.
  - `/auth/security/`: Employee self-service security management dashboard.
- `/attendance/`: Employee check-in/out and personal work session history.
  - `/attendance/admin/`: Workspace business administration (Attendance reviews, manual fallback approvals).
- `/posts/`: Daily standup logs, blocker submissions, team feeds, and discussion threads.
- `/certificates/`: Administrative certificate issuance and PDF previews.
- `/verify/`: Public, unauthenticated certificate verification portal (`X-Robots-Tag: noindex`).
- `/health`: Application process liveness probe (HTTP 200).
- `/ready`: Authoritative database connection readiness probe (Executes `SELECT 1`).

---

## 5. Security & Session State Management

- **Session Architecture**:
  - Backed by `django.contrib.sessions.backends.db` (`django_session` table).
  - Sudo mode elevation is stored in `request.session[SUDO_GRANTED_TIMESTAMP_KEY]` (`apps/accounts/auth/stepup.py`), making it shared across multi-worker Gunicorn nodes.
  - Absolute session lifetime: 12 hours (`SESSION_COOKIE_AGE = 43200`).
  - Browser close expiration: `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`.
- **Client-Side Resumption**:
  - `static/session-resumption.js` monitors window visibility via `document.visibilitychange`.
  - If the tab is hidden for > 30 seconds, it redirects to the login screen on return (`window.location.href = '/auth/login/?flow=signin&next=' + nextRoute`).
- **Biometric Processing Boundary**:
  - Pure Python protocol in `apps/face/engine/protocol.py`.
  - AES-256-GCM encryption for stored templates in `FaceTemplate.encrypted_template`.
  - In-memory execution; raw camera images and 128-dimensional embedding vectors are never written to disk, database, or logs.

---

## 6. Background Workers & Outbox Infrastructure

- **Transactional Outbox (`OutboxEvent`)**:
  - Domain events are atomically written to `core_outboxevent` within business database transactions.
  - Processing logic exists in `apps/core/outbox.py:process_outbox_batch()`.
  - **Defect Identified**: No Celery, background worker process, or scheduled cron job executes `process_outbox_batch()`.
- **Transactional Email Outbox (`EmailOutbox`)**:
  - Outgoing emails are spooled in `core_emailoutbox` with AES-256-GCM encrypted payloads.
  - CLI command available: `python manage.py process_email_outbox` (`apps/core/management/commands/process_email_outbox.py`).

---

## 7. Architectural Anomalies & Technical Deviations

1. **HTMX Polling Inertia**:
   - Templates (`iam_dashboard.html`, `approval_wait.html`) declare `hx-get` and `hx-trigger="every 15s"`.
   - `templates/base.html` **does not load `htmx.min.js`**, rendering all HTMX polling inactive at runtime.
2. **SSE Stream Premature Termination**:
   - `admin_iam_live_stream_view` in `apps/accounts/views/admin_iam.py` does not maintain an open event stream. It queries a finite slice of events, yields them, and closes the response. `iam-monitor.js` handles this closure as an error and reconnects every 3 seconds.
3. **Hardcoded `REMOTE_ADDR` in Authentication Views**:
   - Multiple views (`login.py`, `sessions.py`, `stepup.py`) read `request.META.get("REMOTE_ADDR")` directly instead of resolving client IP through reverse proxies. When deployed behind Cloudflare, all client IPs will appear as the local proxy IP.
4. **Geolocation Blocked by Security Headers**:
   - `SecurityHeadersMiddleware` sets `Permissions-Policy: geolocation=()`, causing all client geolocation queries to fail.

---
*Deliverable C complete. Architecture fully reconstructed from source code without speculation.*
