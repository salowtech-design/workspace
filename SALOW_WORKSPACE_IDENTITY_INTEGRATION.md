# Salow Workspace — Identity & Access Management Integration Guide

**Document Version:** 2.0  
**Target Audience:** Core Engineering, Operations, and Future Subsystem Developers  
**Architecture:** Django Modular Monolith + TiDB Cloud  
**Status:** **Authoritative Integration Spec**

---

## 1. Monolithic Module Integration & Boundaries

The `apps.accounts` and `apps.core` packages constitute the sole authentication and authorization authority for Salow Workspace. Other modules in the modular monolith (e.g., `apps.attendance`, `apps.certificates`, `apps.directory`, `apps.posts`, and `apps.face`) interact with IAM through standardized interfaces.

### 1.1 Custom User Model
The system uses a custom user model configured in `settings.py`:
```python
AUTH_USER_MODEL = "accounts.CustomUser"
```
Subsystems referencing users must always use `settings.AUTH_USER_MODEL` or `django.contrib.auth.get_user_model()`.

### 1.2 Accessing the Authenticated Employee
All authenticated HTTP requests provide `request.user` populated with an instance of `CustomUser`.
```python
# Check authentication and active state
if not request.user.is_authenticated or not request.user.is_active:
    return redirect("accounts:login")

# Access core identity fields
employee_id = request.user.employee_id  # e.g. "ALEX-CO-001"
full_name = request.user.full_name
email = request.user.email
is_verified = request.user.email_verified
role = request.user.role  # CustomUser.Role choices
```

---

## 2. Guarding Views & Checking Permissions

### 2.1 Standard View Protection
Views requiring active employee sessions must be decorated with `@login_required`:
```python
from django.contrib.auth.decorators import login_required


@login_required
def my_protected_view(request): ...
```

### 2.2 Role & Permission Checks
Permissions are centralized in `apps/accounts/permissions.py`. Modules should never check raw strings or extract roles from the `employee_id`.
```python
from apps.accounts.permissions import has_permission, PERM_RECOVERY_ADMIN

if not has_permission(request.user, PERM_RECOVERY_ADMIN):
    raise PermissionDenied("Insufficient administrative privileges.")
```

### 2.3 Sudo Mode (Step-Up Authentication)
Sensitive operations (e.g. deleting biometrics, modifying salary records, revoking credentials) should enforce active sudo authorization:
```python
from apps.accounts.auth.stepup import is_sudo_active
from django.shortcuts import redirect
from django.urls import reverse

if not is_sudo_active(request):
    next_url = request.get_full_path()
    return redirect(f"{reverse('accounts:sudo')}?next={next_url}")
```

---

## 3. Transactional Email & Outbox Worker

All IAM security notifications and recovery links are processed via the transactional `EmailOutbox` service.

### 3.1 Queueing an Email Notification
To queue an encrypted email from any service:
```python
from apps.core.email_service import queue_email

queue_email(
    recipient=user.email,
    subject="Security Notification: Credentials Changed",
    body_text="Plain text body...",
    body_html="<p>HTML body...</p>",
    notification_type="security_alert",
)
```

### 3.2 Processing the Email Outbox
In production and development, the outbox delivery worker is run via the management command:
```powershell
python manage.py process_email_outbox --limit 50 --max-retries 5
```
For continuous delivery, schedule this command via a cron job, systemd service, or background task runner.

---

## 4. Operational Runbook & Diagnostics

Salow Workspace provides built-in CLI management commands for system administration and health monitoring:

### 4.1 System Health Diagnostics (`auth_doctor`)
Runs 12 comprehensive diagnostic checks verifying database connectivity, argon2 hashing, TOTP engines, AES-256-GCM keyrings, rate-limit tables, and cookie policies:
```powershell
python manage.py auth_doctor
```

### 4.2 Initial Super Admin Bootstrapping
Creates or resets the root Super Administrator account with compliant credentials and generated recovery codes:
```powershell
python manage.py bootstrap_admin --employee-id ST-ADM-0001 --email admin@salowtech.com --password "<StrongPassword123!>"
```

### 4.3 Rate Limiter Cache Pruning
Clears expired rate-limiting sliding windows and resets locks:
```powershell
python manage.py reset_ratelimit
```

---

## 5. Architectural Invariants Summary

1. **No External Identity Providers:** All authentication and authorization is owned and operated by Salow Tech. No Keycloak, Firebase, or Auth0 integrations.
2. **TiDB Cloud Storage:** The primary database is TiDB Cloud Serverless (`salow_workspce`). All transactions must account for distributed SQL constraints.
3. **No Passkey in User Interface:** WebAuthn models and backend libraries are preserved in the repository for future milestone readiness, but are strictly removed from current login and recovery templates.
4. **Argon2id and AES-256-GCM:** Passwords use Argon2id. Encrypted secrets and email outbox bodies use AES-256-GCM authenticated encryption.
5. **Session-Bound Authorization:** Authorization state (such as Sudo Mode) is bound strictly to the server-side session.
