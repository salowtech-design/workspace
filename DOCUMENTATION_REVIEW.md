# DOCUMENTATION REVIEW & SPECIFICATION FIDELITY AUDIT
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable B — Individual Assessment of Every Markdown Document & Specification

---

## 1. Documentation Overview

The repository contains **42 Markdown files**:
- **Root Directory**: 10 Stage 0 inspection reports and 1 master `BUILD_REPORT.md`.
- **`docs/` Directory**: 32 architectural, data model, security, runbook, and verification reports.

Every document was independently audited against the actual source code, tests, and configuration.

---

## 2. Individual Assessment of Authoritative Documents

### 2.1 `BUILD_REPORT.md` (Root & `docs/BUILD_REPORT.md`)
- **What It Specifies**: Consolidated Phase 1 & IAM v6.0 master build and verification report.
- **Claims Implemented**:
  - Milestones M0 through M6 implemented.
  - 243 passed automated tests in pytest.
  - All 7 E2E user journeys passed (`scripts/verify_e2e_journeys.py`).
  - All 7 distributed TiDB Cloud concurrency tests passed (`scripts/verify_tidb_concurrency.py`).
  - 1,000 active-user capacity simulation executed (`scripts/load_test_1000_users.py`).
  - Section 6 employee ID syntax parser and database role validation.
- **Supporting Source Files**:
  - `apps/accounts/auth/employee_id.py`, `apps/accounts/auth/audit.py`, `apps/accounts/views/admin_iam.py`, `tests/`.
- **Unverified / Exaggerated Claims**:
  - The "1,000 active users" claim: The underlying benchmark script (`scripts/load_test_1000_users.py`) runs in-process on SQLite with `django.test.Client`, bypassing network stack, TLS, and Gunicorn worker processes. It does NOT prove 1,000 concurrent HTTP users on TiDB Cloud.
- **Conflicts**: Mentions live TiDB Cloud for concurrency, but automated test suite defaults to SQLite (`config/settings/test.py`).
- **Security Implications**: High confidence in core logic, but capacity limits under real-world Gunicorn/TiDB HTTP traffic remain unmeasured.

### 2.2 `docs/ARCHITECTURE.md`
- **What It Specifies**: Modular monolith boundaries, Django application rules, trust boundaries.
- **Claims Implemented**:
  - Boundary rule: modules communicate via services and selectors; no direct cross-module model writes.
  - Pure Python face engine boundary (`apps/face/engine/`).
  - Untrusted browser model with server-side policy enforcement.
- **Supporting Source Files**:
  - `apps/face/engine/protocol.py`, `apps/face/engine/crypto.py`, `apps/accounts/permissions.py`.
- **Unverified / Inaccurate Claims**:
  - Claims HTMX + Alpine.js drive UI interactivity. Alpine.js is completely absent from the codebase; HTMX JavaScript (`htmx.min.js`) is never loaded in `base.html`.
- **Security Implications**: Clean architecture prevents circular dependencies, but missing frontend libraries cause client-side polling to fail.

### 2.3 `docs/DATA_MODEL.md`
- **What It Specifies**: Relational schema across accounts, attendance, certificates, core, directory, face, and posts.
- **Claims Implemented**:
  - `CustomUser` with `employee_id`, role, status, and Argon2id password hash.
  - `ActivationCode`, `TOTPCredential`, `RecoveryCode`, `DeviceApprovalRequest`, `SecurityAlert`.
  - `WorkSession`, `AttendanceCorrection`, `Certificate`, `Post`, `AuditLog`.
- **Supporting Source Files**:
  - `apps/accounts/models.py`, `apps/attendance/models.py`, `apps/core/models.py`, `apps/face/models.py`.
- **Unverified Claims**:
  - None. All listed models exist and have applied database migrations.
- **Conflicts**: `CustomUser.employee_id` is marked immutable, conflicting with role promotions (e.g. promoting `EMP` to `MGR` locks the user out under Section 6 syntax enforcement).

### 2.4 `docs/PERMISSIONS.md`
- **What It Specifies**: Default-deny permission matrix across 7 roles (`super_admin`, `admin`, `security_admin`, `security_reviewer`, `manager`, `employee`, `intern`).
- **Claims Implemented**:
  - Permission constants defined in `apps/accounts/permissions.py`.
  - View decorators (`@require_permission`) and policy checkers (`has_permission`).
  - Sudo mode elevation requirement (10-minute active window).
- **Supporting Source Files**:
  - `apps/accounts/permissions.py`, `apps/accounts/auth/stepup.py`, `tests/unit/test_iam_admin_and_ui_corrections.py`.
- **Security Implications**: Default-deny access control is rigorously enforced at the view layer.

### 2.5 `docs/SECURITY_OPERATIONS.md`
- **What It Specifies**: Security operations architecture, append-only alerts, read-only reviewer role, live stream.
- **Claims Implemented**:
  - `SecurityAlert` model with 9 mandatory alert categories.
  - Dedicated `security_reviewer` role with read-only permissions (`security.events.read`).
  - Live stream endpoint `/auth/admin/iam/live-stream/`.
- **Supporting Source Files**:
  - `apps/accounts/auth/audit.py`, `apps/accounts/views/security_operations.py`, `apps/accounts/views/admin_iam.py`.
- **Unverified Claims**:
  - The live stream view does NOT maintain an open SSE connection; it emits a single batch of events and terminates, causing `iam-monitor.js` to reconnect every 3 seconds via error handling.

### 2.6 `docs/SESSION_SECURITY.md`
- **What It Specifies**: Session lifetime, cookie flags, 30-second window resumption.
- **Claims Implemented**:
  - 12-hour session lifetime (`SESSION_COOKIE_AGE = 43200`).
  - `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`.
  - Client-side visibility listener (`static/session-resumption.js`).
- **Supporting Source Files**:
  - `config/settings/base.py`, `static/session-resumption.js`, `templates/base.html`.
- **Unverified Claims / Architectural Risk**:
  - The 30-second visibility listener redirects users to the login screen upon switching tabs for >30s, even though the server session is still valid for 12 hours. Unsaved form state is lost.

### 2.7 `docs/RUNBOOK.md`
- **What It Specifies**: Operational procedures, TiDB backups, key rotation drills, incident containment.
- **Claims Implemented**:
  - Runbook documentation exists with explicit command-line steps.
- **Unverified Claims**:
  - Automated key rotation drills and automated backup verification scripts are not implemented in code (manual CLI only).

### 2.8 Specialized IAM Audit Files in `docs/`
- `docs/IAM_CAPACITY_BENCHMARK_RESULTS.md`: Documents Stage A-E load tests; benchmark used in-memory SQLite instead of TiDB Cloud.
- `docs/IAM_DEMO_END_TO_END_ACCEPTANCE.md`: Documents manual walkthroughs; supported by `scripts/verify_e2e_journeys.py`.
- `docs/IAM_SECURITY_OPERATIONS_VERIFICATION.md`: Documents alert generation; supported by unit tests in `tests/unit/test_security_operations_spec.py`.
- `docs/IAM_TIDB_INTEGRATION_VERIFICATION.md`: Documents live TiDB Cloud Serverless tests; supported by `scripts/verify_tidb_concurrency.py`.

---

## 3. Discovered Documentation Conflicts

1. **Role Marker Normalization vs Employee ID Immutability**:
   - `docs/DATA_MODEL.md` enforces that `CustomUser.employee_id` is permanently immutable after creation.
   - `Phase 1 & IAM v6.0 (Section 6)` requires employee IDs to encode roles (e.g. `ALICE-EMP-01`) and rejects login if the ID marker does not match the assigned role in the database.
   - **Conflict**: If an employee is promoted from `employee` to `manager`, their assigned role in the database becomes `manager`, but their `employee_id` cannot be changed due to immutability. Consequently, `validate_employee_id_role_consistency` will permanently lock out the employee upon role promotion (`AUTH-SEC-001`).
2. **Database Engine: TiDB Cloud vs SQLite**:
   - `docs/ARCHITECTURE.md` and `ADR-0001` specify TiDB Cloud Serverless as the authoritative database engine.
   - `config/settings/test.py` configures `django.db.backends.sqlite3` (`test_db.sqlite3`).
   - All 243 pytest tests execute against SQLite, masking distributed TiDB transaction and row-level locking behavior during normal CI runs. Only `scripts/verify_tidb_concurrency.py` connects directly to TiDB Cloud.
3. **Frontend Interactivity Contract**:
   - Documentation specifies HTMX + Alpine.js.
   - Source code contains zero Alpine.js code, and `base.html` lacks `htmx.min.js`.

---
*Deliverable B complete. All 42 Markdown documents assessed individually against real codebase evidence.*
