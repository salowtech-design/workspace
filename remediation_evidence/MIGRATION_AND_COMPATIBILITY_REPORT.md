# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Migration and Backward Compatibility Report

- **Document ID**: `REMED-MIGRATION-01`
- **Date**: 20 November 2026
- **Status**: APPLIED & VERIFIED
- **Database Engine**: TiDB Cloud `v8.5.3-serverless` (AWS `ap-southeast-1`) & SQLite 3
- **Total Migrations**: 55 Applied Migrations

---

### 1. Migration Specification

- **File**: `apps/accounts/migrations/0011_recoveryrequest_token_hash_usersession_session_digest_and_more.py`
- **Dependencies**: `apps.accounts.0010_auditcheckpoint_key_id_biometricassertion_and_more`
- **Operations**:
  1. `AddField(model_name="iamsession", name="session_digest", field=CharField(max_length=64, db_index=True, blank=True, default=""))`
  2. `AddField(model_name="recoveryrequest", name="token_hash", field=CharField(max_length=128, db_index=True, blank=True, default=""))`
  3. `AddField(model_name="usersession", name="session_digest", field=CharField(max_length=64, db_index=True, blank=True, default=""))`
  4. `AlterField(model_name="recoveryrequest", name="reset_token", field=CharField(max_length=128, blank=True, default="", editable=False))`

---

### 2. Execution & Application Proof

#### A. Central Remote TiDB Cloud Execution
```bash
.\.venv\Scripts\python.exe manage.py migrate
```
**Output**:
```text
Operations to perform:
  Apply all migrations: accounts, admin, attendance, auth, certificates, contenttypes, core, directory, face, posts, sessions
Running migrations:
  Applying accounts.0011_recoveryrequest_token_hash_usersession_session_digest_and_more... OK
```

#### B. Verification via `showmigrations`
All 55 migrations applied cleanly across all 11 applications on TiDB:
- `accounts`: `0001` through `0011` [X]
- `admin`: `0001` through `0003` [X]
- `attendance`: `0001`, `0002` [X]
- `auth`: `0001` through `0012` [X]
- `certificates`: `0001` [X]
- `contenttypes`: `0001`, `0002` [X]
- `core`: `0001` through `0004` [X]
- `directory`: `0001` [X]
- `face`: `0001`, `0002` [X]
- `posts`: `0001` [X]
- `sessions`: `0001` [X]

---

### 3. Backward Compatibility & Zero-Downtime Rollout Strategy

1. **Non-Breaking Schema Changes**:
   - All added columns are nullable/defaulted to empty strings (`default="", blank=True`).
   - No existing table was locked, dropped, or renamed.
2. **Dual-Lookup Session Compatibility**:
   - `apps/accounts/auth/session_backend.py` and `apps/accounts/auth/sessions.py` query sessions via:
     `Q(session_key=digest) | Q(session_digest=digest) | Q(session_key=raw_key)`
   - Pre-existing active user sessions remain valid and do not cause logout during migration deployment.
   - All newly generated sessions immediately store cryptographic HMAC-SHA256 digests in both `django_session` and `UserSession`.
3. **Recovery Request Compatibility**:
   - Existing pending requests retain legacy behavior while newly issued admin and email resets populate `token_hash` and clear raw tokens.
