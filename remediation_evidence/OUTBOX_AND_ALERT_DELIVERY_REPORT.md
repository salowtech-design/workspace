# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Transactional Outbox and Alert Delivery Report (Workstream G)

- **Document ID**: `REMED-OUTBOX-01`
- **Date**: 20 November 2026
- **Status**: PASSED (Outbox Engine) / BLOCKED (External SMTP Relay)
- **Security Priority**: P1

---

### 1. Transactional Outbox Pattern Architecture

To eliminate two-phase commit risks between database transactions and external email/notification services, Salow Workspace utilizes the **Transactional Outbox Pattern** (`apps/core/email_service.py`).

1. **Atomic Transaction Enqueueing**:
   - Notifications and activation links are inserted into the `EmailOutbox` table in the *same* database transaction as the business operation.
   - If the database transaction rolls back, no email is queued.
2. **Confidential Payload Encryption**:
   - Plaintext `body_text` in `EmailOutbox` contains only sanitized non-sensitive messages:
     `"[CONFIDENTIAL LINK ENCRYPTED IN SECURE PAYLOAD]"`.
   - Full activation links, reset tokens, and security notices are serialized to JSON and encrypted using AES-256-GCM (`encrypted_payload`, `payload_nonce`, `key_id`).
   - Even with full read access to `EmailOutbox`, an attacker cannot extract usable reset tokens without the cryptographic key.

---

### 2. Multi-Worker Concurrency & Stale Lease Recovery

- **Atomic Row Claiming**:
  `process_email_outbox` queries pending messages with:
  ```python
  pending = list(
      EmailOutbox.objects.select_for_update(skip_locked=True)
      .filter(status=EmailOutbox.Status.PENDING)
      .order_by("created_at")[:batch_size]
  )
  ```
  `skip_locked=True` prevents lock contention, allowing multiple worker processes to poll the outbox concurrently without duplicate deliveries.
- **Stale Lease Recovery**:
  If a worker crashes while in `PROCESSING` state, rows with `claimed_at < now - 10 minutes` are automatically reclaimed and reset to `PENDING`.
- **Payload Sanitization upon Delivery**:
  Once an email is dispatched successfully, `encrypted_payload` and `payload_nonce` are cleared to null (`b""`) to enforce forward privacy.

---

### 3. Background Outbox Worker Daemon

A dedicated background management command is provided:
```bash
python manage.py process_email_outbox --loop --interval 10
```
This is configured as a standalone container service in `docker-compose.yml` (`worker`).

---

### 4. Security Operations Alert Delivery

- `SecurityAlert` models record critical operations incidents (tamper attempts, NTP desynchronization, brute-force lockouts).
- Alerts are persisted durably and dispatched via the outbox engine to designated security personnel.

---

### 5. External Dependency Blocker Classification

- **Status**: **BLOCKED — EXTERNAL DEPENDENCY**
- **Dependency**: External SMTP Relay (AWS SES, SendGrid, or corporate SMTP server).
- **Current State**: The transactional outbox pipeline, encryption, claiming, daemon loop, and retry backoff are fully verified. Outbound delivery to external email inboxes requires SMTP credentials provisioned in `.env` (`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`).
