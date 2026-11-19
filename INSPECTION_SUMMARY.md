# STAGE 0 FORENSIC INSPECTION SUMMARY & STRATEGIC ROADMAP
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable J — Executive Synthesis & Strategic Remediation Plan

---

## 1. Executive Summary

This Stage 0 Forensic Inspection was conducted as a strict, read-only investigation to determine the exact state of the Salow Workspace and IAM v6.0 platform prior to beginning Stage 1 implementation. Every claim in historical reports was independently audited against the actual source code, tests, database artifacts, and runtime configuration.

The inspection reveals a platform with **sophisticated cryptographic and security foundations**, but characterized by **several critical operational bugs, client-side anomalies, and unbenchmarked production capacity**.

### Quantitative Summary Scorecard
- **Total Tracked Source Files**: **282 files** (563 physical files on disk excluding `.git/` and `.venv/`).
- **Core Applications**: 7 first-party apps (`accounts`, `core`, `attendance`, `face`, `directory`, `posts`, `certificates`).
- **Database Migrations**: 37 migrations (all 100% applied in `test_db.sqlite3` and verified on TiDB Cloud).
- **Unit Test Suite**: 24 test files, **243 test cases** (100% pass in pytest).
- **Connected Production Database**: TiDB Serverless `v8.5.3` (`salow_workspce`) on AWS `ap-southeast-1` with 45 migrated tables.

---

## 2. What Genuinely Exists (Verified & Functional Assets)

1. **Robust Cryptographic Core (`apps/accounts/auth/crypto.py`)**:
   - AES-256-GCM symmetric encryption for database column protection (TOTP secrets, biometric templates, email outbox payloads).
   - Argon2id password hashing with multi-validator blacklist checks.
   - 10-character Crockford Base32 token generator avoiding ambiguous alphanumeric characters.
2. **Central IAM Platform & 13-Tile Management Grid**:
   - Central identity authority with 13 functional operational subsystems accessible at `/auth/admin/iam/`.
   - Administrative segregation strictly separating business administration (`/attendance/admin/`) from Central IAM.
3. **Dual Authentication & Account Recovery**:
   - Distinct first-time account activation (`flow=login`) and returning sign-in (`flow=signin`) portals.
   - Three distinct recovery methods: Admin reset with dual-superadmin approval, self-service reset with 30s crawler-safe token, and authenticator replacement.
4. **Device Trust & Secondary Device Verification**:
   - Secondary device verification with strict 60-second expiration, out-of-band email OTP, and biometric gating.
5. **Workday Synchronization & Midnight Attendance**:
   - Asia/Kolkata business date calculation preserving historical records and supporting continuous midnight-crossing sessions.
6. **Security Operations & Independent Reviewer**:
   - Append-only `SecurityAlert` model capturing 9 high-severity categories with a dedicated read-only `security_reviewer` role.

---

## 3. Critical Defects & Operational Deficiencies

1. **Employee ID Role Promotion Lockout (CRITICAL)**:
   - `CustomUser.employee_id` is permanently immutable, but Section 6 syntax enforcement rejects logins where the ID role marker does not match the database-assigned role. Promoting an employee permanently locks them out (`AUTH-SEC-001`).
2. **Widespread `REMOTE_ADDR` Hardcoding & Leftmost IP Spoofing (CRITICAL)**:
   - Rate limiting extracts the leftmost `X-Forwarded-For` header, enabling IP spoofing. Authentication views read raw `REMOTE_ADDR`, recording `127.0.0.1` behind reverse proxies.
3. **Biometric UI Capture Fiction & Geolocation Block (HIGH)**:
   - The attendance check-in UI has no camera element and submits empty frames to a stub verifier. Geolocation is actively blocked by `Permissions-Policy: geolocation=()`.
4. **Inert HTMX Polling & Broken SSE Stream (HIGH)**:
   - `templates/base.html` never loads `htmx.min.js`, leaving HTMX triggers inert. The SSE live stream terminates after a single query slice, triggering 3-second reconnect loops.
5. **Missing `SECURE_PROXY_SSL_HEADER` in Production Settings (HIGH)**:
   - Missing configuration in `config/settings/prod.py` risks CSRF failures and redirect loops behind Cloudflare.
6. **Unbenchmarked 1,000-User Capacity (MEDIUM)**:
   - The claimed 1,000-user capacity test was an in-process SQLite simulation with 25 threads bypassing real password/TOTP cryptography. Real HTTP capacity on TiDB Cloud remains unverified.

---

## 4. Proposed Stage 1 Remediation Roadmap

Upon approval of this Stage 0 report, the following prioritized work packages should be executed in Stage 1:

1. **Work Package 1 (Critical IAM Fixes)**:
   - Amend `CustomUser.save()` and `apps/accounts/auth/employee_id.py` to allow audited administrative employee ID re-issuance upon role promotion.
   - Implement centralized proxy IP extraction middleware (`request.client_ip`) reading `CF-Connecting-IP` or the rightmost trusted proxy, and add `SECURE_PROXY_SSL_HEADER`.
2. **Work Package 2 (Frontend & Real-Time Monitoring)**:
   - Include `htmx.min.js` in `templates/base.html` and convert live security event monitoring to proper HTMX polling (`hx-trigger="every 15s"`).
   - Refactor `session-resumption.js` to preserve form drafts or present an in-page modal before redirecting.
3. **Work Package 3 (Attendance & Biometrics)**:
   - Implement WebRTC camera capture in `templates/attendance/panel.html` and update `Permissions-Policy` to permit geolocation if geofencing is required.
4. **Work Package 4 (Background Outbox & Live Capacity Testing)**:
   - Add a scheduled management command for domain event outbox processing (`process_outbox_batch`).
   - Execute a real HTTP-level load test against the running Gunicorn/TiDB Cloud stack.

---

## 5. Mandatory Stopping Condition

In strict accordance with Section 15 of the Stage 0 Directive:

**INSPECTION IS COMPLETE. ALL WORK HAS STOPPED.**

- No source files have been modified.
- No database records have been altered.
- No migrations have been run.
- Awaiting human review and formal approval of these inspection findings and the Stage 1 remediation plan before proceeding.
