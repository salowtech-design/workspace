# TEST SUITE & VERIFICATION EVIDENCE AUDIT REPORT
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable G — Actual Tests, Execution Results, Load Evidence and Unverified Claims

---

## 1. Test Suite Inventory & Test Discovery

Automated test discovery was executed using `pytest --collect-only`.

- **Total Test Cases Discovered**: Exactly **243 automated tests**.
- **Total Test Files**: **24 test files** across 2 active test directories:
  - `tests/security/`: 1 test file (`test_pen_test_suite.py`, 13 tests).
  - `tests/unit/`: 23 test files (230 tests).
- **Empty / Inactive Directories**:
  - `tests/e2e/`: Contains only `__init__.py` (0 tests). End-to-end testing is conducted via `scripts/verify_e2e_journeys.py`.
  - `tests/integration/`: Contains only `__init__.py` (0 tests). Integration testing is handled by `scripts/verify_tidb_concurrency.py`.
- **Resolution of the 235 vs 243 Discrepancy**:
  - The stale `junit-report.xml` in the repository root recorded 235 tests.
  - Commit `7289a38` added 8 additional unit tests covering Section 6 employee ID syntax validation, dual-device biometrics, and SecOps alerting, bringing the true active test count to **243 tests**.

---

## 2. Test Execution & Pass Rates

### 2.1 Pytest Automated Suite
- **Command**: `.venv\Scripts\pytest.exe tests/ -q`
- **Execution Environment**: Local virtualenv (Python 3.12.15, Django 5.2.19, `config.settings.test` on SQLite).
- **Result**: **243 passed** in 170.82 seconds (100% pass rate).

### 2.2 End-to-End User Journeys (`scripts/verify_e2e_journeys.py`)
- **Execution Environment**: Multi-stage transactional client.
- **Results**: **ALL 7 JOURNEYS PASSED**
  - Journey 1: First-time account activation & TOTP enrollment (`PASS`)
  - Journey 2: Returning employee sign-in with TOTP (`PASS`)
  - Journey 3: Returning employee sign-in with recovery code (`PASS`)
  - Journey 4: Dual-approval admin reset for Super Admin (`PASS`)
  - Journey 5: Account suspension immediate session ejection (`PASS`)
  - Journey 6: Administrative console segregation (`PASS`)
  - Journey 7: Workspace module regression checks (`PASS`)

### 2.3 HTTP & View Forensic Audit (`scripts/verify_http_endpoints.py`)
- **Results**: **ALL CHECKS PASSED**
  - Security headers present (CSP, X-Frame-Options: DENY, nosniff, Referrer-Policy).
  - Landing page progressive disclosure and password visibility toggle verified.
  - Role-based boundaries enforced (Intern receives HTTP 403, Admin receives HTTP 200).
  - Real-time live event stream verified (`Content-Type: text/event-stream`).

### 2.4 Distributed TiDB Cloud Concurrency Suite (`scripts/verify_tidb_concurrency.py`)
- **Target Engine**: Live TiDB Serverless 8.0.11-v8.5.3 (`salow_workspce` on AWS `ap-southeast-1`).
- **Results**: **ALL 7 DISTRIBUTED CONCURRENCY TESTS PASSED**
  - Test 1: Concurrent TOTP Replay Prevention — 1 accepted, 4 replayed requests rejected (`AUTH-MFA-003`).
  - Test 2: Concurrent Recovery Code Redemption — 1 redeemed, 4 rejected.
  - Test 3: Concurrent Activation Code Redemption — 1 consumed, 4 rejected.
  - Test 4: Concurrent Rate-Limit Updates on TiDB — 10/10 inserted and counted atomically.
  - Test 5: Concurrent Session Revocation — Immediate cross-connection purge verified.
  - Test 6: Concurrent MFA Replacement Token Consumption — 1 consumed, 4 rejected.
  - Test 7: Outbox Event Atomic Claiming — 1 claimed, 4 bypassed via pessimistic locking.

---

## 3. Forensic Evaluation of the Claimed "1,000-User Capacity Benchmark"

The previous report claimed: *"Capacity Confirmed: The Salow IAM Platform comfortably supports 1,000 concurrent active users with p95 latency < 50ms (Grade A+)"*.

An independent forensic inspection of `scripts/load_test_1000_users.py` reveals that this claim is **unverified and based on an in-process synthetic simulation**:

1. **Database Engine**: Line 25 explicitly forces `DJANGO_SETTINGS_MODULE = "config.settings.test"`. The test ran entirely against a single-file SQLite database (`test_db.sqlite3`), not the distributed TiDB Cloud cluster.
2. **Client Layer**: Line 33 imports `from django.test import Client`. All requests are in-memory Python function calls bypassing the real HTTP network stack, TLS termination, TCP handshakes, reverse proxy buffering, and Gunicorn worker processes.
3. **Thread Cap**: Line 171 bounds thread execution: `max_workers = min(tier, 25)`. A maximum of 25 worker threads executed concurrently, not 1,000.
4. **Bypassed Cryptography**: In Stage B (lines 149-160), users are authenticated via `client.force_login(u)` with a pre-set session flag `s["_salow_mfa_verified"] = True`. Real Argon2id password hashing (which consumes ~2.6 seconds per login) and RFC 6238 TOTP cryptographic decryption were completely bypassed.
5. **SQLite Locking Retries**: Lines 164-167 contain explicit retry blocks:
   ```python
   if "database is locked" in str(e).lower() and attempt < 4:
       time.sleep(0.05 * (attempt + 1))
       continue
   ```
   Under 30 concurrent threads in Stage D, SQLite write locking caused 782 draft save failures (only 21.8% success), and p95 latency spiked to 20,606 ms.

### Verdict on 1,000-User Capacity:
- **Status**: **NOT VERIFIED**.
- **Finding**: While the application logic and pessimistic transactions are concurrency-safe, real-world capacity of 1,000 simultaneously active HTTP users against TiDB Cloud under Gunicorn multi-worker conditions remains unproven.

---
*Deliverable G complete. All 243 tests and benchmark scripts verified with direct forensic evidence.*
