# IMPLEMENTATION RISK REGISTER & DEFECT LOG
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable I — Prioritized Technical, Security, Data-Integrity and Operational Risks

---

## 1. Prioritized Risk Register

| Risk ID | Title | Severity | Likelihood | Impact | Verified Source Evidence |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **RISK-01** | **Employee ID Role Promotion Lockout** | `CRITICAL` | High | Promoting an employee to Manager permanently locks them out with `AUTH-SEC-001`. | `CustomUser.employee_id` immutable in `apps/accounts/models.py:122-127`; role check in `apps/accounts/auth/employee_id.py:90-120`. |
| **RISK-02** | **Widespread `REMOTE_ADDR` & IP Spoofing** | `CRITICAL` | High | Rate limits bypassable via spoofed `X-Forwarded-For`; all IAM views record `127.0.0.1` behind reverse proxies. | `apps/core/middleware.py:101-108`, `apps/accounts/views/login.py:53`, `apps/accounts/auth/sessions.py:28`. |
| **RISK-03** | **Biometric Camera & Geolocation Block** | `HIGH` | High | Attendance check-in UI lacks camera element and fails if `DEV_VERIFIER=False`; geolocation is blocked by CSP. | `templates/attendance/panel.html:45`, `apps/attendance/views.py:65`, `apps/core/middleware.py:45`. |
| **RISK-04** | **Broken HTMX & SSE Live Monitoring** | `HIGH` | High | HTMX polling is inert because `htmx.min.js` is missing; SSE terminates immediately, causing 3s reconnect loops. | `templates/base.html:70-77`, `apps/accounts/views/admin_iam.py:751-775`, `static/iam-monitor.js:59-64`. |
| **RISK-05** | **Missing `SECURE_PROXY_SSL_HEADER`** | `HIGH` | High | SSL offloading at Cloudflare edge causes CSRF token failures and infinite HTTPS redirect loops. | Absent from `config/settings/prod.py`. |
| **RISK-06** | **Dead Domain Event Outbox Processor** | `MEDIUM` | Medium | Domain event outbox (`core_outboxevent`) has no background worker, Celery queue, or cron runner. | `apps/core/outbox.py:39` has no caller outside unit tests. |
| **RISK-07** | **SQLite Test Suite Masking TiDB Behavior** | `MEDIUM` | High | All automated tests run on SQLite, masking TiDB distributed transaction and locking semantics in CI. | `config/settings/test.py:25` configures `django.db.backends.sqlite3`. |
| **RISK-08** | **Aggressive 30s Tab-Switch Ejection** | `MEDIUM` | High | Switching browser tabs for >30s redirects authenticated users to login, discarding unsaved form data. | `static/session-resumption.js:13-16`. |
| **RISK-09** | **Employee ID 3-Part Prefix Parsing Flaw** | `LOW` | Medium | IDs with 3 parts starting with role (e.g. `EMP-BD-001`) fail to parse role marker, defaulting to segment 1. | `apps/accounts/auth/employee_id.py:77`. |

---

## 2. Detailed Defect Analysis & Remediation Plans

### RISK-01: Employee ID Role Promotion Lockout (CRITICAL)
- **Root Cause**: `CustomUser.employee_id` is immutable once saved. If an employee with ID `JANE-EMP-001` is promoted to `manager`, their role in the DB becomes `manager`. However, their `employee_id` cannot be modified. On login, `apps/accounts/auth/employee_id.py:validate_employee_id_role_consistency` checks if `manager` is in the allowed roles for marker `EMP`. It is not, raising `AUTH-SEC-001` and permanently locking the account.
- **Remediation**: Amend `CustomUser.save()` to allow administrative re-issuance of `employee_id` upon promotion, or update the consistency validator to allow promoted roles when an audited administrative promotion record exists.

### RISK-02: Widespread `REMOTE_ADDR` & IP Spoofing (CRITICAL)
- **Root Cause**:
  1. `RateLimitMiddleware._get_client_ip` extracts the leftmost IP from `X-Forwarded-For`. Clients can send `X-Forwarded-For: 1.2.3.4` to spoof arbitrary IPs and evade rate limiting.
  2. Multiple IAM views (`login.py`, `sessions.py`, `stepup.py`) bypass `_get_client_ip` entirely and read `request.META.get("REMOTE_ADDR")`. Behind a proxy (e.g. Cloudflare / Gunicorn), `REMOTE_ADDR` will always be `127.0.0.1`.
- **Remediation**: Create a centralized IP extraction middleware that reads `CF-Connecting-IP` or the rightmost trusted proxy IP, binds `request.client_ip`, and replace all `REMOTE_ADDR` calls across IAM.

### RISK-03: Biometric Camera & Geolocation Block (HIGH)
- **Root Cause**: `templates/attendance/panel.html` has no camera `<video>` or capture logic; `apps/attendance/views.py` passes empty frames to a stub engine. `apps/core/middleware.py` sets `Permissions-Policy: geolocation=()`, actively blocking GPS.
- **Remediation**: Implement a WebRTC camera capture component in `panel.html`, and update `Permissions-Policy` to `geolocation=(self)` if geofencing is required.

### RISK-04: Broken HTMX & SSE Live Monitoring (HIGH)
- **Root Cause**: Templates declare HTMX attributes, but `base.html` never loads `htmx.min.js`. The SSE generator in `admin_iam.py` terminates after one batch, causing `iam-monitor.js` to reconnect every 3s via error recovery.
- **Remediation**: Add `htmx.min.js` to `base.html` and convert the live event monitor to proper HTMX polling (`hx-trigger="every 15s"`), aligning with the Phase 1 specification.

### RISK-05: Missing `SECURE_PROXY_SSL_HEADER` in Production Settings (HIGH)
- **Root Cause**: Edge proxy SSL offloading is not recognized by Django.
- **Remediation**: Add `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` to `config/settings/prod.py`.

---
*Deliverable I complete. All risks prioritized with direct code citations and concrete remediation plans.*
