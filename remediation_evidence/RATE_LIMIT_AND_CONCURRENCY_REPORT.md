# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Rate Limiting and Concurrency Report (Workstream F)

- **Document ID**: `REMED-RATELIMIT-CONC-01`
- **Date**: 20 November 2026
- **Status**: PASSED
- **Security Priority**: P1

---

### 1. Workstream F: Rate Limiting Fail-Closed Architecture

#### A. Multi-Worker Distributed Throttling
Salow Workspace enforces rate limiting via `RateLimitMiddleware` backed by the central database table `RateLimitRecord`. This guarantees identical throttle state across horizontally scaled Gunicorn WSGI workers.

- **Sensitive Endpoints** (`/auth/login/`, `/iam/login/`, `/auth/recovery/`, `/auth/activate/`):
  - Limit: **5 requests per minute** per client IP / employee ID.
  - Exceeding limit: Returns **HTTP 429 Too Many Requests** with `Retry-After: 60` header.
- **Standard Application Endpoints**:
  - Limit: **120 requests per minute**.

#### B. Fail-Closed Resilience
- **Vulnerability**: If the database cluster experiences transient downtime or connectivity blips, a naive rate limiter fails open, allowing brute-force password spraying during degraded states.
- **Remediation**:
  In `apps/core/middleware.py`:
  ```python
  except Exception as e:
      logger.error("Rate limit database check error: %s", e)
      if not settings.DEBUG and is_sensitive:
          # Fail closed: reject sensitive requests when rate limiter store is unreachable
          return HttpResponse(
              "Security throttle service temporarily unavailable. Please retry shortly.",
              status=503,
              content_type="text/plain",
          )
  ```
  In production mode, failures on sensitive authentication paths return HTTP 503, preventing bypass.

---

### 2. Distributed Concurrency & Pessimistic Locking Audit

The concurrency test suite (`scripts/verify_tidb_concurrency.py`) was executed against the central TiDB Cloud serverless database (`salow_workspce`) under 5 simultaneous worker threads.

#### Concurrency Verification Summary:
1. **Concurrent TOTP Code Submission (5 Threads)**:
   - Worker 0 accepted; Workers 1-4 rejected with `AUTH-MFA-003`.
   - **Result**: Exactly 1 accepted, 4 rejected -> **PASS**.
2. **Concurrent Recovery Code Redemption (5 Threads)**:
   - Worker 1 redeemed; Workers 0, 2, 3, 4 rejected.
   - **Result**: Exactly 1 redeemed, 4 rejected -> **PASS**.
3. **Concurrent Activation Token Consumption (5 Threads)**:
   - Worker 1 consumed; Workers 0, 2, 3, 4 rejected.
   - **Result**: Exactly 1 consumed, 4 rejected -> **PASS**.
4. **Concurrent Rate-Limit Updates (5 Threads, 10 Inserts)**:
   - 10/10 successful inserts, atomic row counter = 10.
   - **Result**: Atomic rate limit update -> **PASS**.
5. **Concurrent Session Revocation & Verification (5 Threads)**:
   - Worker 0 executed revocation; Workers 1-4 immediately observed 0 active sessions.
   - **Result**: Instantaneous cluster-wide purge -> **PASS**.
6. **Concurrent MFA Replacement Token Consumption (5 Threads)**:
   - Worker 0 consumed; Workers 1-4 rejected.
   - **Result**: Exactly 1 consumed, 4 rejected -> **PASS**.
7. **Outbox Event Atomic Claiming (5 Threads)**:
   - Worker 1 claimed event using `select_for_update(skip_locked=True)`; Workers 0, 2, 3, 4 bypassed.
   - **Result**: Exactly 1 worker claimed -> **PASS**.

#### Final Verdict:
**ALL 7 TIDB CLOUD CONCURRENCY & REPLAY AUDIT TESTS PASSED [PASS]**.
