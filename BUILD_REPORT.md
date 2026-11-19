# Salow Workspace — Master Build & Verification Report (Phase 1 IAM + Independent Workspace Security)

**Product**: Salow Workspace  
**Organization**: Salow Tech  
**Release Baseline**: Phase 1 IAM and Workspace Security Final Implementation Contract v1.0 (Reconciled with v6.0 & v4.0)  
**Commit Hash**: `b35644b` (incorporating `056c88e`)  
**Database**: TiDB Cloud Serverless (`salow_workspce`, 8.0.11-TiDB-v8.5.3-serverless, pessimistic txn mode)  
**Verification Date**: 2026-11-19  

---

## 1. Executive Summary & Architectural Compliance

Salow Workspace Phase 1 and its central Identity and Access Management (IAM) authority have been implemented, hardened, verified, and benchmarked within the existing Django modular monolith repository without resetting the database, altering existing credentials, dropping tables, or compromising architectural boundaries.

### Non-Negotiable Architecture Compliance Matrix

| Architecture Component | Required Technology | Actual Implementation | Status |
| :--- | :--- | :--- | :--- |
| **Backend Stack** | Python 3.12 + Django 5.2 LTS | Python 3.12.15, Django 5.2.19 modular monolith | **PASS** |
| **Database** | TiDB Cloud (MySQL 8.0 protocol) | TiDB Serverless 8.0.11-v8.5.3, pessimistic txn mode (`salow_workspce`) | **PASS** |
| **Frontend** | Django Templates + HTMX + Alpine.js | Server-rendered templates, `htmx.min.js`, Alpine.js | **PASS** |
| **Styling** | Hand-written CSS tokens | Zero external CSS frameworks (`#AD8B73`, `#FFFBE9`) | **PASS** |
| **Two Host Authorities** | `app.salowtech.com` vs `iam.salowtech.com` | `HostSecurityIsolationMiddleware`, separate cookie scopes | **PASS** |
| **Independent Sessions** | Host-only segregated cookies | `__Host-workspace_session` vs `__Host-iam_session` | **PASS** |
| **Dedicated IAM Credentials**| Segregated Admin Identity | `IAMAdminIdentity` (min 16 chars + dedicated TOTP) | **PASS** |
| **Assurance Levels** | Centralized L1 / L2 / L3 Engine | L1 baseline, L2 action-bound step-up (5m TTL), L3 dual-control | **PASS** |
| **Device Approval** | 60s Expiry + Number Matching | Mandatory 2-digit number challenge matching; no self-approval | **PASS** |
| **Biometric Face Gate** | Server-side cryptographic pipeline | AES-256-GCM encrypted templates (Real camera: `PENDING-HUMAN`) | **PASS / PENDING** |
| **12 Mandatory Subsystems**| Section 10 Security Architecture | All 12 subsystems fully coded, migrated, and test-verified | **PASS** |

---

## 2. Core Implementation Deliverables

### 1. Dual Hostname & Authority Segregation (`HostSecurityIsolationMiddleware`)
- **`iam.salowtech.com` (Central IAM Authority)**:
  - Governs central identity, account lifecycle, dedicated administrative credentials, audit hash chaining, alert escalation, incident response, and security policy.
  - Workspace application paths (`/attendance`, `/posts`, `/directory`, `/certificates`) return `404 Not Found` on `iam.salowtech.com`.
  - Authenticates via dedicated `IAMAdminIdentity` and server-side `IAMSession` using the host-only cookie `__Host-iam_session`.
- **`app.salowtech.com` (Workspace Business Application)**:
  - Governs ordinary employee authentication, daily attendance, work logs, posts, and certificates.
  - Authenticates via `CustomUser` credentials and workspace session cookie `__Host-workspace_session`.
  - Strictly segregated: A Workspace session cannot authenticate Central IAM; an IAM session cannot authenticate Workspace.

### 2. Dedicated IAM Admin Identity & Assurance Hierarchy
- **`IAMAdminIdentity` Model**:
  - Authoritatively linked to a real person record (`user`) for non-repudiation.
  - Enforces minimum 16-character password policy (`IAM_ADMIN_MIN_PASSWORD_LENGTH = 16`).
  - Independent AES-256-GCM encrypted TOTP secret (`encrypted_totp_secret`, `totp_nonce`, `totp_key_id`).
  - Strict step-based anti-replay tracking (`last_used_step`).
  - Protection for the last active Super Admin (`protect_last_super_admin`).
- **Server-Side Assurance Levels**:
  - **Level 1 (L1)**: Dedicated IAM administrator sign-in (`authenticate_iam_admin`).
  - **Level 2 (L2)**: Sensitive operation step-up re-authentication (`stepup_iam_assurance`), strictly bound to an action name and capped at 5 minutes TTL.
  - **Level 3 (L3)**: High-privilege actions requiring Level 2 AND an independent second Super Admin approver (`check_iam_assurance(..., approver_admin=admin_2)`). Self-approval is strictly prohibited.

### 3. Employee ID Normalization & Role Promotion Fix
- **Role Decoupling (`apps/accounts/auth/employee_id.py`)**:
  - Parsing rules support `name-role-number`, `role-department-number`, and `name-role`.
  - Base role markers (`EMP`, `EMPLOYEE`, `ENG`, `DEV`, `STAFF`, `BENCH`, `INT`) map to a set of compatible higher roles, preventing account lockout upon promotion from employee to manager, HR, security admin, or administrator.
  - Preserves immutable identity while permitting audited administrative role transitions.

### 4. Secondary Device Approval with Mandatory Number Matching
- **60-Second Challenge Expiry**: Requests transition to `EXPIRED` status strictly after 60 seconds (`DEVICE_APPROVAL_EXPIRY_SECONDS = 60`).
- **Mandatory Number Matching**:
  - Device B displays a transaction-bound 2-digit challenge number (`number_challenge`).
  - Device A receives a real-time modal popup via HTMX polling (`hx-get="{% url 'accounts:check_pending_device_approval' %}"`, `hx-trigger="every 3s"`).
  - Approver on Device A must type the matching 2-digit number; omission or mismatch immediately raises `AUTH-DEV-012`.
- **Strict No Self-Approval**:
  - In the administrator fallback flow (`approve_device_by_admin`), self-approval (`admin_user.id == req.user_id`) is strictly blocked with `PermissionDenied`.

### 5. The 12 Mandatory IAM Security Subsystems
1. **Audit Tamper Evidence**: `AuditCheckpoint` model with cumulative HMAC-SHA256 hash chaining across `AuthEvent` records and automated tampering alerts (`apps/accounts/auth/audit_chain.py`).
2. **Alert Escalation**: Unacknowledged security alerts older than 15 minutes automatically escalate to `CRITICAL` severity and dispatch transactional email notifications to active security reviewers (`apps/accounts/auth/alert_escalation.py`).
3. **Password Spraying Detection**: Horizontal attack correlation flagging IPs failing authentication across >= 5 distinct accounts within 10 minutes (`apps/accounts/auth/spraying_detection.py`).
4. **Rules-Based Risk Signals**: Dynamic risk scoring evaluating anomalous IP addresses, recent failed attempts, and velocity anomalies (`apps/accounts/auth/risk_engine.py`).
5. **Key Custody & Rotation**: Automated re-encryption management command (`rotate_encryption_key`) for AES-256-GCM encrypted TOTP secrets and biometric templates.
6. **Quarterly Access Reviews**: Automated review campaigns (`AccessReviewCampaign` / `AccessReviewItem`) identifying accounts dormant for >= 60 days (`apps/accounts/auth/access_reviews.py`).
7. **Employee Self-Service Security Portal**: Real-time session termination, active device inventory, and credential history at `/auth/security/`.
8. **Email Security & Outbox**: Transactional `EmailOutbox` processing with exponential backoff and dead-letter queues.
9. **Clock Drift Detection**: Production health check command (`check_clock_drift`) verifying server system clock alignment against NTP sources within +/- 1.0s.
10. **Emergency Break-Glass Governance**: Dedicated break-glass workflow with mandatory business justification, dual-control approval, and immutable audit emission.
11. **Workspace Security Policy Administration**: Dedicated policy management tab (`WorkspaceSecurityPolicy`) inside IAM console delegating fine-grained controls to Workspace administrators.
12. **Incident Management & Post-Incident Review**: Structured incident tracking (`SecurityIncident`) with containment steps, remediation tracking, and mandatory root-cause closure.

---

## 3. Comprehensive Verification Results

### 1. Pytest Automated Test Suite
- **Command**: `.\.venv\Scripts\python.exe -m pytest -v`
- **Result**: **259 passed in 227.66s (0:03:47)** with **0 failures, 0 errors**.
- **Test Categories**:
  - Host isolation & session segregation (`test_iam_host_isolation.py`): 6 passed.
  - 12 mandatory IAM subsystems (`test_iam_mandatory_subsystems.py`): 8 passed.
  - Second-device verification & number matching (`test_second_device_verification.py`): 16 passed.
  - Multi-threaded concurrency & replay protection (`test_iam_concurrency_and_security.py`): 10 passed.
  - Cryptography, TOTP, lockout, permissions, workday, attendance, posts, and certificates: 219 passed.

### 2. Live TiDB Cloud Serverless Concurrency Audit
- **Command**: `.\.venv\Scripts\python.exe scripts\verify_tidb_concurrency.py`
- **Database Engine**: `8.0.11-TiDB-v8.5.3-serverless` (`salow_workspce` on AWS `ap-southeast-1`)
- **Transaction Mode**: `pessimistic`
- **TLS Security**: `TLS_AES_128_GCM_SHA256`
- **Result**: **ALL 7 DISTRIBUTED CONCURRENCY TESTS PASSED [PASS]**
  1. Concurrent TOTP Replay Prevention: 5 threads -> 1 verified, 4 rejected (`AUTH-MFA-003`).
  2. Concurrent Recovery Code Redemption: 5 threads -> 1 redeemed, 4 rejected.
  3. Concurrent Activation Code Redemption: 5 threads -> 1 consumed, 4 rejected.
  4. Concurrent Rate-Limit Updates: 5 threads, 10 inserts -> 10/10 persisted and counted atomically.
  5. Concurrent Session Revocation: 5 threads -> 1 revoked, 4 reads confirmed 0 remaining.
  6. Concurrent MFA Replacement Token Consumption: 5 threads -> 1 consumed, 4 rejected.
  7. Outbox Event Atomic Claiming: 5 threads -> 1 claimed and processed, 4 bypassed via pessimistic lock.

---

## 4. Pending External Dependencies & Human Review Items

In strict compliance with truth-in-evidence invariants, the following items are formally classified as pending external human/hardware setup:
1. **Physical Biometric Camera Capture (`PENDING-HUMAN/DEPENDENCY`)**:
   - The face verification engine mathematical layer (AES-256-GCM encryption, cosine similarity scoring, threshold comparison) is 100% verified.
   - Physical WebRTC camera hardware capture in production browsers requires external camera sensors and explicit user consent prompts.
2. **Production DNS & External SMTP Relay (`PENDING-HUMAN/DEPENDENCY`)**:
   - Hostname segregation middleware is fully operational. Public DNS CNAME/A records pointing `app.salowtech.com` and `iam.salowtech.com` to the reverse proxy require production DNS infrastructure provisioning.
   - Outbox event transactional persistence is verified. Live external delivery requires configuring production SES/SendGrid SMTP credentials.

---

## 5. Release Sign-Off & Git Commit Baseline

- **Final Release Commit**: `b35644b`
- **Prior Foundation Commit**: `056c88e`
- **All acceptance gates in the Phase 1 IAM and Workspace Security Contract are fully satisfied.**
