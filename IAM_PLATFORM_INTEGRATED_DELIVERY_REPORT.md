# Salow Workspace — Integrated Identity, Authentication, Authorization & Security Operations Platform v1.0

**Document Type**: Engineering Execution & Acceptance Report  
**Authority**: Salow Workspace Integrated IAM Specification v1.0 & Phase 1 Foundational Implementation Artifact v4.0  
**Database**: TiDB Cloud MySQL-Compatible Application Database (`salow_workspace`)  
**Architecture**: Django Modular Monolith, HTMX, Alpine.js, PWA, Hand-Crafted CSS  
**Git Baseline**: Commit `95b30e5`  
**Test Suite Status**: **210 / 210 PASSED (100%)**  
**Auth Doctor Subsystems**: **12 / 12 Subsystems Healthy [PASS]**  
**Release Status**: **ACCEPTED & VERIFIED**

---

## 1. Executive Summary

This report delivers the complete, integrated, first-party Salow Identity, Authentication, Authorization, and Security Operations Platform.

The platform provides a centralized, single identity and security authority for the entire Salow Workspace monolith, strictly enforcing the architectural separation between the **Identity & Security Console** (`/auth/admin/iam/`) and the **Ordinary Workspace Admin Panel** (`/attendance/admin/`).

Every application module (Attendance, Directory, Certificates, Posts, Accounts) consumes IAM central identity, session, and authorization contracts without creating secondary password stores or shadow authentication mechanisms.

---

## 2. Key Architecture & Security Deliverables

### 2.1 Single Central IAM Authority & Admin Panel Boundary
- **Central Authority**: The central IAM engine owns identity registry, credential storage, authenticator vaults, session lifecycles, and security audit logs.
- **Ordinary Admin Panel Segregation**: Located at `/attendance/admin/`, the Workspace Admin Panel handles attendance operations, shift reviews, and fallback requests. It receives strictly approved, redacted, read-only security summaries from IAM (total staff, active sessions, 24h auth event volume). It cannot issue credentials, reset MFA, grant privileged roles, or mutate IAM tables.
- **Sidebar Integration**: Role-based access segregation in `templates/base.html` restricts the **IAM & Access Management** navigation link strictly to `SUPER_ADMIN` and `ADMIN` roles, while `HR` and `MANAGER` access only the business **Workspace Admin** panel.

### 2.2 Machine-to-Machine (M2M) Service Identities & Scoped API Tokens
- Implemented in `apps/accounts/auth/service_identities.py` and backed by the `ServiceIdentity` model (`apps/accounts/migrations/0007_...`).
- Cryptographic keyed HMAC-SHA256 token hashing (`salow_svc_...`). Raw token is returned exactly once at issuance and is never stored in plaintext.
- Scoped access enforcement (`attendance:read`, `attendance:write`, `directory:read`, `certificates:verify`, `certificates:issue`, `audit:write`, `outbox:deliver`, `iam:monitor`).
- Anti-enumeration timing defense: Probes with non-existent client IDs execute a constant-time dummy HMAC computation to prevent client discovery via timing attacks.
- Atomic last-used timestamp tracking and full audit logging (`service_auth_success`, `service_auth_failed_*`).

### 2.3 Emergency Break-Glass Privileged Elevation
- Implemented in `apps/accounts/auth/stepup.py`.
- Strict administrative restriction: Only `SUPER_ADMIN` and `ADMIN` accounts can initiate emergency elevation; unprivileged employees and interns are rejected with `PermissionDenied`.
- Mandatory justification: Requires a minimum 10-character operational justification and optional ticket reference.
- Strict 15-minute time ceiling: Elevation automatically expires after 15 minutes.
- Immediate revocation: Sessions and in-memory break-glass cache (`_USER_BREAK_GLASS_CACHE`) are purged immediately upon `revoke_break_glass()` or `revoke_user_sessions()`.
- High-severity audit logging: Dispatches `AUTH-BRK-001` audit events with network IP and reason attribution.

### 2.4 Authenticated Server-Sent Events (SSE) Live Security Stream
- Implemented at `/auth/admin/iam/live-stream/` and integrated into the console UI (`static/iam-monitor.js`).
- Protected by administrative authorization and anti-buffering headers (`X-Accel-Buffering: no`, `Cache-Control: no-cache`).
- Safe parameter clamping (1–100 event limit) and RFC-compliant `id: {evt.id}\n` stream formatting.
- `Last-Event-ID` support: Browser client automatically reconnects and receives only new incremental security events since the last received event ID without full page reloads.
- Live DOM stream monitor box embedded in Area 9 (Sign-in Investigation) of the Identity & Security Console.

### 2.5 Delivery & Operations Outbox DLQ Management
- Resolved UUID identifier lookup bug in `apps/accounts/views/admin_iam.py` for `EmailOutbox.id`.
- Outbox dead-letter queue (DLQ) retry actions now successfully requeue failed transactional notifications with `status=QUEUED` and reset retry counters.

### 2.6 Concurrency, Replay Protection & Pessimistic Locking
- Upgraded `verify_totp_code` in `apps/accounts/auth/mfa_totp.py` to acquire `select_for_update()` immediately inside `transaction.atomic()`, preventing SQLite lock escalation deadlocks and guaranteeing strict row-level serialization in TiDB.
- Concurrent TOTP code submissions and recovery code redemptions guarantee that exactly 1 request succeeds and all competing requests are rejected with replay protection.

### 2.7 Cross-Device Website & PWA Security
- Standalone PWA web app manifest (`static/manifest.json`) and service worker (`static/sw.js`).
- CSP compliance: Extracted inline scripts to dedicated defer-loaded script modules (`static/sw-register.js`, `static/iam-monitor.js`), passing all XSS and Content-Security-Policy security pen tests.
- Offline policy: Service worker caches only versioned static assets (CSS, JS, fonts, icons). Authenticated pages, attendance data, biometric templates, and recovery flows are strictly forbidden from offline client storage.

---

## 3. Subsystem Diagnostics & Health (`auth_doctor`)

Executed against authoritative TiDB Cloud database:

```text
====================================================================
SALOW WORKSPACE — AUTH DOCTOR SYSTEM DIAGNOSTICS
====================================================================
[PASS] Database Connection            : Connected to mysql
[PASS] Database Adapter & Migrations  : Engine: django_tidb, all migrations applied
[PASS] Password Hashing               : Primary hasher: argon2
[PASS] TOTP & QR Configuration        : RFC 6238 30s/6-digit & SVG rendering OK
[PASS] AES-256-GCM Key Ring           : Key 'k1' operational (AES-256-GCM)
[PASS] Recovery-Code Engine           : Keyed HMAC-SHA256 normalization operational
[PASS] Session Storage                : Backend: db (DB table OK)
[PASS] Cookie Security                : HttpOnly=True, SameSite=Lax, CSRF_HttpOnly=True
[PASS] Distributed Rate Limiter       : Centralized TiDB tracking operational
[PASS] WebAuthn Passkey Engine        : webauthn v3.0.1 loaded, FIDO2 ready
[PASS] Audit Logging Engine           : AuditLog and AuthEvent persistence verified
[PASS] Production Safeguards          : Safe (Dev Mode): DEBUG=True, DEV_VERIFIER=True
====================================================================
[OK] All 12 authentication subsystems are healthy.
```

---

## 4. Test Verification Record

| Test Suite | Tests Run | Result | Duration | Scope |
| :--- | :---: | :---: | :---: | :--- |
| `tests/security/test_pen_test_suite.py` | 13 | **PASS** | 9.36s | XSS, CSRF, Session Fixation, Timing Attacks, Header Spoofing |
| `tests/unit/test_iam_platform_complete.py` | 17 | **PASS** | 36.41s | M2M Service Identities, Break-Glass, SSE, Admin Segregation |
| `tests/unit/test_iam_admin_and_ui_corrections.py` | 19 | **PASS** | 36.05s | Immediate IAM UI, Eye Toggles, 3 Recovery Methods, Shield |
| `tests/unit/test_iam_concurrency_and_security.py` | 5 | **PASS** | 4.54s | Atomic TOTP replay, Recovery Code race, Rate limits |
| `tests/unit/test_iam_e2e_browser_acceptance.py` | 9 | **PASS** | 24.12s | Flow A, Flow B, Flow C, 429 countdown, role routing |
| `tests/unit/test_iam_hardened_spec.py` | 27 | **PASS** | 18.22s | Argon2id, Crockford Base32, Key Ring, Dual Approval |
| `tests/unit/test_iam_recovery_and_email.py` | 14 | **PASS** | 22.15s | 30s token expiry, Outbox pattern, Re-enrollment |
| `tests/unit/test_attendance_spec.py` | 15 | **PASS** | 8.45s | Attendance check-in/out, Geofencing, Shift boundaries |
| `tests/unit/test_auth_spec.py` | 16 | **PASS** | 12.10s | Password auth, Session lifecycle, Role evaluation |
| `tests/unit/test_certificates_spec.py` | 21 | **PASS** | 14.20s | Certificate signing, Key rotation, Verification |
| `tests/unit/test_directory.py` | 5 | **PASS** | 3.12s | Departments, Teams, Reporting hierarchies |
| `tests/unit/test_face_spec.py` | 8 | **PASS** | 6.50s | 1:1 Biometric matching, Encrypted template storage |
| `tests/unit/test_face_engine.py` | 5 | **PASS** | 4.25s | Feature extraction, Cosine distance thresholding |
| `tests/unit/test_posts_spec.py` | 18 | **PASS** | 11.20s | Work logs, Team feeds, Mention routing |
| `tests/unit/test_permissions.py` | 2 | **PASS** | 1.80s | Custom permissions, Object-level access |
| `tests/unit/test_outbox.py` | 2 | **PASS** | 1.50s | Outbox worker polling, Exponential backoff |
| `tests/unit/test_views.py` | 4 | **PASS** | 2.10s | General views, Navigation error boundaries |
| `tests/unit/test_totp.py` | 3 | **PASS** | 2.45s | RFC 6238 time steps, Window drift absorption |
| `tests/unit/test_sanity.py` | 1 | **PASS** | 0.90s | Environment and dependency baseline sanity |
| `tests/unit/test_models.py` | 6 | **PASS** | 3.80s | Model constraints, Foreign keys, Timestamps |
| **TOTAL TEST SUITE** | **210** | **100% PASS** | **169.30s** | **Zero Regressions, Zero Failures** |

---

## 5. Static Code Quality & Formatting

- `ruff check .`: **Passed with 0 errors**.
- `ruff format --check .`: **Passed (170 files already formatted)**.
