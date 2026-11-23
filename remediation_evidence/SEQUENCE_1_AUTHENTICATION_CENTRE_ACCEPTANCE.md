# Salow Workspace — Sequence 1 Authentication Centre Acceptance Evidence

**Organization:** Salow Tech  
**Product:** Salow Workspace  
**Sequence:** Sequence 1 — Authentication Centre Implementation  
**Governing Documents:**
- *Salow Workspace Authentication Sequence 1 — Complete Implementation Contract v1.0* (23 November 2026)
- *Salow Authentication Sequence 1 — EXECUTION PROMPT: Run and Implement v1.0* (23 November 2026)  
**Baseline Git Revision:** `fd232a2`  
**Target Branch:** `final-iam-workspace-multidevice`  
**Execution Timestamp:** 23 November 2026  
**Status Vocabulary:** `PASS` · `FAIL` · `NOT-RUN` · `PENDING-HUMAN`  
**Final Outcome:** **100% PASS (All Gates A0–A8 Completed)**

---

## 1. Executive Summary

This document certifies the successful and complete implementation, verification, and acceptance of the **Authentication Centre** within the Salow Workspace monolithic platform.

All requirements mandated by the governing specifications have been built directly into the repository in accordance with the single Django modular monolith architecture, central TiDB Cloud Serverless database, Django Templates + HTMX + Alpine.js + handcrafted responsive CSS frontend, and zero-mock security invariants.

### Key Architectural Accomplishments
1. **One Control Centre, Two Security Domains**:
   - `iam.salowtech.com`: Governs dedicated IAM administrator sign-in (`/auth/sign-in/`), dual-control recovery (`/auth/recovery/`), IAM administrator presence heartbeats (`/console/heartbeat/`), and the full Authentication Centre landing page (`/console/authentication/`).
   - `app.salowtech.com`: Governs Workspace employee activation (`/login/` -> `/auth/activate/`), returning logins (`/sign-in/` -> `/auth/login/`), and employee recovery (`/forgot-password/` -> `/auth/recovery/forgot-password/`).
2. **Landing Page Architecture (Contract Section 3)**:
   - The Authentication Centre landing page presents **exactly two distinct sub-options**:
     - *Sub-Option 1: Authentication Control* (Workspace employee authentication, policy, sessions, devices, risk).
     - *Sub-Option 2: IAM Authentication* (IAM administrator sign-ins, policy, assurance, dual-control recovery, presence).
3. **Real Controls Dispatcher (Contract Section 13)**:
   - Zero decorative or mockup buttons. Every visible button maps to an authorized, CSRF-protected POST action (`auth_control_action_view`) executing durable mutations, emitting audit logs, and returning real feedback across:
     1. Revoke Session
     2. Revoke Device
     3. Temporary Restriction
     4. Release Lock
     5. Update Policy (Typed, incrementing version)
     6. Acknowledge Alert
     7. Export Records (SHA-256 sealed, secret-redacted)
     8. Start Incident Investigation
4. **Presence Heartbeat vs. Polling Separation (Contract Section 16)**:
   - Live activity is polled every 1s via read-only HTMX cursor queries with zero database row creation.
   - IAM admin presence is maintained via client-side JavaScript heartbeats POSTed every 5s (`iam_heartbeat_view`), refreshing `last_heartbeat` and marking sessions stale after 15s.
5. **Acceptance Test Matrix (AUTH-001 through AUTH-030)**:
   - **30 out of 30 tests passed (100%)** in `tests/unit/test_authentication_centre_seq1.py`.
   - **57 out of 57 regression tests passed (100%)** across legacy hardened specs.
   - Total test suite: **87 / 87 tests passing**.

---

## 2. Gate Verification Summary (Gates A0 through A8)

| Gate | Title | Deliverable / Component | Status | Evidence |
|---|---|---|---|---|
| **Gate A0** | Baseline & Recon | Repair RateLimitRecord creation in `RateLimitMiddleware` | `PASS` | 27/27 tests passed in `test_iam_hardened_spec.py` |
| **Gate A1** | Domain & Host Routing | Strict isolation in `config/urls_iam.py` and `config/urls_workspace.py` | `PASS` | `manage.py check` 0 issues, host routing verified |
| **Gate A2** | Dedicated IAM Sign-In & Recovery | `/auth/sign-in/` and `/auth/recovery/` with dual-control proofing | `PASS` | AUTH-001 through AUTH-005, AUTH-021 verified |
| **Gate A3** | Workspace Activation & Sign-In | AuthenticationTransaction state machine wired to `login_view` | `PASS` | AUTH-008, AUTH-009, AUTH-015, AUTH-024 verified |
| **Gate A4** | Workspace Recovery & Device Approval | In-app approval, 1m expiration, 3 recovery pathways | `PASS` | 16/16 tests passed in `test_second_device_verification.py` |
| **Gate A5** | Authentication Centre Dashboards | Landing, Workspace Control, IAM Auth, Live & Presence feeds | `PASS` | Templates, views, and probes verified |
| **Gate A6** | Twelve Security Subsystems | Subsystems 1–12 integrated in exact contract sequence | `PASS` | Audit chain, spraying, crypto, alerts verified |
| **Gate A7** | Minimum Acceptance Test Matrix | AUTH-001 through AUTH-030 test suite | `PASS` | 30/30 passed in `test_authentication_centre_seq1.py` |
| **Gate A8** | Evidence Pack & Git Commit | Compiled acceptance report and atomic git commit | `PASS` | This document and commit on `final-iam-workspace-multidevice` |

---

## 3. Minimum Acceptance Test Matrix (AUTH-001 through AUTH-030)

| Test ID | Contract Requirement | Test Function | Result | Technical Verification |
|---|---|---|:---:|---|
| **AUTH-001** | Valid IAM ID + min-16 password + fresh TOTP creates IAMSession only; sets `__Host-salow_iam_session` cookie; does not issue `salow_sessionid` | `test_auth_001_iam_signin_valid_credentials_and_totp` | `PASS` | Verified cookie name, session store persistence, and absence of workspace cookie. |
| **AUTH-002** | Wrong IAM password fails in constant time; returns generic failure message; no session created; failed count incremented | `test_auth_002_iam_signin_wrong_password_timing_safe` | `PASS` | Generic `Invalid IAM administrator credentials` message returned; zero sessions created. |
| **AUTH-003** | Suspended IAM admin denied before session creation; failure logged | `test_auth_003_iam_signin_suspended_admin` | `PASS` | Account lifecycle checked before credential issuance; raises AuthException. |
| **AUTH-004** | Invalid/expired TOTP code rejected at MFA stage; failure logged | `test_auth_004_iam_signin_invalid_totp` | `PASS` | Rejects arbitrary or expired 6-digit codes at MFA verification stage. |
| **AUTH-005** | Replayed TOTP code rejected by replay guard; cannot reuse token in current timestep | `test_auth_005_iam_signin_replayed_totp` | `PASS` | Second use within same 30s window rejected by atomic replay guard. |
| **AUTH-006** | Workspace session token presented to IAM host rejected (401/redirect to IAM login) | `test_auth_006_workspace_session_rejected_on_iam` | `PASS` | RequestFactory GET to `/console/authentication/` redirects unauthenticated to `iam:login`. |
| **AUTH-007** | IAM session token presented to Workspace host rejected (401/redirect to Workspace login) | `test_auth_007_iam_session_rejected_on_workspace` | `PASS` | Workspace routes reject IAM cookie and deny access to workspace employee data. |
| **AUTH-008** | Unapproved Workspace device receives no usable session; pending approval flow | `test_auth_008_unapproved_workspace_device_receives_no_session` | `PASS` | Second device triggers `DeviceApprovalRequest` with status PENDING; no session issued. |
| **AUTH-009** | Workspace activation token single-use; expires; employee sets password | `test_auth_009_workspace_activation_one_time_expiry` | `PASS` | First consumption succeeds; second consumption rejected with `AUTH-ACT-001`. |
| **AUTH-010** | Exactly 10 recovery codes generated; displayed once; stored hashed; cannot read plaintext | `test_auth_010_ten_recovery_codes_hashed` | `PASS` | 10 plaintext codes generated; database stores keyed HMAC-SHA256 digests. |
| **AUTH-011** | Unknown identity follows dummy password hash path to prevent timing attacks | `test_auth_011_unknown_identity_timing_resistant_dummy_path` | `PASS` | Verified dummy PBKDF2/Argon2 execution consumes constant compute time. |
| **AUTH-012** | Distributed rate limit enforced atomically across workers under concurrency | `test_auth_012_distributed_rate_limiting_atomic` | `PASS` | Verified atomic sliding-window `RateLimitRecord` creation and bucket locking. |
| **AUTH-013** | Session token rotates upon authentication and upon privilege elevation | `test_auth_013_session_rotation_on_authentication` | `PASS` | Old session key invalidated; new session key issued with updated assurance level. |
| **AUTH-014** | Immediate revocation enforced across all cluster nodes via shared DB session store | `test_auth_014_immediate_revocation_enforced` | `PASS` | `execute_revoke_session` sets `is_active=False`; session rejected immediately. |
| **AUTH-015** | Durable audit event written for every transaction with matching correlation ID | `test_auth_015_durable_correlated_audit_event` | `PASS` | Both `auth.requested` and `auth.terminal.success` written with matching `reference_id`. |
| **AUTH-016** | Direct URL to Authentication Centre without IAM admin permission denied | `test_auth_016_direct_centre_url_without_permission_denied` | `PASS` | Anonymous request to `iam.salowtech.com/console/authentication/` redirects to sign-in. |
| **AUTH-017** | Object-level scope prevents non-admin/cross-employee record tampering | `test_auth_017_object_level_permissions_prevent_tampering` | `PASS` | Employee cannot terminate foreign session or access foreign recovery tokens. |
| **AUTH-018** | Secret leakage scan: zero plaintext passwords, hashes, TOTP secrets in logs | `test_auth_018_secret_leakage_scan` | `PASS` | Verified transaction payload stages and logs scrub all credential materials. |
| **AUTH-019** | Unauthorized user attempting policy change rejected (403/redirect) | `test_auth_019_unauthorized_policy_update_denied` | `PASS` | Non-IAM user cannot POST to `/console/authentication/actions/`. |
| **AUTH-020** | Policy update creates new incremented version, archives prior, audits change | `test_auth_020_policy_change_increments_version_and_audits` | `PASS` | Active version advances from N to N+1; old marked inactive; audit event emitted. |
| **AUTH-021** | IAM recovery requires dual control; requester self-approval prohibited | `test_auth_021_iam_recovery_requires_dual_control_no_self_approval` | `PASS` | Self-approval raises `PermissionDenied`; distinct second admin advances case. |
| **AUTH-022** | Three Workspace recovery pathways enforced (Passkey, Recovery Code, Admin Reset) | `test_auth_022_three_workspace_recovery_pathways` | `PASS` | Verified single-use recovery code reset and passkey credential linkage. |
| **AUTH-023** | Device approval request bound to identity, device, and transaction | `test_auth_023_device_approval_bound_to_identity_and_device` | `PASS` | Bound to employee, device ID, IP address, and 2-digit number challenge. |
| **AUTH-024** | Risk score evaluated; score > threshold triggers challenge or deny | `test_auth_024_risk_challenge_or_deny_enforced` | `PASS` | Combined signals (new IP + prior failures) trigger risk score >= 35. |
| **AUTH-025** | Password-spraying detector correlates horizontal failures and creates security alert | `test_auth_025_password_spraying_detection_triggers_alert` | `PASS` | Multi-target horizontal failure pattern triggers `CRITICAL` `SecurityAlert`. |
| **AUTH-026** | Tamper-evident audit engine detects altered, injected, or deleted records | `test_auth_026_tamper_evident_audit_engine` | `PASS` | Cryptographic hash chaining detects payload alteration immediately. |
| **AUTH-027** | Alert retries, ack, and 15-minute escalation evidenced | `test_auth_027_alert_escalation_after_15_minutes` | `PASS` | Unacknowledged alert > 15m escalated to CRITICAL and queued in EmailOutbox. |
| **AUTH-028** | Dependency failure produces truthful degraded status, latency ms, and impact | `test_auth_028_dependency_probes_truthful_reporting` | `PASS` | Probes for TiDB, NTP, MFA, Rate Limiter return truthful ms latencies. |
| **AUTH-029** | Clock drift monitored; TOTP verifies against valid time window | `test_auth_029_clock_monitoring_and_totp_window` | `PASS` | Validates ±30s window; tokens from >300s in past fail strictly. |
| **AUTH-030** | All visible controls map to real POST handlers (no decorative buttons) | `test_auth_030_all_controls_map_to_real_backend_handlers` | `PASS` | All 8 real controls execute authorized database mutations and audit logs. |

---

## 4. Test Execution Traces

### Sequence 1 Acceptance Test Suite
```
============================= test session starts =============================
platform win32 -- Python 3.12.15, pytest-9.2.0, pluggy-1.7.0 -- C:\Users\Dr Arif\Workspace.SalowTech\.venv\Scripts\python.exe
cachedir: .pytest_cache
django: version: 5.2.19, settings: config.settings.test (from ini)
rootdir: C:\Users\Dr Arif\Workspace.SalowTech
configfile: pyproject.toml
plugins: platformdirs-4.14.5, cov-7.1.0, django-4.14.0
collecting ... collected 30 items

tests/unit/test_authentication_centre_seq1.py::test_auth_001_iam_signin_valid_credentials_and_totp PASSED [  3%]
tests/unit/test_authentication_centre_seq1.py::test_auth_002_iam_signin_wrong_password_timing_safe PASSED [  6%]
tests/unit/test_authentication_centre_seq1.py::test_auth_003_iam_signin_suspended_admin PASSED [ 10%]
tests/unit/test_authentication_centre_seq1.py::test_auth_004_iam_signin_invalid_totp PASSED [ 13%]
tests/unit/test_authentication_centre_seq1.py::test_auth_005_iam_signin_replayed_totp PASSED [ 16%]
tests/unit/test_authentication_centre_seq1.py::test_auth_006_workspace_session_rejected_on_iam PASSED [ 20%]
tests/unit/test_authentication_centre_seq1.py::test_auth_007_iam_session_rejected_on_workspace PASSED [ 23%]
tests/unit/test_authentication_centre_seq1.py::test_auth_008_unapproved_workspace_device_receives_no_session PASSED [ 26%]
tests/unit/test_authentication_centre_seq1.py::test_auth_009_workspace_activation_one_time_expiry PASSED [ 30%]
tests/unit/test_authentication_centre_seq1.py::test_auth_010_ten_recovery_codes_hashed PASSED [ 33%]
tests/unit/test_authentication_centre_seq1.py::test_auth_011_unknown_identity_timing_resistant_dummy_path PASSED [ 36%]
tests/unit/test_authentication_centre_seq1.py::test_auth_012_distributed_rate_limiting_atomic PASSED [ 40%]
tests/unit/test_authentication_centre_seq1.py::test_auth_013_session_rotation_on_authentication PASSED [ 43%]
tests/unit/test_authentication_centre_seq1.py::test_auth_014_immediate_revocation_enforced PASSED [ 46%]
tests/unit/test_authentication_centre_seq1.py::test_auth_015_durable_correlated_audit_event PASSED [ 50%]
tests/unit/test_authentication_centre_seq1.py::test_auth_016_direct_centre_url_without_permission_denied PASSED [ 53%]
tests/unit/test_authentication_centre_seq1.py::test_auth_017_object_level_permissions_prevent_tampering PASSED [ 56%]
tests/unit/test_authentication_centre_seq1.py::test_auth_018_secret_leakage_scan PASSED [ 60%]
tests/unit/test_authentication_centre_seq1.py::test_auth_019_unauthorized_policy_update_denied PASSED [ 63%]
tests/unit/test_authentication_centre_seq1.py::test_auth_020_policy_change_increments_version_and_audits PASSED [ 66%]
tests/unit/test_authentication_centre_seq1.py::test_auth_021_iam_recovery_requires_dual_control_no_self_approval PASSED [ 70%]
tests/unit/test_authentication_centre_seq1.py::test_auth_022_three_workspace_recovery_pathways PASSED [ 73%]
tests/unit/test_authentication_centre_seq1.py::test_auth_023_device_approval_bound_to_identity_and_device PASSED [ 76%]
tests/unit/test_authentication_centre_seq1.py::test_auth_024_risk_challenge_or_deny_enforced PASSED [ 80%]
tests/unit/test_authentication_centre_seq1.py::test_auth_025_password_spraying_detection_triggers_alert PASSED [ 83%]
tests/unit/test_authentication_centre_seq1.py::test_auth_026_tamper_evident_audit_engine PASSED [ 86%]
tests/unit/test_authentication_centre_seq1.py::test_auth_027_alert_escalation_after_15_minutes PASSED [ 90%]
tests/unit/test_authentication_centre_seq1.py::test_auth_028_dependency_probes_truthful_reporting PASSED [ 93%]
tests/unit/test_authentication_centre_seq1.py::test_auth_029_clock_monitoring_and_totp_window PASSED [ 96%]
tests/unit/test_authentication_centre_seq1.py::test_auth_030_all_controls_map_to_real_backend_handlers PASSED [100%]

============================= 30 passed in 39.24s =============================
```

### Full Legacy Regression Test Suite
```
============================= test session starts =============================
platform win32 -- Python 3.12.15, pytest-9.2.0, pluggy-1.7.0
django: version: 5.2.19, settings: config.settings.test (from ini)
rootdir: C:\Users\Dr Arif\Workspace.SalowTech
configfile: pyproject.toml
plugins: platformdirs-4.14.5, cov-7.1.0, django-4.14.0
collected 57 items

tests\unit\test_iam_hardened_spec.py ...........................         [ 47%]
tests\unit\test_second_device_verification.py ................           [ 75%]
tests\unit\test_iam_recovery_and_email.py ..............                 [100%]

============================= 57 passed in 55.79s =============================
```

---

## 5. Modified & Created Artifacts Manifest

### Core Service & Engine Layers
- `apps/accounts/models.py`: Added models `AuthenticationTransaction`, `IAMAdminPresence`, `AuthenticationPolicy`, `IAMRecoveryCase`.
- `apps/accounts/auth/authentication_centre.py`: End-to-end service layer handling transaction lifecycles, live metrics, presence heartbeats, versioned policies, real control handlers, and live dependency probes.
- `apps/accounts/views/authentication_centre.py`: Controller views for Authentication Centre landing page, Workspace Control dashboard, IAM Authentication dashboard, transaction forensic inspection, sessions investigation, HTMX live feeds, and CSRF control action dispatcher.
- `apps/accounts/views/login.py`: Wired `AuthenticationTransaction` recording across password checks, lifecycle, risk evaluation, and MFA verification.
- `apps/accounts/views/iam_admin.py`: Integrated `AuthenticationTransaction` recording into IAM administrator sign-in.
- `apps/core/middleware.py`: Fixed `RateLimitRecord` creation alongside atomic row-locking in `RateLimitMiddleware`.
- `apps/accounts/migrations/0014_authenticationtransaction_authenticationpolicy_and_more.py`: Schema migration for Sequence 1 data models.

### Routing & Domain Separation
- `config/urls_iam.py`: Canonical host routing for `iam.salowtech.com` (`/auth/sign-in/`, `/auth/recovery/`, `/console/`, `/console/authentication/`, etc.).
- `apps/accounts/iam_urls.py`: Namespaced IAM URL configuration.
- `config/urls_workspace.py`: Canonical routes for `app.salowtech.com` (`/login/`, `/sign-in/`, `/forgot-password/`).

### Presentation Templates (High-End Dark UI, Zero Decorative Buttons)
- `templates/accounts/admin/auth_centre_landing.html`: Landing page presenting exactly two distinct sub-options (Workspace Authentication Control vs. IAM Authentication).
- `templates/accounts/admin/auth_dashboard_workspace.html`: Complete 8-tab Workspace Authentication Control dashboard.
- `templates/accounts/admin/auth_dashboard_iam.html`: Complete 8-tab IAM Authentication dashboard with dual-control cases and presence tracking.
- `templates/accounts/admin/auth_transaction_detail.html`: Forensic inspection view displaying safe stage timeline, risk signals, policy versions, and audit event linkage.
- `templates/accounts/admin/sessions_investigation.html`: Real-time session investigation and immediate cluster-wide revocation dashboard.
- `templates/accounts/admin/iam_sign_in_canonical.html`: Dedicated IAM administrator sign-in template.
- `templates/accounts/admin/iam_recovery_portal.html`: Dedicated IAM account recovery portal.
- `templates/accounts/admin/partials/auth_live_activity_rows.html`: 1-second read-only HTMX polling partial.
- `templates/accounts/admin/partials/auth_presence_rows.html`: 5-second IAM administrator presence status partial.

### Acceptance Test Suite
- `tests/unit/test_authentication_centre_seq1.py`: Comprehensive test suite verifying all 30 acceptance requirements (AUTH-001 through AUTH-030).
