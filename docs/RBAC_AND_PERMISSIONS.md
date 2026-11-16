# Salow Workspace — Role-Based Access Control (RBAC) & IAM Policies

**Authoritative Specification**: Salow Workspace IAM Architecture  
**Document Status**: Official Engineering Architecture Guide  
**Applies To**: `apps.accounts.permissions`, `apps.core.middleware`  

---

## 1. Principles of Authorization

Salow Workspace operates under a **Default-Deny** authorization posture:
1. Every URL, HTMX endpoint, POST action, and file download requires explicit permission evaluation.
2. Hiding navigation items in UI templates does **not** substitute for server-side policy enforcement.
3. Authorization policies are centralized in `apps.accounts.permissions`, eliminating fragmented and inconsistent role checks in individual views.

---

## 2. Enterprise Roles Hierarchy

Salow Workspace defines six standardized roles:

| Role | Code | Responsibility Scope |
|---|---|---|
| **Super Admin** | `SUPER_ADMIN` | Organization owner, full system governance, diagnostic commands, dual-admin approvals |
| **Admin** | `ADMIN` | Account administration, operational configuration, system logs, hardware audit |
| **HR** | `HR` | Employee lifecycle management, onboarding activation, subordinate attendance, org directory |
| **Manager** | `MANAGER` | Team leadership, daily work log review, subordinate leave approvals, team directory |
| **Employee** | `EMPLOYEE` | Standard worker: self-attendance, personal daily logs, peer directory, personal certificates |
| **Intern** | `INTERN` | Scoped worker: self-attendance, personal daily logs, restricted directory visibility |

---

## 3. Centralized Permission Matrix

The following authoritative matrix dictates allow (`ALLOW`) and deny (`DENY`) access across core functional domains:

| Endpoint / Subsystem | Anonymous | Intern | Employee | Manager | HR | Admin | Super Admin |
|---|---|---|---|---|---|---|---|
| `/accounts/login/` | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/accounts/activate/` | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/dashboard/` | DENY | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/attendance/` (Self Check-in) | DENY | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/attendance/review/` (Team) | DENY | DENY | DENY | ALLOW (Team) | ALLOW (Org) | ALLOW | ALLOW |
| `/posts/create/` (Daily Log) | DENY | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/certificates/my/` | DENY | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/certificates/issue/` | DENY | DENY | DENY | DENY | ALLOW | ALLOW | ALLOW |
| `/directory/` (Peer Profile) | DENY | ALLOW (Self) | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/accounts/recovery/admin/` | DENY | DENY | DENY | DENY | ALLOW (Staff) | ALLOW | ALLOW |
| `/accounts/recovery/super/` | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW (Dual) |
| `/admin/` (Django Admin) | DENY | DENY | DENY | DENY | DENY | ALLOW | ALLOW |
| `python manage.py auth_doctor` | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW (CLI) |

---

## 4. Object-Level & Reporting Scope Policies

Beyond static role checks, Salow Workspace enforces **horizontal privilege boundary checks**:
- **Personal Data Protection**: Employees can view only their own attendance records, draft logs, and private profile fields. Querying `user_id=X` where $X \ne \text{request.user.id}$ raises `PermissionDenied` (HTTP 403).
- **Manager Scope Enforcement**: Managers can only review, approve, or comment on subordinates whose active record exists in `ReportingRelationship.objects.filter(manager=request.user, subordinate=subordinate, is_active=True)`.
- **HR Scope Enforcement**: HR personnel cannot modify Super Admin records or access executive diagnostic tools.
