# Salow Workspace — IAM Initial Verification Report

**Project**: Salow Workspace  
**Organization**: Salow Tech  
**Date of Audit**: 2026-11-16  
**Environment**: Local Salow Workspace Monolith (Python 3.12.15, Django 5.2.19, TiDB Cloud v8.5.3-serverless)  
**Authoritative Backend**: TiDB Cloud (`salow_workspce` on AWS ap-southeast-1)  

---

## 1. Executive Summary

This verification report documents the baseline status of the Salow Workspace Identity, Authentication, Authorization, and Security System prior to executing the comprehensive IAM hardening directive.

All assessments are based on actual source inspection, live execution against TiDB Cloud, and automated test execution (119/119 unit and penetration security tests passing).

---

## 2. Subsystem Verification Breakdown

### 2.1 Account Identity & Account Lifecycle (Part 4)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/accounts/models.py` (`CustomUser`, `UserManager`)
  - `apps/accounts/middleware.py` (`AccountSecurityMiddleware`)
- **Existing Implementation**:
  - Immutable employee ID normalized to uppercase (e.g., `ST-EMP-1001`).
  - Argon2id password hashing as primary hasher.
  - Granular lifecycle statuses: `INVITED`, `ACTIVATING`, `ACTIVE`, `LOCKED`, `DISABLED`, `ONBOARDING`, `INACTIVE`, `SUSPENDED`, `EXITED`.
  - Roles: `SUPER_ADMIN`, `ADMIN`, `HR`, `MANAGER`, `EMPLOYEE`, `INTERN`.
- **Missing Implementation & Security Defects**:
  - `AccountSecurityMiddleware` explicitly ejects `INVITED`, `ACTIVATING`, `DISABLED`, and `LOCKED` accounts, but did not check `SUSPENDED` and `EXITED` status strings in the session invalidation loop.
- **Database Dependencies**: `accounts_customuser` table on TiDB Cloud.
- **Tests Executed**: `tests/unit/test_models.py`, `tests/unit/test_auth_spec.py` (All PASS).
- **Required Changes**: Add `SUSPENDED` and `EXITED` to `AccountSecurityMiddleware` real-time ejection checks; ensure session invalidation on status change.

### 2.2 First-Time Activation Engine (Part 4.3)
- **Status**: VERIFIED
- **Existing Files**:
  - `apps/accounts/auth/activation.py`
  - `apps/accounts/views/activation.py`
  - `apps/accounts/models.py` (`ActivationCode`)
- **Existing Implementation**:
  - Crockford Base32 10-character codes (`XXXXX-XXXXX`) excluding ambiguous characters (`I, L, O, U`).
  - Keyed HMAC-SHA256 storage (raw secret never persisted).
  - 48-hour expiration TTL.
  - Brute-force lockout (5 attempts maximum).
  - Atomic consumption outside rollback boundaries.
  - Multi-step activation wizard: Code verification -> Permanent password creation -> TOTP enrollment -> Recovery code storage.
- **Database Dependencies**: `accounts_activationcode` table with `unique_live_code_per_user_and_purpose` constraint.
- **Tests Executed**: `TestActivationEngine` (5 tests in `tests/unit/test_auth_spec.py` - PASS).

### 2.3 Password Security & Credential Management (Part 4.4)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/accounts/auth/passwords.py`
  - `apps/accounts/models.py`
- **Existing Implementation**:
  - Argon2id hasher (`argon2-cffi 25.1.0`).
  - Minimum 12-character length requirement.
  - User attribute similarity checks (rejects passwords containing employee ID or full name parts).
  - Blacklisted common dictionary password rejection.
- **Missing Implementation**:
  - Self-service password change view with current-password verification and automatic session revocation.
- **Database Dependencies**: `accounts_customuser` table.
- **Tests Executed**: `TestPasswordPolicy` (4 tests in `tests/unit/test_auth_spec.py` - PASS).

### 2.4 Normal Authentication & MFA TOTP (Part 5)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/accounts/auth/login_flow.py`
  - `apps/accounts/auth/mfa_totp.py`
  - `apps/accounts/views/login.py`
  - `apps/accounts/models.py` (`TOTPCredential`, `LoginAttempt`)
- **Existing Implementation**:
  - Step 1: Employee ID & password verification with timing-safe dummy check against user enumeration.
  - Step 2: Restricted pre-authentication state redirecting to `/auth/mfa/challenge/`.
  - Replay protection with `last_used_step` persistence.
  - AES-256-GCM encryption of TOTP secrets at rest using keyring `k1`.
  - Pure Python SVG QR code generation without external network calls.
- **Missing Implementation & Security Defects**:
  - Pre-authentication state (`_salow_login_pending_uid`) in session lacked independent short-lived TTL (e.g., 5-minute expiry) and attempt count cap.
  - Challenge verification window in `apps/accounts/auth/mfa_totp.py` was widened to window=4; needs atomic concurrency verification to ensure race-free anti-replay under TiDB pessimistic locking.
- **Database Dependencies**: `accounts_totpcredential`, `accounts_loginattempt`, `accounts_usersession`.
- **Tests Executed**: `TestTOTPMFAEngine`, `test_login_invalid_credentials_returns_generic_error` (PASS).

### 2.5 WebAuthn / Passkeys (Part 6)
- **Status**: NOT IMPLEMENTED
- **Existing Files**: None.
- **Existing Implementation**: None.
- **Missing Implementation**:
  - `PasskeyCredential` database model (storing credential ID, public key, sign counter, user handle, display name).
  - WebAuthn registration service (`generate_registration_options`, `verify_registration_response`).
  - WebAuthn assertion service (`generate_authentication_options`, `verify_authentication_response`).
  - Frontend WebAuthn JavaScript client and ceremony handlers.
  - Passkey sign-in option on login page and passkey management in security dashboard.
- **Database Dependencies**: Requires new migration for `PasskeyCredential`.
- **Tests Executed**: None.

### 2.6 Forgot Password & Account Recovery (Part 7 & Part 8)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/accounts/auth/reset.py`
  - `apps/accounts/auth/recovery_codes.py`
- **Existing Implementation**:
  - 10 single-use recovery codes generated per set; stored as keyed HMAC-SHA256; atomic burn on consumption.
  - Backend functions: `self_service_reset_password`, `issue_mfa_reset_code`, `verify_mfa_reset_and_authorize`.
  - Dual-admin approval requirement enforced for privileged admin accounts.
  - Minimum 2 Super Admins guard.
- **Missing Implementation**:
  - Independent recovery views:
    - `/auth/recovery/forgot-password/` (timing-safe self-service recovery entry point).
    - `/auth/recovery/reset/` (complete recovery and set new password).
    - `/auth/recovery/admin/` (dedicated administrative and delegated HR/Manager recovery console).
  - HR / Manager team-scoped delegated recovery authorization logic.
- **Database Dependencies**: `accounts_recoverycode`, `accounts_activationcode`.
- **Tests Executed**: Recovery code generation & redemption tests (PASS).

### 2.7 Server-Side Session Security & Resumption (Part 9)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/accounts/auth/sessions.py`
  - `apps/accounts/views/security.py`
  - `apps/accounts/models.py` (`UserSession`)
- **Existing Implementation**:
  - Server-side session tracking with `UserSession` model.
  - Key rotation on login (`request.session.cycle_key()`).
  - Single-session termination from security dashboard.
  - Session revocation on password reset or deactivation.
- **Missing Implementation**:
  - "Terminate all other sessions" action.
  - Idle timeout lock (15 minutes) with server-side draft preservation and seamless resumption upon re-authentication.
  - Sudo mode / recent re-authentication verification before sensitive credentials or recovery operations.
- **Database Dependencies**: `django_session`, `accounts_usersession`.
- **Tests Executed**: Basic session rotation and termination tests (PASS).

### 2.8 RBAC & IAM Authorization (Part 10)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/accounts/permissions.py`
  - `docs/PERMISSIONS.md`
- **Existing Implementation**:
  - Role decorators: `@require_role`, `@require_permission`, `@require_sudo`.
  - Static permission definitions in `docs/PERMISSIONS.md`.
- **Missing Implementation**:
  - Unified central IAM policy evaluator checking tenant boundaries, team reporting hierarchy, and object-level ownership.
  - Full permission matrix test suite asserting allow/deny outcome for every role against every protected route.
- **Tests Executed**: `tests/unit/test_permissions.py` (2 tests - PASS).

### 2.9 Distributed Rate Limiting & Abuse Protection (Part 11)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/core/middleware.py` (`RateLimitMiddleware`)
  - `apps/accounts/auth/lockout.py` (`LoginAttempt`)
- **Existing Implementation**:
  - `RateLimitMiddleware` with 60 req/min limit on sensitive paths (`/auth/login`, `/auth/activate`, `/verify`) and 300 req/min general.
  - `LoginAttempt` database-backed lockout tracking.
- **Security Defects**:
  - `RateLimitMiddleware` stored state in a Python process memory dictionary (`_storage`), which does not synchronize across multiple Django worker processes or clustered servers.
  - Untrusted client IP resolution from `X-Forwarded-For` without validating reverse proxy authenticity.
- **Required Changes**:
  - Implement a distributed-safe sliding window rate limiter (database-backed or cache-backed).
  - Add trusted proxy IP validation.

### 2.10 Cryptography & Key Management (Part 12)
- **Status**: VERIFIED
- **Existing Files**:
  - `apps/accounts/auth/config.py`
  - `apps/accounts/auth/mfa_totp.py`
  - `apps/face/services.py`
  - `apps/certificates/services.py`
- **Implementation**:
  - Argon2id for passwords.
  - AES-256-GCM for TOTP secrets and face templates with key ring `k1`.
  - HMAC-SHA256 for activation codes, recovery codes, and certificate authenticity signatures.
  - Zero hard-coded cryptographic keys; secrets strictly loaded from environment.
- **Tests Executed**: `test_totp_secret_encryption_at_rest`, `test_certificates_spec.py` (PASS).

### 2.11 Face Recognition System (FRS) Boundary (Part 13)
- **Status**: VERIFIED
- **Existing Files**:
  - `apps/face/services.py`
  - `apps/face/models.py`
- **Implementation**:
  - Face recognition is strictly segregated to attendance check-in/out and approved step-up verification.
  - Not part of standard login.
  - Never used as a password-reset factor.
  - No raw biometric images or embeddings stored unencrypted.
- **Tests Executed**: `tests/unit/test_face_spec.py`, `tests/unit/test_face_engine.py` (PASS).

### 2.12 Diagnostics Command (Part 18)
- **Status**: PARTIALLY VERIFIED
- **Existing Files**:
  - `apps/accounts/management/commands/auth_doctor.py`
- **Verification**:
  - Verified operational against TiDB Cloud: 8/8 subsystems reporting `[PASS]`.
- **Required Enhancements**:
  - Expand to cover the 12 required architectural diagnostics: WebAuthn configuration, rate-limit backend availability, cookie security, production debug safeguards, recovery-code readiness, and keyring integrity.

### 2.13 TiDB Cloud Integration (Part 16)
- **Status**: VERIFIED
- **Driver & Backend**: `django-tidb 5.2.1` with `mysqlclient 2.3.1` over TLS (`certifi`).
- **Target**: `gateway01.ap-southeast-1.prod.aws.tidbcloud.com:4000`, database `salow_workspce`.
- **Current State**: All 6 pending migrations applied cleanly (`accounts.0003`, `accounts.0004`, `attendance.0002`, `certificates.0001`, `face.0002`, `posts.0001`).

### 2.14 1,000 Concurrent User Load Testing (Part 15)
- **Status**: NOT IMPLEMENTED
- **Existing Files**: None.
- **Required Implementation**:
  - Reproducible load test harness simulating 1,000 concurrent sessions, sign-in, MFA verification, rate limiting, and TiDB connection pool performance.

---

## 3. Migration and Legacy Code Impact
- Legacy files `apps/accounts/views.py` and `apps/accounts/services.py` contain deprecated code from Milestone M0 that referenced dropped models (`LoginHistory`, `UserTOTPDevice`). These must be cleaned up to avoid developer confusion.
- A new migration is required for `PasskeyCredential` and draft persistence models.

---

## 4. Next Implementation Actions
1. **Passkey & WebAuthn Subsystem**: Model, W3C WebAuthn ceremony services, registration/authentication views, and UI.
2. **Account Recovery & Delegation Subsystem**: Public forgot password flow, recovery code reset, HR/Manager team-scoped delegated recovery, and Admin recovery console.
3. **Session & Draft Resilience**: 15-minute idle timeout lock screen, server-side draft persistence, and "Logout all other sessions" action.
4. **Distributed Rate Limiting**: Multi-worker database-backed sliding window rate limiter with trusted proxy validation.
5. **IAM Central Policy Engine & Permission Matrix Tests**: Comprehensive coverage across all roles and endpoints.
6. **Enhanced Diagnostics**: Expand `auth_doctor` to 12 checks.
7. **Load Testing Harness**: 1,000 virtual concurrent user stress script.
8. **Final Build & Acceptance Documentation**: `docs/IAM_BUILD_REPORT.md` and complete runbook suite.
