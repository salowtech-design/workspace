# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## File Ownership and Code Review Ledger

- **Document ID**: `REMED-OWNERSHIP-01`
- **Date**: 20 November 2026
- **Status**: APPROVED
- **Reviewers**: Principal Security Architect, Principal Django Engineer

---

### 1. Ownership & Responsibility Matrix

| Subsystem / Path | Primary Owner | Security Reviewer | Verification Scope |
|---|---|---|---|
| `apps/accounts/models.py` | Lead Data Architect | Security Architect | Schema invariants, token hashing, session digests |
| `apps/accounts/auth/session_backend.py` | Principal Django Eng | Security Architect | Raw cookie hashing, HMAC-SHA256, DBSessionStore |
| `apps/accounts/auth/reset.py` | Security Architect | AppSec Lead | Reset token hashing, recovery request lifecycle |
| `apps/accounts/auth/iam_auth.py` | IAM Specialist | AppSec Lead | Dedicated IAM auth, TOTP anti-replay, dual approval |
| `apps/accounts/auth/config.py` | Infrastructure Lead | Security Architect | Production key enforcement, fail-closed configuration |
| `apps/accounts/auth/audit_chain.py` | Security Architect | Compliance Officer | Tamper-evident hash chain, independent custody |
| `apps/face/engine/pipeline.py` | Biometrics Lead | Security Architect | 1:1 matching, client override rejection, licensing |
| `apps/core/email_service.py` | Backend Engineer | AppSec Lead | Outbox concurrency, AES-256 payload encryption |
| `apps/core/middleware.py` | Backend Engineer | Security Architect | Rate limiting fail-closed protection (HTTP 503) |
| `config/settings/` | DevOps Lead | Security Architect | WSGI config, key isolation, production hardening |
| `scripts/tidb_backup_verify.py` | DBA / SRE | Security Architect | DR drill, schema dump, isolated restore, RTO/RPO |

---

### 2. Code Review Security Invariants Checklist

1. **Zero Secret Exposure**:
   - [x] No plaintext passwords, TOTP seeds, recovery codes, or raw reset tokens are persisted.
   - [x] `RecoveryRequest.token_hash` stores keyed HMAC-SHA256; `reset_token` is empty/uneditable.
   - [x] Session cookie bearer values are never written to `django_session` or `UserSession` (hashed via HMAC-SHA256).
   - [x] Sensitive recovery links are encrypted in AES-256-GCM `sensitive_payload`; `body_text` is redacted.

2. **Concurrency & Atomicity**:
   - [x] `select_for_update()` used across TOTP validation, recovery redemption, activation, and outbox processing.
   - [x] `skip_locked=True` used in background outbox worker to prevent multi-worker contention.
   - [x] TiDB pessimistic transaction mode verified under multi-threaded concurrency.

3. **Fail-Closed Security**:
   - [x] Rate limiting returns HTTP 503 on database failure in production for sensitive endpoints.
   - [x] Cryptographic key loaders raise `ImproperlyConfigured` when production keys are absent.
   - [x] Missing off-database audit custody files raise CRITICAL alerts and halt verification.
   - [x] Clock drift greater than 1.0 second or unreachable NTP generates durable HIGH alerts.

4. **Biometric Integrity**:
   - [x] Biometric verification restricted to 1:1 matching against authenticated user.
   - [x] Client-side boolean flags (`face_verified=True`) strictly rejected.
   - [x] External hardware dependencies (camera sensor) documented and classified as blockers.

---

### 3. Review Verdict

All 22 modified and created files have undergone rigorous line-by-line review against the Salow Security Architecture v1.0 and Remediation Contract. All changes comply with architectural invariants with zero regressions.
