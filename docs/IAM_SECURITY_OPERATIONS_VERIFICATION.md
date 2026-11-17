# Salow Workspace — IAM Security Operations & Incident Response Runbook

**Directive**: Salow Workspace IAM v1.0 Forensic Verification & Security Correction  
**Stage**: Stage 11 — Security Operations and Incident Response  
**Timestamp**: 2026-11-17T09:41:00+05:30  
**Auditor**: Senior IAM & Security Operations Engineer  
**Scope**: Standard Operating Procedures (SOPs) for Account Compromise, Key Rotation, and Threat Containment  

---

## 1. Incident Management Protocol (8-Step Standard Response)

Every security incident in Salow Workspace follows a standardized 8-step containment lifecycle:
1. **Authorized Actor**: Verification that the responder has appropriate organizational standing (`SUPER_ADMIN` or `ADMIN`).
2. **Identity Confirmation**: Multi-factor re-authentication or out-of-band identity check.
3. **Immediate Containment**: Account status freeze or network-level blocking.
4. **Session & Credential Revocation**: Immediate purging of active sessions and invalidation of compromised credentials.
5. **Audit Evidence Preservation**: Capturing query logs, `AuthEvent` timelines, and `AuditEvent` records.
6. **Recovery Procedure**: Issuing single-use activation or recovery tokens.
7. **Post-Recovery Validation**: Verifying that the target account enrolls fresh MFA and sets a new password.
8. **Incident Closure**: Documenting root cause, timeline, and post-mortem actions.

---

## 2. Threat Scenarios & Operational Runbooks

### 2.1 Scenario 1: Compromised Employee Account
- **Authorized Actor**: Direct Manager, HR, Admin, or Super Admin.
- **Immediate Containment**:
  ```python
  from apps.accounts.models import CustomUser
  from apps.accounts.services import set_user_status

  user = CustomUser.objects.get(employee_id="ST-EMP-EXAMPLE")
  set_user_status(user, CustomUser.Status.SUSPENDED)
  ```
- **Session Revocation**: `set_user_status` automatically purges all active records from `apps.accounts.models.UserSession` and terminates database sessions in `django_session`.
- **Validation**:
  ```python
  from apps.accounts.models import UserSession

  assert not UserSession.objects.filter(user=user).exists()
  ```

---

### 2.2 Scenario 2: Lost Authenticator Device (TOTP)
- **Authorized Actor**: Admin, HR, or Direct Manager.
- **Immediate Containment**: Invalidate existing TOTP credential.
  ```python
  from apps.accounts.models import CustomUser, TOTPCredential

  user = CustomUser.objects.get(employee_id="ST-EMP-EXAMPLE")
  TOTPCredential.objects.filter(user=user).delete()
  ```
- **Recovery Action**: Issue single-use `MFA_RESET` token.
  ```python
  from apps.accounts.auth.reset import issue_mfa_reset_code

  admin_user = CustomUser.objects.get(employee_id="ST-ADM-EXAMPLE")
  record, raw_code = issue_mfa_reset_code(target_user=user, issuing_admin=admin_user)
  ```
- **Employee Handoff**: Employee redeems the token with their current password and is routed to `/auth/activate/mfa/setup/` to scan a new QR code.

---

### 2.3 Scenario 3: Lost Hardware Security Key / Passkey
- **Authorized Actor**: Self (via dashboard) or Administrator.
- **Immediate Containment**: Deactivate individual passkey credential.
  ```python
  from apps.accounts.auth.passkeys import revoke_passkey
  from apps.accounts.models import CustomUser

  user = CustomUser.objects.get(employee_id="ST-EMP-EXAMPLE")
  revoke_passkey(user, credential_id=123)
  ```
- **Validation**: Confirm `PasskeyCredential.objects.get(id=123).is_active is False`.

---

### 2.4 Scenario 4: Suspected Administrator Compromise (Dual-Admin Enforcement)
- **Authorized Actor**: Dual Super Admins.
- **Rule**: A single administrator **cannot** reset another administrator unilaterally.
- **Protocol**:
  1. Super Admin 1 initiates recovery request specifying justification.
  2. Request enters `PENDING_APPROVAL` status.
  3. Super Admin 2 performs out-of-band video/in-person verification, logs in with MFA, and approves the reset.
  4. Global session revocation is executed immediately for the target administrator.

---

### 2.5 Scenario 5: Cryptographic Key Rotation (`KeyRing`)
- **Authorized Actor**: Lead Security Engineer / Super Admin.
- **Procedure**:
  1. Generate new 256-bit AES key: `openssl rand -hex 32`.
  2. Add key to production secrets vault as `SALOW_KEYRING_K2`.
  3. Update `DEFAULT_KEY_ID = "k2"` in `apps/accounts/auth/crypto.py`.
  4. Perform test encryption and decryption verification via `python manage.py auth_doctor`.
  5. Historical records prefixed with `k1::` continue to decrypt seamlessly using the retained `k1` key in the KeyRing.

---

### 2.6 Scenario 6: Rate-Limiting & Credential Stuffing Attack
- **Detection**: Spike in HTTP 429 responses in reverse proxy logs or `RateLimitRecord` entries.
- **Containment**: Rate limiter automatically throttles offending IPs and target employee IDs (`acc:{employee_id}:sensitive`) to 5 requests/minute.
- **Administrative Inspection**:
  ```python
  from apps.core.models import RateLimitRecord
  from django.utils import timezone

  recent = RateLimitRecord.objects.filter(timestamp__gt=timezone.now().timestamp() - 300)
  print(f"Active throttled keys: {recent.count()}")
  ```
