# Salow Workspace IAM — Security & RBAC Enforcement Report

**Document Version:** 2.0  
**Scope:** Security Invariants, Threat Mitigations, Anti-Replay Protections, Dual-Control Policies, and Role Matrix  
**Status:** **Strictly Enforced & Validated**

---

## 1. Security Architecture & Threat Mitigations

The Salow Workspace IAM enforces defense-in-depth principles across authentication, authorization, credential management, and transport security.

### 1.1 Anti-Enumeration & Identity Privacy
- **Login Endpoint (`/auth/login/`):** Returns identical generic response `"Invalid employee ID or password."` regardless of whether the Employee ID exists or password is incorrect.
- **Activation Wizard (`/auth/activate/`):** Code verification failure yields a generic `"Invalid or expired activation code."` without revealing whether the code format is valid or bound to an account.
- **Recovery Hub (`/auth/recovery/forgot-password/`):** All three recovery methods suppress account existence indicators:
  - Method 1 returns `"If an eligible active account matches this Employee ID, a recovery request has been routed to the administrator queue."`
  - Method 2 returns `"Recovery code is invalid or has already been used."` or neutral email link dispatch.
  - Method 3 returns identical feedback regardless of whether the email address is verified.

### 1.2 Dual-Control & Delegated Recovery Authority
Administrative actions are protected by strict server-side policy guards in `apps/accounts/auth/reset.py`:
- **Super Admin Invariant:** A Super Admin's credentials can NEVER be reset by a single administrator or via self-service TOTP. Dual-control approval from a second, distinct active Super Admin is strictly required. Self-approval is rejected at the database and service boundaries.
- **Admin Invariant:** Standard Administrators cannot recover or reset Super Admin credentials. Target Admin recovery requires sign-off from a second administrator.
- **HR Invariant:** HR personnel can only initiate recovery for workforce classifications (`EMPLOYEE` and `INTERN`). Attempts to recover Managers or Admins raise `PermissionDenied`.
- **Manager Invariant:** Managers can only initiate recovery for direct subordinates within their reporting hierarchy (`ReportingRelationship`) or led teams. Attempts to recover outside employees raise `PermissionDenied`.

### 1.3 Step-Up (Sudo Mode) Authentication
- **Window:** 10 minutes (`SUDO_WINDOW_MINUTES = 10`).
- **Authorization Storage:** Authoritatively bound to the user's server-side session (`_salow_sudo_granted_at`).
- **Super Admin Requirement:** In addition to password verification, Super Admin step-up requires submission of a fresh 6-digit TOTP code.
- **Protected Actions:** Passkey revocation, administrative recovery approvals, credential deletions, and elevated settings modifications.

### 1.4 Session Management & Inactivity Lifecycle
- **Absolute Lifetime:** Baseline 12 hours for staff accounts.
- **Idle Lock:** 15 minutes of inactivity engages an interactive idle lock screen requiring password unlock.
- **Session Fixation Prevention:** Session keys are recycled (`request.session.cycle_key()`) upon every authentication event.
- **Instant Revocation:** Password changes, status transitions (`SUSPENDED`, `EXITED`), role changes, and MFA resets immediately revoke all existing device sessions across the database.
- **HTTP Methods:** Logout is strictly restricted to `POST` requests with CSRF verification.

### 1.5 Rate Limiting & Distributed Coordination
- **Multidimensional Tracking:** Implemented in `RateLimitMiddleware` using both client IP and Employee ID dimensions (`acc:<id>:sensitive`).
- **Proxy Header Resolution:** `X-Forwarded-For` is inspected only when the request originates from a pre-configured trusted proxy IP (`127.0.0.1`, `::1`, `localhost`). Untrusted callers cannot spoof remote IPs.
- **Rate Limit Window:** 5 requests per 60 seconds on sensitive authentication endpoints (`/auth/login`, `/auth/recovery`, `/auth/activate`). 300 requests per 60 seconds for general endpoints.
- **Cluster Coordination:** Request timestamps are stored in `RateLimitRecord` on TiDB Cloud, ensuring cluster-wide rate limiting across horizontal workers.

### 1.6 Replay & Race Condition Defense
- **RFC 6238 TOTP Anti-Replay:** `TOTPCredential.last_used_step` stores the 30-second time-step. Any token submitted within the same time-step or prior to `last_used_step` is immediately rejected.
- **Atomic Single-Use Codes:** Recovery codes and activation tokens are consumed inside `transaction.atomic()` with row locks (`select_for_update`), guaranteeing that concurrent requests cannot double-spend tokens.

---

## 2. Role-Based Access Control (RBAC) Matrix

Salow Workspace enforces a **central default-deny authorization policy**. Application permissions are determined solely by the database-assigned role, never inferred from the Employee ID prefix.

| Permission / Subsystem | Super Admin | Admin | HR | Manager | Employee | Intern |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Sign In & View Dashboard** | YES | YES | YES | YES | YES | YES |
| **Self-Service Password Change** | YES | YES | YES | YES | YES | YES |
| **Self-Service Recovery Code Reset** | YES | YES | YES | YES | YES | YES |
| **Self-Service TOTP Reset** | NO (Dual Required) | YES | YES | YES | YES | YES |
| **Self-Service Email Reset** | NO (Dual Required) | YES | YES | YES | YES | YES |
| **Self-Service Authenticator Replace** | NO (Dual Required) | YES | YES | YES | YES | YES |
| **Access Admin Recovery Queue** | YES | YES | YES | YES | NO (403) | NO (302) |
| **Recover Super Admin Account** | Dual-Control | NO | NO | NO | NO | NO |
| **Recover Admin Account** | YES | Dual-Control | NO | NO | NO | NO |
| **Recover HR Account** | YES | YES | NO | NO | NO | NO |
| **Recover Manager Account** | YES | YES | NO | NO | NO | NO |
| **Recover Employee Account** | YES | YES | YES | Scoped (Team) | NO | NO |
| **Recover Intern Account** | YES | YES | YES | Scoped (Team) | NO | NO |
| **View Audit Event Console** | YES | YES | NO | NO | NO | NO |
| **Run Auth Doctor Diagnostics** | YES | YES | NO | NO | NO | NO |
