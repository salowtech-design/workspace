# Salow Workspace IAM — Database & Configuration Report

**Document Version:** 2.0  
**Target Engine:** TiDB Cloud Serverless (Distributed MySQL-compatible engine)  
**Host:** `gateway01.ap-southeast-1.prod.aws.tidbcloud.com`  
**Database Name:** `salow_workspce`  
**Status:** **Connected, Schema Synchronized, Invariants Enforced**

---

## 1. Database Connection & Engine Validation

The Salow Workspace IAM runs against a managed TiDB Cloud serverless instance utilizing the `django_tidb` database engine adapter.

### 1.1 Connection Parameters
- **Backend Adapter:** `django_tidb` (built on `mysqlclient`)
- **Port:** `4000` (TLS enabled)
- **Engine Version:** TiDB Serverless (MySQL 8.0 wire-protocol compatible)
- **Time Zone:** UTC storage, application presentation configured for `Asia/Kolkata`
- **Isolation Level:** Read Committed (distributed snapshot isolation)

All connection tests, transactions, unique constraint checks, and concurrent write locks were validated against TiDB Cloud with zero connection drops.

---

## 2. Migration History & Schema Alignment

All 42 application tables across the modular monolith are fully synchronized. The IAM subsystem spans the following critical migrations:

### `apps.accounts` Migrations:
1. `0001_initial`: Created `CustomUser`, initial status, roles, and user session models.
2. `0002_customuser_failed_login_attempts_and_more`: Added lockout counters and lock expiration timestamps.
3. `0003_authevent_loginattempt_remove_usertotpdevice_user_and_more`: Created immutable `AuthEvent` and `LoginAttempt` audit logs, `TOTPCredential` (replacing legacy device), and `RecoveryCode`.
4. `0004_alter_customuser_status`: Expanded lifecycle status choices to include `PENDING_ACTIVATION`.
5. `0005_passkeycredential_workspacedraft`: Preserved `PasskeyCredential` table and introduced `WorkspaceDraft`.
6. `0006_customuser_email_verified_recoveryrequest`: Added `email_verified` boolean to `CustomUser` and created the `RecoveryRequest` table with dual-approval foreign keys.

### `apps.core` Migrations:
1. `0001_initial`: Base core configurations.
2. `0002_featureflag_outboxevent_workspacesetting`: Workspace feature toggles and generic outbox event records.
3. `0003_ratelimitrecord`: Database sliding-window model `RateLimitRecord` for cluster-wide rate limiting.
4. `0004_emailoutbox`: Dedicated `EmailOutbox` model for encrypted transactional notifications and background delivery.

---

## 3. Detailed Data Model Schema

### 3.1 `CustomUser` (`accounts_customuser`)
- `id`: Auto-incrementing primary key.
- `employee_id`: `VARCHAR(32)`, case-insensitive normalized, unique index. Immutable after creation.
- `full_name`: `VARCHAR(128)`.
- `email`: `VARCHAR(254)`, null permitted, indexed.
- `email_verified`: `BOOLEAN`, default `False`. Tracks company email ownership.
- `password`: `VARCHAR(255)`, Argon2id hashed.
- `role`: `VARCHAR(20)`, choices: `super_admin`, `admin`, `hr`, `manager`, `employee`, `intern`.
- `status`: `VARCHAR(20)`, choices: `ACTIVE`, `LOCKED`, `SUSPENDED`, `EXITED`, `PENDING_ACTIVATION`.
- `failed_login_attempts`: `INT`, default `0`.
- `locked_until`: `DATETIME`, null if not locked.

### 3.2 `TOTPCredential` (`accounts_totpcredential`)
- `user_id`: One-to-one foreign key to `CustomUser`.
- `encrypted_secret`: `VARBINARY(255)`. Secret key encrypted using AES-256-GCM.
- `nonce`: `VARBINARY(32)`. 12-byte initialization vector.
- `key_id`: `VARCHAR(32)`. Identifier for keyring rotation (default `'k1'`).
- `confirmed_at`: `DATETIME`, null until initial verification completes.
- `last_used_step`: `BIGINT`. Tracks RFC 6238 30-second time-step to prevent token replay attacks.

### 3.3 `RecoveryCode` (`accounts_recoverycode`)
- `user_id`: Foreign key to `CustomUser`.
- `code_hash`: `VARCHAR(128)`, keyed HMAC-SHA256 representation of the normalized 12-character backup code.
- `consumed_at`: `DATETIME`, null until burned. Single-use invariant enforced at the database level.

### 3.4 `ActivationCode` (`accounts_activationcode`)
- `user_id`: Foreign key to `CustomUser`.
- `code_hash`: `VARCHAR(64)`, SHA-256 digest of Crockford Base32 token.
- `purpose`: `VARCHAR(32)`, choices: `ACTIVATION`, `PASSWORD_RESET`, `MFA_RESET`, `MFA_REPLACEMENT`, `EMAIL_VERIFICATION`.
- `is_live`: `INT`, nullable with unique constraint `(user_id, purpose, is_live)`. Enforces strictly one live code per purpose per user on TiDB.
- `attempts`: `INT`, max 5 attempts before token is automatically invalidated.
- `expires_at`: `DATETIME`.

### 3.5 `RecoveryRequest` (`accounts_recoveryrequest`)
- `user_id`: Foreign key to `CustomUser`.
- `request_type`: `VARCHAR(32)`, choices: `ADMIN_PASSWORD_RESET`, `MFA_REPLACEMENT`.
- `status`: `VARCHAR(20)`, choices: `PENDING`, `APPROVED`, `REJECTED`, `COMPLETED`.
- `approved_by_id`: Foreign key to `CustomUser` (primary administrator).
- `second_approved_by_id`: Foreign key to `CustomUser` (co-approver for Super Admin dual-control).
- `reason`: `TEXT`.

### 3.6 `EmailOutbox` (`core_emailoutbox`)
- `recipient`: `VARCHAR(254)`.
- `subject`: `VARCHAR(255)`.
- `encrypted_payload`: `LONGTEXT`. Contains serialized HTML/plain body and metadata encrypted via AES-256-GCM.
- `notification_type`: `VARCHAR(64)`. E.g. `password_reset_link`, `mfa_replacement_link`, `admin_reset_requested`.
- `status`: `VARCHAR(32)`, choices: `QUEUED`, `ACCEPTED_BY_TRANSPORT`, `RETRY_PENDING`, `DEAD_LETTER`.
- `attempts`: `INT`, default `0`.
- `max_attempts`: `INT`, default `5`.
- `retry_after`: `DATETIME`.
- `delivered_at`: `DATETIME`.
- *Privacy Safeguard:* Payload is purged (`encrypted_payload = ""`) upon terminal acceptance by transport to prevent data accumulation.

---

## 4. Key Management & Environment Configuration

### 4.1 Master Key Ring
- **Algorithm:** AES-256-GCM (Authenticated Encryption with Associated Data).
- **Environment Variable:** `AUTH_AES_KEYS={"k1": "<32-byte-hex-or-base64>"}`.
- **Rotation Support:** Supported via key identifiers (`k1`, `k2`); existing secrets store their matching `key_id`.

### 4.2 Application Environment Variables
```env
# Database Configuration
TIDB_HOST=gateway01.ap-southeast-1.prod.aws.tidbcloud.com
TIDB_PORT=4000
TIDB_NAME=salow_workspce
TIDB_USER=<redacted>
TIDB_PASSWORD=<redacted>

# Security & Keyring
SECRET_KEY=<redacted>
AUTH_AES_KEYS={"k1": "<redacted>"}
ALLOWED_EMAIL_DOMAINS=salowtech.com

# Email Delivery Configuration
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend  # or smtp backend
EMAIL_HOST=localhost
EMAIL_PORT=1025
DEFAULT_FROM_EMAIL=no-reply@salowtech.com
```
