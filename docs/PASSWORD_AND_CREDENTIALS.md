# Salow Workspace — Password Policy, Storage & Credentials Management

**Authoritative Specification**: Salow Workspace IAM Architecture  
**Document Status**: Official Engineering Architecture Guide  
**Applies To**: `apps.accounts.auth.passwords`, `apps.accounts.models`  

---

## 1. Password Policy Principles

Salow Workspace enforces enterprise password controls aligned with NIST SP 800-63B guidelines:
- **No Administrator-Chosen Passwords**: Every employee sets their own permanent password during initial activation or authorized recovery.
- **Complexity and Length**: Minimum 12 characters. No arbitrary symbol mandates that degrade entropy or encourage predictable substitutions.
- **Identifier Substring Checks**: Passwords containing the employee's ID (`employee_id`), first name, last name, or common company keywords (`salow`, `workspace`) are rejected.
- **Common Password Screening**: Passwords matching common weak lists (e.g., `Password123!`, `Qwerty2026`) are rejected via Django's `CommonPasswordValidator`.

---

## 2. Password Hashing Specification

The authoritative password hashing engine utilizes **Argon2id** (RFC 9106), the winner of the Password Hashing Competition (PHC) and the gold standard for memory-hard key derivation.

### 2.1 Hashing Configuration (`settings.py`)
```python
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
]
```

### 2.2 Argon2id Parameters
- **Time Cost ($t$)**: 2 iterations.
- **Memory Cost ($m$)**: 19,456 KiB (19 MiB).
- **Parallelism ($p$)**: 1 lane.
- **Salt**: 16 bytes of cryptographically secure pseudo-random bytes generated via `os.urandom()`.
- **Output Tag**: 32-byte digest.

---

## 3. Password Verification & Rotation Lifecycle

### 3.1 Verification Flow
1. Submission of plaintext password over TLS 1.3.
2. Constant-time comparison executed in the native C library bindings of `argon2-cffi`.
3. If the stored password hash is in an older format (e.g. legacy PBKDF2), Django transparently upgrades the hash to Argon2id upon successful sign-in.

### 3.2 Password Change (`/accounts/security/password/change/`)
- Requires verification of the current password before accepting a new credential.
- Enforces that the new password is not identical to the current password.
- Updates `CustomUser.password_changed_at = timezone.now()`.
- **Session Revocation**: In accordance with Stage 5 requirements, all existing user sessions across other browsers/devices are invalidated immediately. The current session key is rotated and preserved.

### 3.3 Zero Exposure Safeguards
1. Plaintext passwords are never written to disk, database, cache, or message brokers.
2. Django logging filters and custom audit serializers redact any field named `password`, `new_password`, `confirm_password`, or `current_password` with `[REDACTED]`.
3. Diagnostic commands (`python manage.py auth_doctor`) verify password hasher order without querying or printing user password hashes.
