# SPECIFICATION GAP & COMPLIANCE MATRIX
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main` (five rows re-read from source later, see below)  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable H — Requirement-by-Requirement Comparison Against Phase 1 v4.0 & IAM v6.0

---

## 1. Requirement Compliance Matrix

| Requirement Area | Specification Authority | Code Location | Status | Forensic Evidence & Gap Details |
| :--- | :--- | :--- | :---: | :--- |
| **Python & Django Stack** | Phase 1 v4.0 (Sec 3) | `pyproject.toml` | **VERIFIED** | Python 3.12.15, Django 5.2.19 LTS modular monolith. |
| **TiDB Cloud Database** | Phase 1 v4.0 (Sec 3) | `config/settings/prod.py` | **VERIFIED** | Connected to TiDB Serverless `v8.5.3` (`salow_workspce`) with pessimistic mode. |
| **First-Party IAM Platform** | IAM v6.0 (Sec 6) | `apps/accounts/` | **VERIFIED** | Central identity authority with 13-tile management grid. |
| **Dual Authentication** | IAM v6.0 (Sec 7) | `apps/accounts/views/login.py` | **VERIFIED** | Distinct flows for first-time activation (`flow=login`) and returning sign-in (`flow=signin`). |
| **Lockout Thresholds** | IAM v6.0 (Sec 7) | `apps/accounts/auth/lockout.py` | **VERIFIED** | 5 attempts for activation, 3 attempts for returning sign-in. |
| **RFC 6238 TOTP MFA** | IAM v6.0 (Sec 8) | `apps/accounts/auth/mfa_totp.py` | **VERIFIED** | AES-256-GCM encrypted secrets, anti-replay step tracker, Base64 SVG QR codes. |
| **Three Recovery Methods** | IAM v6.0 (Sec 8) | `apps/accounts/views/recovery.py`| **VERIFIED** | Method 1 (Admin dual-approval), Method 2 (Self-service 30s token), Method 3 (Authenticator replacement). |
| **Secondary Device Approval**| IAM v6.0 (Sec 9) | `apps/accounts/auth/device_approval.py` | **VERIFIED** | 60s challenge expiry, primary device approval or dual admin fallback, email OTP. |
| **Session Lifetime (12h)** | IAM v6.0 (Sec 10) | `config/settings/base.py` | **VERIFIED** | `SESSION_COOKIE_AGE = 43200`, `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`. |
| **30s Window Resumption** | IAM v6.0 (Sec 10) | `static/session-resumption.js` | **PARTIALLY VERIFIED**| Listener works, but redirects to login on >30s tab switch, discarding unsaved form state. |
| **Workday Synchronization** | Phase 1 v4.0 (Sec 11)| `apps/core/workday.py` | **VERIFIED** | Asia/Kolkata timezone; historical posts and attendance preserved across midnight. |
| **Attendance Midnight Rule**| Phase 1 v4.0 (Sec 11)| `apps/attendance/models.py` | **VERIFIED** | Open sessions crossing midnight remain a single continuous attendance record. |
| **SecOps Alerting (9 Cats)** | IAM v6.0 (Sec 13) | `apps/accounts/auth/audit.py` | **VERIFIED** | Real `SecurityAlert` records created across all 9 high-severity categories. |
| **Independent SecOps Reviewer**| IAM v6.0 (Sec 13)| `apps/accounts/views/security_operations.py` | **VERIFIED** | `security_reviewer` role has read-only access to triage and live event streams. |
| **Employee ID Role Marker** | IAM v6.0 (Sec 6) | `apps/accounts/auth/employee_id.py` | **CONFLICTING** | Syntax parsed, but role consistency check locks out promoted employees permanently. |
| **Biometric Face Capture** | Phase 1 v4.0 (Sec 17)| `templates/attendance/panel.html`| **PARTIALLY VERIFIED**| Re-read from source (not run here): the panel has a camera `<video>` element, a `getUserMedia` request, and copies the current frame into the form as a JPEG data URL before submit (the earlier "no camera, hardcoded empty frames" claim is out of date). Encryption and protocol rows above were not re-checked. Matching still uses the stub engine, see the Real Biometric Models row. |
| **Geolocation Enforcement**| Phase 1 v4.0 (Sec 17)| `apps/core/middleware.py` | **UNVERIFIED (changed)** | Re-read from source: the middleware now sets `Permissions-Policy: camera=(self), microphone=(), geolocation=(self)`, so the old claim that it blocks geolocation is out of date. A docstring at the top of that file still lists `geolocation=()`. Whether geolocation is actually enforced at check-in was not tested. |
| **HTMX Polling** | IAM v6.0 (Sec 13) | `templates/base.html` | **UNVERIFIED (changed)** | Re-read from source: `base.html` now loads `/static/htmx.min.js` (the file exists in `static/`). The earlier "not loaded" claim is out of date. Polling behaviour was not tested. |
| **Live Stream (SSE)** | IAM v6.0 (Sec 13) | `apps/accounts/views/admin_iam.py` | **CONFLICTING** | Stream emits a finite slice and closes immediately; client reconnects every 3s via error handling. |
| **1,000-User Capacity** | IAM v6.0 (Sec 23) | `scripts/load_test_1000_users.py`, `tests/browser/load_http.py` | **NOT VERIFIED** | The old script is an in-process SQLite test. `tests/browser/load_http.py` is a bounded diagnostic over real HTTP on a throwaway TiDB 8.5.1 unistore, run at 10, 50 and 100 simulated users on one shared 1 CPU box with stored sessions. It is not a capacity test and says nothing about 1,000 users, TiDB Cloud or a multi-worker deploy. It also surfaced an open TiDB 8133 read error on chat space under concurrent inserts (unistore only so far). |
| **Real Biometric Models** | Phase 1 v4.0 (Sec 17)| `apps/face/engine/` | **REQUIRES HUMAN DECISION**| Re-read from source: no `.onnx` model files in the repo, and the engine folder has a `stub.py`. Model choice, licensing and consent for real models remain open. |

---

## 2. Note on the original counts

The percentage table and the closing line from the baseline audit were removed. The counts are not valid for the rows re-read above, and
the other rows were not re-checked in this pass. Only the five rows named above were re-read, from source, without running them.
This file makes no claim that the product is complete or production ready.
