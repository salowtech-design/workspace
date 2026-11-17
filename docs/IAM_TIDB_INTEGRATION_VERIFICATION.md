# Salow Workspace — TiDB Cloud Integration & Verification Report

**Directive**: Salow Workspace IAM v1.0 Forensic Verification & Security Correction  
**Stage**: Stage 8 — TiDB Integration Verification  
**Timestamp**: 2026-11-17T09:14:30+05:30  
**Auditor**: Senior Database & Infrastructure Security Engineer  
**Backend**: TiDB Cloud Starter (v8.5.3-serverless, MySQL 8.0 protocol)  
**Storage Engine**: TiKV Distributed Transactional Key-Value Store  

---

## 1. Database Identity & Connection Configuration

| Parameter | Authoritative Value | Source Configuration |
|---|---|---|
| **Database Name** | `salow_workspace` | `config/settings/dev.py`, `config/settings/prod.py`, `.env.example` |
| **Database Engine** | `django_tidb` | PingCAP officially maintained Django dialect (`django-tidb~=5.2.0`) |
| **Driver** | `mysqlclient` | C-based native MySQL client library (`mysqlclient>=2.2.0`) |
| **Port** | `4000` | Default TiDB Cloud SQL endpoint port |
| **Character Set** | `utf8mb4` | Full 4-byte UTF-8 encoding |
| **Collation** | `utf8mb4_general_ci` | TiDB Cloud default Unicode collation |
| **TLS / SSL** | Required (`ssl_mode=VERIFY_IDENTITY`) | Configured via `certifi.where()` CA bundle (`isrgrootx1.pem`) |
| **Transaction Model** | Pessimistic Transactions (Snapshot Isolation) | Supported natively via `select_for_update()` |

---

## 2. Table Schemas, Constraints & Indexes

All IAM tables have been migrated and verified:

### 2.1 `accounts_customuser`
- Primary Key: `id` (Auto-increment BigInt).
- Unique Indexes: `employee_id` (VARCHAR 32, uppercase normalized), `email` (nullable).
- Indexes: `role`, `status`, `is_active`.
- Storage: TiKV distributed region keys.

### 2.2 `accounts_totpcredential`
- Foreign Key: `user_id` $\rightarrow$ `accounts_customuser.id` (CASCADE, Unique).
- Fields: `encrypted_secret` (Blob), `nonce` (12B), `key_id` (VARCHAR 16), `last_used_step` (Integer), `confirmed_at` (DateTime).
- Atomic Replay Defense: Verified via `select_for_update()` under `with transaction.atomic():`.

### 2.3 `accounts_passkeycredential`
- Foreign Key: `user_id` $\rightarrow$ `accounts_customuser.id` (CASCADE).
- Unique Composite Index: `(user_id, credential_id)`.
- Fields: `public_key` (Blob/Binary), `sign_count` (BigInt), `aaguid` (VARCHAR 36), `display_name` (VARCHAR 64).

### 2.4 `accounts_workspacedraft`
- Foreign Key: `user_id` $\rightarrow$ `accounts_customuser.id` (CASCADE).
- Unique Composite Index: `(user_id, path)`.
- Fields: `draft_data` (JSON), `updated_at` (DateTime).

### 2.5 `core_ratelimitrecord`
- Composite Index: `(key, timestamp)`.
- Key Format: `VARCHAR(255)` storing composite IP (`10.100.x.x:sensitive`) and account dimensions (`acc:ST-EMP-xxxx:sensitive`).
- Atomic Sliding Window: Atomic inserts and count aggregation over `timestamp__gt=cutoff`.

### 2.6 `accounts_activationcode`
- Nullable Live Marker Constraint:
  ```sql
  UNIQUE KEY `unique_live_code_per_user_and_purpose` (`user_id`, `purpose`, `is_live`)
  ```
  *TiDB Compatibility Note*: TiDB complies with the ANSI SQL standard where multiple `NULL` values in a unique index do not collide. By setting `is_live=1` for active codes and setting `is_live=NULL` upon consumption/expiry, TiDB enforces that exactly one active code can exist per user/purpose, while preserving an immutable history of consumed codes.

---

## 3. Concurrency & Transactional Semantics

1. **Snapshot Isolation & Pessimistic Locks**:
   - TiDB Cloud v8 defaults to pessimistic transaction mode (`pessimistic`).
   - Row-level pessimistic locks (`SELECT ... FOR UPDATE`) block concurrent transactions attempting to read/update the same `TOTPCredential` or `ActivationCode` row until the active transaction commits.
2. **Atomic Consumption**:
   - `verify_and_consume_code()` acquires `select_for_update()` on the `ActivationCode` record, validates attempt counters, flips `is_live = None`, and records `consumed_at = timezone.now()`.
   - Multi-threaded test `test_concurrent_activation_code_redemption` proved that 5 simultaneous threads racing for the same token resulted in exactly 1 success and 4 clean rejections.
3. **Database Engine Safeguard**:
   - Dedicated staging and test configurations are strictly audited to ensure test suites do not silently substitute SQLite when `--settings=config.settings.dev` or `prod` is specified.
   - `auth_doctor` inspects `connection.vendor` and flags non-TiDB/non-MySQL engines when running in production/staging mode.
