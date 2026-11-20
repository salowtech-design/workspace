# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Key Custody, Encryption and Rotation Report (Workstream C)

- **Document ID**: `REMED-KEY-CUSTODY-01`
- **Date**: 20 November 2026
- **Status**: PASSED (Local/KMS Architecture) / BLOCKED (OCI KMS Provisioning)
- **Security Priority**: P0 (Gate 3)

---

### 1. Cryptographic Key Inventory & Purpose Segregation

In accordance with Section 8 of the Salow Security Architecture, strict key segregation is enforced. No single cryptographic key is shared across multiple functional domains.

| Key Identifier | Purpose / Domain | Algorithm | Storage / Configuration | Rotation Support |
|---|---|---|---|---|
| `BIOMETRIC_PRIMARY_KEY_HEX` | Face template & TOTP secret encryption | AES-256-GCM | Environment / KMS | Key Ring (`k1`, `k2`, ...) |
| `ACTIVATION_HMAC_KEY` | Account activation & recovery code hashing | HMAC-SHA256 | Environment / KMS | Single / Keyed digest |
| `EMAIL_PAYLOAD_KEY_HEX` | Outbox sensitive payload encryption | AES-256-GCM | Environment / KMS | Key ID payload tag |
| `SESSION_HMAC_KEY` | Session bearer digest calculation | HMAC-SHA256 | Environment / KMS | Keyed HMAC |
| `AUDIT_HMAC_KEY` | Audit checkpoint tamper-evident signing | HMAC-SHA256 | Independent Off-DB | `AUDIT_SIGNING_KEYS` map |

---

### 2. Fail-Closed Production Configuration & Startup Enforcement

In `config/settings/prod.py`, startup validation asserts that:
1. Every production key must be explicitly set with a 64-character hexadecimal (256-bit) value.
2. Default literals and development fallback secrets are strictly prohibited when `DEBUG=False`.
3. **Key Reuse Prohibition**: All keys must be cryptographically distinct:
   ```python
   key_set = {v for v in [biometric_key, activation_key, email_payload_key, session_hmac_key, audit_key, secret_key] if v}
   if len(key_set) < 6:
       raise ImproperlyConfigured("Cryptographic keys must be unique per functional purpose.")
   ```

---

### 3. Key Rotation & Decryption Architecture

- **Biometric & TOTP Key Ring (`BiometricKeyRing`)**:
  - Encrypted records store a key ID header (e.g. `k1`, `k2`).
  - During rotation, a new primary key is made active (`k2`).
  - Existing records encrypted with `k1` continue to decrypt successfully using the historical key map.
- **Audit Signing Key Rotation**:
  - `AUDIT_SIGNING_KEYS` dictionary retains historical verification keys mapped by key ID.
  - Verification verifies signatures using the key ID recorded in the checkpoint.

---

### 4. External Dependency Classification

- **Status**: **BLOCKED — EXTERNAL DEPENDENCY**
- **Dependency**: Production OCI Vault / AWS KMS hardware security module (HSM) key custody.
- **Current State**: Keys are configured via secure container environment injection (`.env` / Docker secrets). Automatic remote HSM envelope encryption requires cloud tenant KMS provisioning by Salow Tech infrastructure team.
