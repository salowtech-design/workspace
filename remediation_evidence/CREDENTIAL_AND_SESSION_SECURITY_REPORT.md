# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Credential and Session Security Report (Workstreams A & B)

- **Document ID**: `REMED-CRED-SESS-01`
- **Date**: 20 November 2026
- **Status**: PASSED
- **Security Priority**: P0 (Gate 1 & Gate 2)

---

### 1. Workstream A: Credential & Recovery-Token Protection

#### A. Architecture & Invariants
- **Primary Password Hasher**: Argon2id (`django.contrib.auth.hashers.Argon2PasswordHasher`) configured with memory cost 64MB, time cost 3 iterations, parallelism 4.
- **TOTP Credential Encryption**: 160-bit Base32 secret keys encrypted with AES-256-GCM using authenticated key ring (`BiometricKeyRing`). Plaintext secrets never appear in database or logs.
- **Recovery Codes**: Generated as high-entropy 10-character codes; stored strictly as keyed HMAC-SHA256 digests (`RecoveryCode.keyed_hash`). Consumed single-use under row-level `select_for_update()`.
- **Administrative & Email Password Reset Tokens**:
  - Raw high-entropy tokens are returned to the caller or encrypted inside outbox payload.
  - Stored in the database exclusively as `RecoveryRequest.token_hash` (`CharField(max_length=128)`).
  - The legacy `reset_token` column is deprecated, uneditable, and cleared upon approval.
- **Email Payload Redaction**:
  - `apps/core/email_service.py` sanitizes `body_text` in notification records, storing `"[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]"`.
  - Sensitive recovery URLs are stored exclusively within AES-256-GCM `sensitive_payload`.

#### B. Verification Evidence
- `tests/unit/test_iam_recovery_and_email.py`: 14 tests covering password reset token hashing, dual-approval requirement for Super Admins, and single-use consumption -> **PASSED**.
- `tests/unit/test_totp.py`: 3 tests covering TOTP generation, AES-256-GCM encryption, and tampering rejection -> **PASSED**.

---

### 2. Workstream B: Session Bearer Protection & Session Isolation

#### A. Threat Model & Vulnerability Remediation
- **Vulnerability**: Standard Django `django.contrib.sessions.backends.db` stores raw cookie bearer values directly as the primary key `session_key` of `django_session`. A database read breach immediately yields valid session cookies.
- **Remediation**:
  1. Implemented `apps.accounts.auth.session_backend.SessionStore` subclassing Django's `DBSessionStore`.
  2. The custom session backend computes a 40-character keyed HMAC-SHA256 digest (`hash_session_bearer`) before saving to or reading from `django_session`.
  3. The raw session key exists only in the client's HTTP cookie (`sessionid` or `salow_iam_session`).
  4. In `UserSession` and `IAMSession`, `session_digest` stores the keyed digest.
  5. Backwards-compatible lookup fallback allows existing sessions to transition without forced re-authentication.

#### B. Hostname and Cookie Isolation
- **Workspace Host (`app.salowtech.com`)**:
  - Session cookie: `sessionid` / `__Host-sessionid`
  - Cookie flags: `HttpOnly`, `SameSite=Lax`, `Secure=True` (in production)
- **IAM Host (`iam.salowtech.com`)**:
  - Dedicated IAM session cookie: `salow_iam_session` / `__Host-iam_session`
  - Workspace views reject IAM session cookies.
  - IAM views reject standard employee workspace session cookies.
- **Multi-Device Revocation**:
  - Session revocation (`revoke_user_sessions`) purges active sessions across both `UserSession` and `django_session` using digest indexing.

#### C. Verification Evidence
- `tests/unit/test_iam_host_isolation.py`: 7 tests covering host segregation, IAM session cookie isolation, and workspace 404 routing -> **PASSED**.
- `tests/unit/test_second_device_verification.py`: 16 tests covering secondary device approval, session binding, and push authorization -> **PASSED**.
