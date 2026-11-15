# Salow Workspace — Permission & Access Control Matrix

This document is automatically generated from `apps/accounts/permissions.py`.
Default-deny access control is strictly enforced across all modules.

| Permission Code | super_admin | admin | manager | employee | intern |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `accounts.user.create` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.user.deactivate` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.user.edit` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `accounts.user.view` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW |
| `attendance.session.approve` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY |
| `attendance.session.override` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `attendance.session.view_all` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `attendance.session.view_self` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW |
| `attendance.session.view_team` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY |
| `certificates.cert.issue` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `certificates.cert.revoke` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `core.audit.view` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `core.settings.manage` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `directory.team.manage` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `directory.team.view` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY |
| `posts.post.create` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW |
| `posts.post.moderate` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `posts.post.view_all` | ✅ ALLOW | ✅ ALLOW | ❌ DENY | ❌ DENY | ❌ DENY |
| `posts.post.view_team` | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ✅ ALLOW | ❌ DENY |

## Sudo Mode Requirements
- Password re-entry is required within 15 minutes for sensitive operations:
  - Employee status deactivation or role elevation.
  - Certificate issuing and revocation.
  - Security settings and manual attendance overrides.