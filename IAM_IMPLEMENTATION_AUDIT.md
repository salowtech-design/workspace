# CENTRAL IAM IMPLEMENTATION & SECURITY AUDIT REPORT
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable D — Detailed IAM Feature and Security Assessment

---

## 1. Executive Summary & Verification Scorecard

The Central Identity and Access Management (IAM) platform was audited across its models, authentication engines, authorization boundaries, cryptographic mechanisms, and administrative interfaces.

### Subsystem Implementation Scorecard
- **Authentication & Activation**: **VERIFIED** (Dual-portal flow, Argon2id, lockout thresholds).
- **MFA & Recovery Codes**: **VERIFIED** (RFC 6238 TOTP, AES-256-GCM, single-use recovery codes).
- **Account Recovery Methods (1, 2, 3)**: **VERIFIED** (Admin dual-control, self-service 30s token, replacement).
- **Secondary Device Verification**: **VERIFIED** (60s challenge expiry, email OTP, biometric gate).
- **IAM 13-Tile Navigation Grid**: **VERIFIED** (13 functional operational subsystems).
- **Employee ID Normalization (Section 6)**: **CONFLICTING** (Validates syntax and role consistency, but conflicts with `CustomUser.employee_id` immutability during role promotions).
- **12 Mandatory IAM Security Subsystems**: 2 VERIFIED, 6 PARTIALLY VERIFIED, 3 NOT IMPLEMENTED, 1 CONFLICTING.

---

## 2. Comprehensive Audit of the 12 Mandatory IAM Security Subsystems

| # | Mandatory Security Subsystem | Implementation Status | Existing Code & Evidence | Gaps & Technical Deficiencies |
|---|---|:---:|---|---|
| **1** | **Audit Tamper Evidence** | `PARTIALLY VERIFIED` | `AuditLog` (`apps/core/models.py:38`) and `AuthEvent` (`apps/accounts/models.py:535`) override ORM `.save()` and `.delete()` to throw exceptions on mutation. | Lacks cryptographic hash chaining (HMAC-SHA256 blocks) or true WORM storage; raw SQL mutations leave no tamper evidence. |
| **2** | **Alert Delivery & Escalation** | `PARTIALLY VERIFIED` | `SecurityAlert` model (`apps/accounts/models.py:465`) captures 9 security categories. Triage views exist in `security_operations.py`. | Zero automated outbound alert dispatch (no SMTP, webhook, Slack, PagerDuty); zero automated time-based SLA escalation. |
| **3** | **Dedicated Admin Identities & AAL Levels** | `PARTIALLY VERIFIED` | Dedicated `SUPER_ADMIN` and `ADMIN` roles. Sudo re-authentication enforced for privileged operations. | AAL3 hardware tokens (FIDO2/WebAuthn) are not enforced; software TOTP (AAL2) is used. Admins share standard email domains. |
| **4** | **Password-Spraying Detection** | `NOT IMPLEMENTED` | Single-account lockouts (3 attempts for sign-in; `login_flow.py:61`). IP-based rate limiting. | No horizontal, cross-account spraying detection (e.g. detecting 1 failure across 50 accounts from an IP/subnet). |
| **5** | **Rules-Based Risk Signals** | `NOT IMPLEMENTED` | None. | Zero impossible-travel calculations, IP reputation scoring, or device velocity risk engines exist. |
| **6** | **Key Custody & Rotation Drills** | `PARTIALLY VERIFIED` | AES-256-GCM encryption with key ring support (`active_key_id="k1"`) in `apps/accounts/auth/crypto.py`. | Plain environment variable key storage (`AUTH_KEY_RING`). No automated KMS integration, rotation drills, or re-encryption CLI. |
| **7** | **Device-Approval Hardening** | `VERIFIED` | `apps/accounts/views/device_approval.py`: Strict 60s window, out-of-band email OTP, HMAC cookie signatures, dual admin fallback. | Fully verified via `tests/unit/test_second_device_verification.py`. |
| **8** | **Quarterly Access Reviews & Dormant Accounts** | `NOT IMPLEMENTED` | None. | Zero models, views, or scheduled tasks exist for user recertification or dormant account deactivation. |
| **9** | **Employee Self-Service Security Page** | `VERIFIED` | Active at `/auth/security/` (`security_page.py`): Session inspection, remote revocation, enrolled device management, recovery codes. | Verified; responsive UI and integration tests pass. |
| **10** | **Email Security (SPF, DKIM, DMARC, Bounces)**| `PARTIALLY VERIFIED` | Encrypted email outbox spool (`apps/core/email_service.py`), allowed domain whitelist (`apps/accounts/auth/config.py`). | SPF, DKIM, DMARC DNS policies are absent from repository manifests. No bounce processing webhook or table exists. |
| **11** | **Clock Synchronization & Drift Alerts** | `CONFLICTING` | TOTP verification window relaxed to ±4 steps (±120s) in `apps/accounts/auth/mfa_totp.py:157` to absorb skew. | Accommodates drift rather than detecting, alerting, or enforcing NTP synchronization. |
| **12** | **Incident Response & PIR** | `PARTIALLY VERIFIED` | Runbooks in `docs/RUNBOOK.md`. Break-glass emergency mode implemented in `apps/accounts/auth/break_glass.py`. | Zero automated containment playbooks, automated session freeze hooks, or post-incident review tracking tools. |

---

## 3. IAM 13-Tile Management Grid Audit

The central IAM dashboard at `/auth/admin/iam/` (`templates/accounts/admin/iam_dashboard.html`) renders 13 operational subsystems:

1. **Authentication** (`?tab=auth_core`): Password policies, lockout thresholds, Argon2id settings. (`VERIFIED`)
2. **Authorization & RBAC** (`?tab=roles`): Role permissions, user role assignment. (`VERIFIED`)
3. **Identity & Accounts** (`?tab=identity`): Employee provisioning, invitation codes, status lifecycle. (`VERIFIED`)
4. **Identity Recovery** (`?tab=recovery`): Pending admin reset requests, dual-approval workflow. (`VERIFIED`)
5. **Sessions & Devices** (`?tab=sessions`): Active user sessions, multi-device management, session revocation. (`VERIFIED`)
6. **MFA & Authenticators** (`?tab=security_mfa`): TOTP status, recovery code resets, passkey management. (`VERIFIED`)
7. **Forgot Password** (`?tab=password_mgmt`): Self-service token settings, recovery audit trail. (`VERIFIED`)
8. **Privileged Access** (`?tab=breakglass`): Break-glass emergency access, dual-admin authorizations. (`VERIFIED`)
9. **Security Operations** (`?tab=audit`): SecOps alert triage, append-only security event logs. (`VERIFIED`)
10. **Service Identities** (`?tab=services`): Internal machine-to-machine tokens, lifecycle management. (`VERIFIED`)
11. **Secure Application Operation** (`?tab=threat`): CSP headers, rate-limiting counters, security shield. (`VERIFIED`)
12. **Threat Protection & Exceptions** (`?tab=exceptions`): Outbox queue inspection, retry handling. (`VERIFIED`)
13. **Resilience & Assurance** (`?tab=resilience`): TiDB Cloud health, database latency telemetry, diagnostic probes. (`VERIFIED`)

---

## 4. Authentication Workflows & Verification

### 4.1 First-Time Account Activation (`/auth/login/?flow=login`)
1. User enters Employee ID and 10-character Crockford Base32 activation code.
2. Syntax and role marker verified by `apps/accounts/auth/employee_id.py`.
3. 5-attempt rate-limiting enforced by `apps/accounts/auth/lockout.py`.
4. Mandatory initial password creation (Argon2id, min 12 characters).
5. MFA setup with pure Python SVG QR code display.
6. Confirmation of 6-digit TOTP code.
7. Generation of 10 single-use recovery codes (keyed HMAC-SHA256).
8. Status updated to `CustomUser.Status.ACTIVE` and session established.
- **Status**: **VERIFIED** (Tested in E2E Journey 1).

### 4.2 Returning Employee Sign-In (`/auth/login/?flow=signin`)
1. User submits Employee ID, password, and optional TOTP/recovery code on a single screen.
2. Syntax validation checks role marker consistency against database.
3. 3-attempt lockout enforced.
4. Step-up challenge initiated if secondary device is detected.
- **Status**: **VERIFIED** (Tested in E2E Journeys 2 & 3).

### 4.3 Three Distinct Account Recovery Methods
1. **Method 1 (Admin Reset)**: Employee ID + reason -> Admin review queue -> Dual-approval required for `SUPER_ADMIN` -> One-time activation code re-issued. (`VERIFIED`)
2. **Method 2 (Self-Service Reset)**: Employee ID + (Recovery Code OR TOTP) + Company Email -> 30-second crawler-safe token issued -> Password reset. (`VERIFIED`)
3. **Method 3 (Authenticator Replacement)**: Employee ID + Current Password + Company Email -> Email OTP -> New TOTP QR + 10 new recovery codes -> Atomic credential replacement. (`VERIFIED`)

---

## 5. Critical Defect: Role Promotion Lockout (Section 6 Conflict)

- **Source Location**: `apps/accounts/auth/employee_id.py:90-120` and `apps/accounts/models.py:122-127`.
- **Finding**:
  ```python
  # apps/accounts/models.py
  if self.pk is not None:
      orig = CustomUser.objects.filter(pk=self.pk).values("employee_id").first()
      if orig and orig["employee_id"] != self.employee_id:
          raise ValidationError("employee_id is immutable once assigned.")
  ```
  `CustomUser.employee_id` is strictly immutable. If an employee with identifier `JOHN-EMP-001` is promoted to Manager (`CustomUser.Role.MANAGER`), their assigned role in the database becomes `manager`.
  Upon subsequent login, `validate_employee_id_role_consistency` checks the ID marker:
  ```python
  # apps/accounts/auth/employee_id.py
  if assigned_role not in allowed_roles:
      raise AuthException("AUTH-SEC-001", "Identifier role marker does not match assigned role")
  ```
  Because `JOHN-EMP-001` marker `EMP` does not map to `MANAGER`, the login is rejected and the user is locked out permanently.
- **Status**: `CONFLICTING` (Requires an architectural decision to allow administrative ID updates or decouple role markers from static identifiers).

---
*Deliverable D complete. All IAM components, workflows, and subsystems forensically verified.*
