# Salow Workspace — Permission & Access Control Matrix

This document is automatically generated from `apps/accounts/permissions.py`.
Default-deny access control is strictly enforced across all modules.

| Permission Code | super_admin | admin | security_admin | security_reviewer | hr | manager | employee | intern |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `accounts.backup.manage` | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.device.approve` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.recovery.admin` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.recovery.delegated` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY |
| `accounts.user.create` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.user.deactivate` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.user.edit` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.user.view` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW |
| `attendance.session.approve` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ✅ ALLOW | ❌ DENY | ❌ DENY |
| `attendance.session.override` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `attendance.session.view_all` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `attendance.session.view_self` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW |
| `attendance.session.view_team` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ✅ ALLOW | ❌ DENY | ❌ DENY |
| `certificates.cert.issue` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `certificates.cert.revoke` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `core.audit.view` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `core.settings.manage` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `directory.team.manage` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `directory.team.view` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY |
| `posts.post.create` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW |
| `posts.post.moderate` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `posts.post.view_all` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `posts.post.view_team` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ✅ ALLOW | ✅ ALLOW | ❌ DENY |
| `security.alerts.acknowledge` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |
| `security.events.read` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY | ❌ DENY |

## Sudo Mode Requirements
- Password re-entry is required within 15 minutes for sensitive operations:
  - Employee status deactivation or role elevation.
  - Certificate issuing and revocation.
  - Security settings and manual attendance overrides.