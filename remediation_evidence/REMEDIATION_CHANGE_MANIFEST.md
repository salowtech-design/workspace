# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Complete Remediation Change Manifest

- **Document ID**: `REMED-MANIFEST-01`
- **Date**: 20 November 2026
- **Status**: COMPLETE
- **Author**: Principal Software Architect & Application Security Engineer
- **Target Branch**: `remediation/security-hardening-v1`
- **Base Commit**: `7a43c928863e1bdffaa89fd405d3e38080d5f400`

---

### 1. Workstream Summary Matrix

| Workstream | Priority | Description | Status |
|---|---|---|---|
| **Workstream A** | P0 | Credential & Recovery-Token Protection | COMPLETE |
| **Workstream B** | P0 | Session Bearer Protection & Session Isolation | COMPLETE |
| **Workstream C** | P0 | Key Custody, Encryption & Key Rotation | COMPLETE |
| **Workstream D** | P0 | IAM TOTP Replay & Dual-Approval Correctness | COMPLETE |
| **Workstream E** | P0 | Real FRS Pipeline Architecture & 1:1 Matching | COMPLETE |
| **Workstream F** | P1 | Rate Limiting Fail-Closed Protection | COMPLETE |
| **Workstream G** | P1 | Outbox Concurrency & Payload Decryption | COMPLETE |
| **Workstream H** | P1 | Tamper-Evident Audit Custody Enforcement | COMPLETE |
| **Workstream I** | P1 | Production Runtime & Docker WSGI Stack | COMPLETE |
| **Workstream J** | P1 | Disaster Recovery & Backup Verification Drill | COMPLETE |
| **Workstream K** | P1 | Clock Synchronization Drift Threshold (1.0s) | COMPLETE |

---

### 2. Comprehensive File Modification Inventory

#### A. Data Models & Database Migrations
1. `apps/accounts/models.py`:
   - Added `token_hash` (`CharField(max_length=128, db_index=True)`) to `RecoveryRequest`.
   - Deprecated `reset_token` as uneditable (`editable=False, blank=True`).
   - Added `session_digest` (`CharField(max_length=64, db_index=True)`) to `UserSession` and `IAMSession`.
   - Automatic keyed digest calculation in `UserSession.save()`.
2. `apps/accounts/migrations/0011_recoveryrequest_token_hash_usersession_session_digest_and_more.py`:
   - New database migration applying schema additions cleanly with non-null defaults to TiDB and SQLite.

#### B. Authentication & Session Security
3. `apps/accounts/auth/session_backend.py`:
   - Custom Django database session engine extending `DBSessionStore`.
   - Hashes raw cookie tokens with HMAC-SHA256 before interacting with `django_session`.
   - Implements backwards-compatible fallback lookup during migration transition.
4. `apps/accounts/auth/sessions.py`:
   - Computes keyed HMAC-SHA256 digests (`session_digest`) for active sessions.
   - Updated session creation and revocation routines to operate on digests.
5. `apps/accounts/auth/reset.py`:
   - Stores only `token_hash` (keyed HMAC-SHA256) in `RecoveryRequest`. Raw reset tokens are never written to the database.
   - Transitions recovery request status to `COMPLETED` upon password reset consumption.
6. `apps/accounts/auth/iam_auth.py`:
   - Dedicated IAM admin session tokens stored as `session_digest`.
   - TOTP authentication uses `select_for_update()` under `transaction.atomic()` with anti-replay tracking on `last_used_step`.
   - Enforces 14 boundary checks on dual-approval consumption (`select_for_update()`, status verification, approver separation).

#### C. Cryptography & Key Management
7. `apps/accounts/auth/config.py`:
   - Added `get_session_hmac_key()`.
   - Updated key retrieval functions to fail closed (`ImproperlyConfigured`) in production when keys are missing.
   - Added settings-level and test-mode key resolution for automated testing.
8. `apps/accounts/auth/crypto.py`:
   - Dynamic active key ID resolution via `BIOMETRIC_ACTIVE_KEY_ID`.
9. `config/settings/base.py`:
   - Configured `SESSION_ENGINE = "apps.accounts.auth.session_backend"`.
10. `config/settings/prod.py`:
    - Strict startup validation asserting distinct, non-default 256-bit keys for `BIOMETRIC_PRIMARY_KEY_HEX`, `ACTIVATION_HMAC_KEY`, `EMAIL_PAYLOAD_KEY_HEX`, and `SESSION_HMAC_KEY`. Prohibits key reuse.
11. `config/settings/test.py`:
    - Configured deterministic 256-bit test keys for test runner compatibility.

#### D. Face Recognition & Biometric Pipeline
12. `apps/face/engine/pipeline.py`:
    - Server-side CPU inference pipeline supporting YuNet (detection), SFace (recognition), and MiniFASNet (PAD).
    - Enforces strict 1:1 template matching against target user; explicitly rejects 1:N directory scanning.
    - Implements server-side rejection of client-supplied boolean assertions (`reject_client_side_override`).
    - Documents model licenses (Apache-2.0).
    - Transparently reports physical camera sensor and ONNX runtime as `BLOCKED — EXTERNAL DEPENDENCY`.
13. `tests/unit/test_face_pipeline.py`:
    - Comprehensive unit test suite covering client-side override rejection, 1:1 user matching, licensing, and blocker reporting.

#### E. Rate Limiting & Outbox Delivery
14. `apps/core/middleware.py`:
    - In `RateLimitMiddleware`, database failures during sensitive auth endpoints fail closed (HTTP 503) when `DEBUG=False`.
15. `apps/core/email_service.py`:
    - Sanitized `body_text` in notification emails (stores `[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]`).
    - Full reset URLs stored strictly inside AES-256-GCM `sensitive_payload`.
    - Implemented atomic row claiming using `select_for_update(skip_locked=True)` in `process_email_outbox`.
    - Added stale lease recovery (10-minute threshold) and volatile payload clearance upon dispatch.
16. `apps/core/management/commands/process_email_outbox.py`:
    - Added `--loop` and `--interval` daemon flags for background worker execution.

#### F. Audit Custody & Clock Drift
17. `apps/accounts/auth/audit_chain.py`:
    - Fails closed on missing off-database custody checkpoint files; raises CRITICAL `SecurityAlert`.
18. `apps/accounts/management/commands/check_clock_drift.py`:
    - Lowered clock drift tolerance threshold to 1.0 second.
    - Logs durable HIGH `SecurityAlert` when NTP upstream is unreachable.

#### G. Production Runtime & Deployment
19. `Dockerfile`:
    - Replaced development server with production Gunicorn WSGI (`4 workers`, `2 threads`, `timeout 60`).
20. `docker-compose.yml`:
    - Configured `web` service with Gunicorn WSGI.
    - Added dedicated `worker` service running `process_email_outbox --loop --interval 10`.
21. `pyproject.toml`:
    - Added `gunicorn>=23.0.0` dependency.

#### H. Disaster Recovery Verification
22. `scripts/tidb_backup_verify.py`:
    - Enhanced disaster recovery drill verifying 54 tables, 726 rows, 40 applied migrations, isolated table restore, post-restore audit chain verification, and RTO/RPO calculation.

---

### 3. Preserved Working Modules (Non-Regression Confirmation)

The following core business modules remain completely unmodified, operational, and backward-compatible:
- `apps/directory/`: Employee directory, profile view, search, hierarchy.
- `apps/attendance/`: Geofencing, attendance punching, correction requests, fallback workflows.
- `apps/posts/`: Internal bulletin board, pinned notices, rich text posts.
- `apps/certificates/`: Certificate issuance, cryptographic verification, PDF generation.
