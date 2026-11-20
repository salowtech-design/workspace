# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Gate 0: Baseline Verification & Environment Integrity Report

- **Document ID**: `REMED-GATE0-BASE-01`
- **Date**: 20 November 2026
- **Status**: PASSED
- **Author**: Principal Software Architect & Application Security Engineer
- **Target Repository**: Salow Workspace (`salowtech/salow-workspace`)

---

### 1. Executive Summary

This report documents the forensic verification and environment validation conducted under **Gate 0** of the *Controlled Engineering Remediation Contract*. Before any code modification or remediation task was executed, the repository state was forensically verified, tagged immutably, and validated against the remote central TiDB database.

All Gate 0 criteria have been satisfied with zero regressions and zero unauthorized state mutations.

---

### 2. Git Baseline Identification & Isolation

1. **Governing Baseline Commit**:
   - **Commit SHA**: `7a43c928863e1bdffaa89fd405d3e38080d5f400`
   - **Branch**: `main`
   - **Commit Date**: 2026-11-20
   - **Subject**: Phase 1 Complete Security Architecture and IAM Hardening Baseline
2. **Immutable Baseline Tag**:
   - Tag created: `baseline-remediation-7a43c92`
   - Points directly to commit `7a43c928863e1bdffaa89fd405d3e38080d5f400`.
3. **Dedicated Remediation Branch**:
   - Active working branch: `remediation/security-hardening-v1`
   - Branch derived directly from `baseline-remediation-7a43c92`.

---

### 3. Environment & Runtime Specifications

| Component | Verified Version / Engine | Host / Environment |
|---|---|---|
| **Python Runtime** | Python `3.12.15` (64-bit Windows) | Local Developer VirtualEnv (`.\.venv`) |
| **Django Framework** | Django `5.2.19 LTS` | Modular Monolith Architecture |
| **Primary Database** | TiDB `v8.5.3-serverless` (MySQL 8.0 Protocol) | AWS `ap-southeast-1` via TLS |
| **Database Driver** | `mysqlclient 2.3.1` / `django-tidb 5.2.1` | Native C extension + TLS CA verification |
| **Test Database** | SQLite 3 (`test_db.sqlite3` with WAL mode) | Local file-based test runner |
| **Cryptography** | `cryptography 50.0.2` (AES-256-GCM, Argon2id) | Native OpenSSL 3.x bindings |
| **TOTP / MFA** | `pyotp 2.11.2` (RFC 6238 TOTP) | Server-side time-step validation |
| **Passkey / FIDO2** | `webauthn 3.0.1` (W3C WebAuthn Level 3) | FIDO2 / CTAP2 compliance |
| **Browser Runner** | `playwright 1.65.2` (Chromium 159.0.8154.6) | Headless browser automation |

---

### 4. Baseline Database & Migration State

At baseline inspection, all 54 existing Django migrations across 11 applications were confirmed as applied on the remote TiDB database (`salow_workspce`):
- `accounts`: `0001_initial` through `0010_auditcheckpoint_key_id_biometricassertion_and_more`
- `admin`: `0001_initial` through `0003_logentry_add_action_flag_choices`
- `attendance`: `0001_initial` through `0002_attendancecorrection_attendancefallbackrequest`
- `auth`: `0001_initial` through `0012_alter_user_first_name_max_length`
- `certificates`: `0001_initial`
- `contenttypes`: `0001_initial`, `0002_remove_content_type_name`
- `core`: `0001_initial` through `0004_emailoutbox`
- `directory`: `0001_initial`
- `face`: `0001_initial`, `0002_biometricnoticeacknowledgement_facetemplate_and_more`
- `posts`: `0001_initial`
- `sessions`: `0001_initial`

Zero migrations were unapplied or pending at Gate 0.

---

### 5. Baseline Automated Test Verification

Before any remediation mutations, the entire existing automated test suite was executed:
1. **Pytest Unit and Security Suites**:
   - Command: `.\.venv\Scripts\python.exe -m pytest tests/unit/ tests/security/`
   - Output: `262 passed in 251.78s (0:04:11)`
   - Failures: 0, Errors: 0
2. **Distributed TiDB Concurrency Verification**:
   - Command: `.\.venv\Scripts\python.exe scripts/verify_tidb_concurrency.py`
   - Output: `ALL 7 TIDB CLOUD CONCURRENCY & REPLAY AUDIT TESTS PASSED [PASS]`
   - Tests:
     - Test 1: Concurrent TOTP Replay Prevention (5 Threads) -> PASS
     - Test 2: Concurrent Recovery Code Redemption (5 Threads) -> PASS
     - Test 3: Concurrent Activation Code Redemption (5 Threads) -> PASS
     - Test 4: Concurrent Rate-Limit Updates on TiDB (5 Threads) -> PASS
     - Test 5: Concurrent Session Revocation & Verification (5 Threads) -> PASS
     - Test 6: Concurrent MFA Replacement Token Consumption (5 Threads) -> PASS
     - Test 7: Outbox Event Atomic Claiming (5 Threads) -> PASS

---

### 6. Gate 0 Acceptance Verdict

- **Verdict**: **PASSED**
- **Justification**: Baseline repository state is clean, isolated on a dedicated branch, backed by an immutable tag, connected to authoritative TiDB Cloud, and fully green on all 269 baseline tests.
