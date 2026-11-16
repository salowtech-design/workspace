# Salow Workspace — Operations & Reliability Runbook

## 1. Platform Health & Readiness Monitoring

### Health Endpoints
- **Liveness Probe**: `/health` and `/health/`
  - Purpose: Basic process responsiveness. Does not touch the database.
  - Expected: `HTTP 200 OK` with JSON `{"status": "pass"}`.
  - Action if non-200: Process is hung or deadlocked. Restart the container/service.
- **Readiness Probe**: `/ready` and `/ready/`
  - Purpose: Full end-to-end database connectivity probe against TiDB / SQL storage.
  - Expected: `HTTP 200 OK` with JSON `{"status": "pass", "database": "connected"}`.
  - Fail-closed: `HTTP 503 Service Unavailable` on database connection loss without leaking credentials or stack traces.
- **Public Verification Service**: `/verify/` and `/verify/<serial_or_uuid>/`
  - Purpose: Anonymous credential verification portal.
  - Headers: Enforces `X-Robots-Tag: noindex, nofollow`. Rate limited to 60 req/min per IP.

---

## 2. TiDB Cloud Backup & Restoration Procedures

### Automated Daily Snapshots
- TiDB Cloud Starter automatically creates point-in-time snapshots.
- Retention: 7 days rolling.

### Manual Database Backup (Dumpling / mysqldump)
Run from a secure bastion host with TLS enabled:
```bash
mysqldump \
  --host=${TIDB_HOST} \
  --port=4000 \
  --user=${TIDB_USER} \
  --password=${TIDB_PASSWORD} \
  --ssl-ca=${TIDB_CA_PATH} \
  --single-transaction \
  --quick \
  --databases salow_workspace > salow_backup_$(date +%Y%m%d_%H%M%S).sql
```

### Restoration Runbook
1. Create a clean target database or spin up a restore branch in TiDB Cloud.
2. Ingest the backup dump:
   ```bash
   mysql \
     --host=${TIDB_HOST} \
     --port=4000 \
     --user=${TIDB_USER} \
     --password=${TIDB_PASSWORD} \
     --ssl-ca=${TIDB_CA_PATH} \
     salow_workspace < salow_backup_20261116.sql
   ```
3. Run Django schema migrations to verify parity:
   ```bash
   python manage.py migrate --check
   ```
4. Run diagnostics doctor:
   ```bash
   python manage.py auth_doctor
   ```

---

## 3. Cryptographic Key Ring Management & Secrets Rotation

### Key Ring Configuration
- Primary encryption: AES-256-GCM authenticated encryption for biometric templates and TOTP credentials.
- Key ring format in environment: `KEY_RING_JSON={"active": "k2", "keys": {"k1": "...", "k2": "..."}}`.

### Rotation Workflow
1. Generate a new cryptographically secure 256-bit key:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
2. Add the new key to `KEY_RING_JSON` under an incremental ID (e.g. `k2`).
3. Set `"active": "k2"` in the key ring JSON. Existing data encrypted with `k1` continues to decrypt seamlessly.
4. Restart workspace application instances.
5. Verify decryption using `python manage.py auth_doctor`.

---

## 4. Pilot Operations & Seeding Runbook

### Clean Pilot Seeding
To initialize the workspace with realistic company data across departments, teams, users, attendance, work logs, and certificates:
```bash
python manage.py seed_pilot_data
```
*Note: This command is idempotent and securely sets user credentials without logging plaintext passwords.*

### System Diagnostics
Always run prior to opening access to users:
```bash
python manage.py auth_doctor
python manage.py check --deploy
```

---

## 5. Security Incident & Emergency Procedures

### 1. Compromised or Disputed Certificate
1. Locate the certificate by serial number or UUID in the admin portal (`/certificates/`).
2. Submit the revocation form with an explicit reason.
3. The revocation is immediately active across all internal and public verification endpoints (`/verify/<serial>`).
4. An immutable audit record is logged to `AuditLog`.

### 2. Brute Force or Credential Stuffing Attack
- `RateLimitMiddleware` automatically throttles excessive requests with `HTTP 429 Too Many Requests` (60 req/min for sensitive paths).
- `LoginAttempt` lockout automatically locks accounts after 5 failed password attempts within 15 minutes.
- To inspect lockout status:
  ```bash
  python manage.py shell -c "from apps.accounts.models import LoginAttempt; print(list(LoginAttempt.objects.all()[:10]))"
  ```

### 3. Break-Glass Super Admin Access
- When normal authentication channels are blocked, Super Admin recovery codes can be redeemed at `/auth/login/recovery/`.
- All privileged operations in sudo mode expire after 15 minutes.
