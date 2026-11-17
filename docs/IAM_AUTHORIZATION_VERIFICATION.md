# Salow Workspace — IAM Authorization & RBAC Verification Report

**Directive**: Salow Workspace IAM v1.0 Forensic Verification & Security Correction  
**Stage**: Stage 5 — RBAC and Authorization Verification  
**Timestamp**: 2026-11-17T09:14:00+05:30  
**Auditor**: Senior IAM & Security Verification Engineer  
**Scope**: Server-Side Default-Deny Policy Enforcement, Object-Level Ownership, and IDOR Defense  

---

## 1. Executive Summary

This report documents the exhaustive verification of server-side authorization controls across all six organizational roles in Salow Workspace (`ANONYMOUS`, `INTERN`, `EMPLOYEE`, `MANAGER`, `HR`, `ADMIN`, `SUPER_ADMIN`).

Every permission check is enforced server-side within view entry points or service boundaries. No access control relies on client-side state, hidden UI templates, or navigational suppression.

---

## 2. Server-Side Default-Deny Posture

The application implements a strict **Default-Deny** architectural rule:
1. When an authentication decorator (`@login_required`) or permission decorator (`@require_permission`, `@require_sudo`) is evaluated:
   - If user identity is missing or ambiguous $\rightarrow$ **DENY** (HTTP 302 redirect to `/auth/login/` or HTTP 401).
   - If user account status is not `ACTIVE` (e.g. `SUSPENDED`, `EXITED`, `DISABLED`, `LOCKED`) $\rightarrow$ **DENY** and terminate active session immediately.
   - If MFA verification flag is absent from session dictionary $\rightarrow$ **DENY** protected access and route to `/auth/login/totp/`.
   - If role lacks explicit permission code $\rightarrow$ **DENY** (HTTP 403 Forbidden via `PermissionDenied`).

---

## 3. Authoritative Role-to-Permission Matrix

The table below documents verified authorization behavior across primary application routes:

| Route / Subsystem | Anonymous | Intern | Employee | Manager | HR | Admin | Super Admin |
|---|---|---|---|---|---|---|---|
| `/auth/login/` | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/auth/activate/` | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/auth/security/` (Dashboard) | DENY (302) | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/auth/security/draft/save/` | DENY (401) | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/attendance/` (Self Check-in) | DENY (302) | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/attendance/panel/` (Team View)| DENY (302) | DENY (403)| DENY (403)| ALLOW (Team)| ALLOW (Org)| ALLOW | ALLOW |
| `/posts/` (Daily Communication) | DENY (302) | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/certificates/issue/` | DENY (302) | DENY (403)| DENY (403)| DENY (403)| ALLOW | ALLOW | ALLOW |
| `/directory/` (Peer Directory) | DENY (302) | ALLOW (Self)| ALLOW | ALLOW | ALLOW | ALLOW | ALLOW |
| `/auth/recovery/console/` | DENY (302) | DENY (302)| DENY (302)| ALLOW (Scope)| ALLOW | ALLOW | ALLOW |
| `/auth/recovery/admin/` | DENY (302) | DENY (403)| DENY (403)| DENY (403)| DENY (403)| ALLOW (Dual)| ALLOW (Dual)|
| `python manage.py auth_doctor` | DENY | DENY | DENY | DENY | DENY | DENY | ALLOW (CLI)|

---

## 4. Object-Level Access Control & Insecure Direct Object Reference (IDOR) Defense

Horizontal privilege escalation (IDOR) attacks are eliminated by enforcing ownership and relationship validation in database querysets:

### 4.1 Attendance & Private Record Isolation
- An employee requesting another employee's attendance record (e.g. `/attendance/records/?user_id=999`) cannot view data belonging to other employees.
- Querysets filter strictly by `user=request.user` unless the user possesses explicit elevated supervisory permissions (`can_review_subordinate_attendance`).

### 4.2 Workspace Draft Isolation
- The draft persistence endpoint `/auth/security/draft/save/` and `/auth/security/draft/get/` binds drafts strictly to `user=request.user`.
- Modifying the path or form payload cannot expose or overwrite another employee's draft. Cross-user draft leakage test (`test_draft_cross_user_isolation`) passed 100%.

### 4.3 Manager Reporting-Line Boundary
- A Manager cannot approve leave, review attendance, or initiate account recovery for an employee outside their direct reporting relationship (`ReportingRelationship`) or led team (`Team`).
- Attempting to pass an unrelated employee ID raises `PermissionDenied` (HTTP 403). Tested in `test_manager_unauthorized_recovery_denied`.

---

## 5. Account Lifecycle Invariants

| Lifecycle State | Session Behavior on Protected Request | Ejection Mechanism |
|---|---|---|
| `SUSPENDED` | Terminated immediately; user redirected to `/auth/login/?error=suspended` | `AccountSecurityMiddleware` real-time ejection |
| `EXITED` | Terminated immediately; database session deleted | `AccountSecurityMiddleware` real-time ejection |
| `LOCKED` | Terminated; sign-in rejected until lockout timer expires | `AccountSecurityMiddleware` & login view |
| `DISABLED` | Terminated immediately | `AccountSecurityMiddleware` real-time ejection |
| Role Change | Demoting/altering user role purges all active sessions across devices | `apps.accounts.services.set_user_role` triggers `revoke_user_sessions` |
