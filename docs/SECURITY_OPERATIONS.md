# Salow Workspace — Security Operations & Diagnostic Runbook

**Authoritative Specification**: Salow Workspace IAM Architecture  
**Document Status**: Official Engineering Architecture Guide  
**Applies To**: `apps.accounts.management.commands.auth_doctor`, `apps.core.middleware.SecurityHeadersMiddleware`  

---

## 1. Automated Health Diagnostics (`auth_doctor`)

Salow Workspace includes a comprehensive 12-point automated diagnostic command:
```bash
python manage.py auth_doctor
```

### 1.1 The 12 Subsystem Health Verification Points

| # | Diagnostic Check | Verification Logic | Pass Criterion |
|---|---|---|---|
| **1** | **Password Hashing Order** | Inspects `settings.PASSWORD_HASHERS` | `Argon2PasswordHasher` is primary |
| **2** | **Database Connectivity** | Executes test query against database | Cursor returns cleanly |
| **3** | **Database Adapter & Migrations** | Checks migration executor for unapplied migrations | 0 pending migrations |
| **4** | **TOTP Engine** | Verifies `apps.accounts.auth.mfa_totp` functions | Vector SVG generator functional |
| **5** | **Cryptographic KeyRing** | Tests AES-256-GCM round-trip encryption/decryption | Ciphertext decrypted matching plaintext |
| **6** | **Recovery Codes Engine** | Tests HMAC-SHA256 generation & format | 10 alphanumeric tokens generated |
| **7** | **Session Engine** | Checks `SESSION_ENGINE` & `SessionStore` | Database session engine operational |
| **8** | **Cookie Security Settings** | Inspects `SESSION_COOKIE_*` settings | `HttpOnly=True`, `SameSite=Lax`, `Secure` |
| **9** | **Distributed Rate Limiter** | Probes `RateLimitRecord` table on active DB | Sliding window counter persists and queries |
| **10** | **WebAuthn / Passkeys Engine** | Verifies `webauthn` library & challenge generator | Level 3 options generated |
| **11** | **Audit Trail Persistence** | Verifies `AuditEvent` table accessibility | Events log cleanly to DB |
| **12** | **Production Stub Safeguards** | Validates absence of debug backdoors | Face stub / bypass guards active |

---

## 2. HTTP Security Headers (`SecurityHeadersMiddleware`)

Every HTTP response emitted by Salow Workspace includes mandatory browser defense headers:
- **`Content-Security-Policy`**: Enforces strict script and object sources (`default-src 'self'`).
- **`X-Frame-Options: DENY`**: Prevents clickjacking attacks by forbidding iframe embedding.
- **`X-Content-Type-Options: nosniff`**: Prevents MIME-type confusion attacks.
- **`Referrer-Policy: strict-origin-when-cross-origin`**: Protects sensitive internal URLs during outbound navigation.
- **`Permissions-Policy`**: Disables unauthorized browser APIs (geolocation, microphone, camera outside attendance).

---

## 3. Incident Response & Emergency Procedures

### 3.1 Revoking All Active Sessions for an Employee
In case of a compromised device or stolen laptop:
```bash
python manage.py shell -c "
from apps.accounts.models import CustomUser
from apps.accounts.auth.sessions import revoke_user_sessions
u = CustomUser.objects.get(employee_id='ST-EMP-1001')
revoke_user_sessions(u)
print('All sessions revoked.')
"
```

### 3.2 Emergency Account Suspension
```bash
python manage.py shell -c "
from apps.accounts.models import CustomUser, AccountStatus
from apps.accounts.auth.sessions import revoke_user_sessions
u = CustomUser.objects.get(employee_id='ST-EMP-1001')
u.status = AccountStatus.SUSPENDED
u.save(update_fields=['status'])
revoke_user_sessions(u)
print('Account suspended and sessions terminated.')
"
```

---

## 4. Security Operations Event Stream & Alert Triage (`SecurityAlert`)

### 4.1 Append-Only Audit & Security Event Stream
- **Endpoint**: `GET /security/events?after=<id>`
- **Protocol**: Cursor-paginated JSON or HTMX event list fragment.
- **Access Control**: Restricted to `SecurityAdmin`, `SecurityReviewer`, and `SuperAdmin`.
- **Append-Only Invariant**: Events and alerts are strictly immutable.

### 4.2 Security Alert Categories & Triage Protocol
- **Categories**:
  1. Repeated authentication failures (`repeated_auth_failures`)
  2. Account lockouts (`account_lockout`)
  3. Privileged role changes (`privileged_role_change`)
  4. MFA reset requests (`mfa_reset`)
  5. Recovery code usage (`recovery_code_used`)
  6. Second-device approval / fallback (`device_approval_fallback`)
  7. Break-glass elevation triggers (`break_glass_triggered`)
- **Independent Acknowledgement**: Triage actors can acknowledge alerts (`POST /auth/security/alerts/<id>/ack/`) with a justification rationale. Crucially, actors cannot acknowledge alerts concerning their own account (independent review requirement).

