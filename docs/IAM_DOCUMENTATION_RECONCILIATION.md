# Salow Workspace — IAM Documentation Reconciliation Report

**Directive**: Salow Workspace IAM v1.0 Forensic Verification & Security Correction  
**Stage**: Stage 1 — Resolve Documentation Contradictions  
**Timestamp**: 2026-11-17T09:12:00+05:30  
**Auditor**: Senior IAM & Security Verification Engineer  
**Scope**: All architectural, operational, and delivery specifications in `docs/`  

---

## 1. Executive Summary

A comprehensive multi-document cross-inspection of the 13 IAM documentation files revealed specific discrepancies between initial conceptual drafts and the verified codebase implementation. This report provides authoritative reconciliation across all identified areas, ensuring documentation describes the verified reality of the Salow Workspace system.

---

## 2. Session Architecture Reconciliation

### 2.1 The Contradiction
- `AUTHENTICATION.md` Section 1 casually referenced *"encrypted cookies"* for browser session management.
- `SESSION_SECURITY.md` Section 1 correctly identified Django's database-backed session engine (`django.contrib.sessions.backends.db.SessionStore`).

### 2.2 Reconciled Architectural Truth
Salow Workspace utilizes **server-side database-backed session persistence**, NOT encrypted client-side cookies.
1. **Browser Session Cookie Contents**:
   - The cookie named `sessionid` contains **only an opaque, high-entropy 32-character pseudo-random session key** (e.g., `s7df8g9s7df...`).
   - The cookie contains **zero** serialized user attributes, zero tokens, zero roles, and zero sensitive state.
2. **Server-Side Storage**:
   - The authoritative session payload is stored server-side in the `django_session` database table (on TiDB Cloud).
   - Session data is serialized using Django's JSON serializer and base64-encoded with a keyed HMAC-SHA256 signature to guarantee server-side integrity.
3. **Cookie Security Attributes**:
   - `SESSION_COOKIE_HTTPONLY = True`: Blocks access from JavaScript, neutralizing session theft via DOM/XSS injection.
   - `SESSION_COOKIE_SAMESITE = 'Lax'`: Mitigates Cross-Site Request Forgery (CSRF) across cross-site navigations.
   - `SESSION_COOKIE_SECURE = True` (production): Restricts transmission exclusively to TLS (HTTPS) channels.
   - `SESSION_COOKIE_AGE = 28800`: Enforces an 8-hour absolute session ceiling.
4. **Session Identifier Rotation**:
   - Upon successful password + MFA verification, `request.session.cycle_key()` is called, invalidating the pre-authentication session key and minting a fresh key to eliminate session fixation vulnerabilities.
   - Password changes and administrative revocations similarly cycle or purge the session key.
5. **Session Revocation**:
   - Handled via `apps.accounts.auth.sessions.revoke_user_sessions(user)`, which deletes all matching records from `apps.accounts.models.UserSession` and deletes corresponding database rows from `django_session`.
6. **Session Expiry & Data Protection**:
   - 15-minute idle timeout is enforced server-side by `AccountSecurityMiddleware`.
   - Work-in-progress state is preserved server-side via explicit client-initiated autosave to `/auth/security/draft/save/` (`WorkspaceDraft` model), not in client-side localStorage.

---

## 3. TiDB Database Identity Reconciliation

### 3.1 The Contradiction
- `IAM_INITIAL_VERIFICATION.md` line 7 contained the string `salow_workspce`.
- `.env.example`, `config/settings/dev.py`, `config/settings/prod.py`, and project specifications state `salow_workspace`.

### 3.2 Reconciled Truth
- Inspection of `config/settings/dev.py` line 28:
  ```python
  "NAME": os.environ.get("TIDB_DB_NAME", "salow_workspace")
  ```
- Inspection of `.env.example` line 33:
  ```ini
  TIDB_DB_NAME=salow_workspace
  ```
- **Conclusion**: The authoritative database name is **`salow_workspace`**. The string `salow_workspce` in `IAM_INITIAL_VERIFICATION.md` was an isolated clerical typo (omission of the letter 'a') in the audit report header. The actual configuration, migrations, and database connection strings have always used `salow_workspace`. No destructive database renaming or table migration is required.

---

## 4. Recovery Authority & Scope Hierarchy Reconciliation

### 4.1 The Contradiction
Documentation previously interchanged the terms *"direct reporting line"* and *"direct reporting tree"*, creating ambiguity regarding whether managers could reset accounts of indirect multi-level subordinates.

### 4.2 Reconciled Truth & Enforced Policy
Salow Workspace enforces a **Direct Manager or Assigned Team Lead** recovery policy in `apps.accounts.auth.reset.check_recovery_authority()`:
```python
is_subordinate = ReportingRelationship.objects.filter(
    manager=actor, subordinate=target_user
).exists()
is_team_member = Team.objects.filter(
    lead=actor, memberships__user=target_user
).exists()
```
A manager may ONLY initiate recovery for an employee or intern if:
1. The manager is the target's **direct manager** (`ReportingRelationship`), OR
2. The manager is the target's **assigned team lead** (`Team.lead`).
Recursive tree traversal is deliberately excluded to prevent organizational overreach.

### 4.3 Comprehensive Recovery Hierarchy
| Initiator Role | Permitted Target Roles | Governance & Constraints |
|---|---|---|
| **Super Admin** | Any Employee / Intern / Manager / HR / Admin / Super Admin | Target `SUPER_ADMIN` requires approval from a second active Super Admin. System must maintain $\ge 2$ Super Admins. |
| **Admin** | Admin, HR, Manager, Employee, Intern | Strictly **DENIED** from recovering Super Admins. Target `ADMIN` requires approval from a second administrator. |
| **HR** | Employee, Intern only | Strictly **DENIED** from recovering Managers, Admins, or Super Admins. |
| **Manager** | Direct Subordinates / Assigned Team Members only | Strictly restricted to subordinates in active `ReportingRelationship` or led `Team`. |
| **Employee / Intern**| Self only | Self-service password reset using 1-of-10 single-use recovery codes or registered passkey. |

---

## 5. Capacity & Benchmark Documentation Reconciliation

### 5.1 The Contradiction
The initial `LOAD_TESTING.md` described a 1,000-user capacity test, but recorded an initial test execution consisting of 100 virtual users and 200 requests.

### 5.2 Reconciled Truth
1. The 100-user execution was an initial execution validation of the test harness (`scripts/load_test_1000_users.py`), not the completed 1,000-user full-scale benchmark.
2. Stage 9 of this directive executes the staged concurrency progression:
   - Stage A: 1 User (Baseline)
   - Stage B: Gradual Concurrency (10 $\rightarrow$ 25 $\rightarrow$ 50 $\rightarrow$ 100 $\rightarrow$ 250 $\rightarrow$ 500 $\rightarrow$ 1,000 users)
   - Stage C: Authentication Load (Argon2id + TOTP evaluation)
   - Stage D: Authenticated Workload (Navigation + Autosave)
   - Stage E: Abuse Workload (Throttling verification)
3. Full benchmark metrics and separated legitimate vs. throttled request distributions are documented in [docs/IAM_CAPACITY_BENCHMARK_RESULTS.md](file:///c:/Users/Dr%20Arif/Workspace.SalowTech/docs/IAM_CAPACITY_BENCHMARK_RESULTS.md).

---

## 6. Diagnostic Claims vs. Functional Security Differentiation

A successful configuration check does not prove functional security. Salow Workspace operational reporting strictly distinguishes the following diagnostic tiers:

| Tier | Meaning | Verification Method | Example Check in `auth_doctor` |
|---|---|---|---|
| **Tier 1: Configuration Verified** | Django settings and options are syntactically valid | Settings inspection | `PASSWORD_HASHERS[0] == 'Argon2PasswordHasher'` |
| **Tier 2: Dependency Available** | Required cryptographic / protocol libraries are loaded | Import inspection | `import webauthn; import pyotp; import argon2` |
| **Tier 3: Database Reachable** | Connection to database host succeeds | Cursor test query | `connection.cursor().execute("SELECT 1")` |
| **Tier 4: Migrations Applied** | Schema tables match Django model state | Executor plan check | `MigrationExecutor.migration_plan()` is empty |
| **Tier 5: Functional Security Verified** | Code correctly enforces authorization, locks, and replay defense under live test conditions | Automated test suite (`pytest`) | `test_concurrent_totp_replay_prevention` |
| **Tier 6: Production Staging Verified** | Real deployment behind reverse proxy with SSL and environment secrets | Production staging audit | HTTPS termination, TLS 1.3, CSP header check |

`python manage.py auth_doctor` executes Tiers 1 through 4 plus cryptographic round-trip sanity checks (Tier 5 functional verification is owned by `pytest`).
