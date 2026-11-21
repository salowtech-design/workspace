"""
Admin IAM Management Console & Administrator Security Shield Views.
Provides:
1. Visible IAM & Access Management area with 7 distinct operational tabs:
   - Identity & Accounts (user creation issuing one-time activation codes, directory)
   - Roles & Permissions (matrix of 6 roles, role assignments)
   - Security & MFA (MFA adoption, recovery code count, mfa reset triggers)
   - Sessions & Devices (active sessions table, revocation controls)
   - Recovery Console (pending recovery requests with dual-control for super admin)
   - Account Lifecycle (suspend, reactivate, lifecycle state transitions)
   - IAM Audit (real-time AuthEvent inspection with search and filters)
2. Administrator Security Shield:
   - Dedicated verification panel with TOTP, 1:1 Face verification, RBAC checks
   - Latency measurements (TOTP ms, face ms, RBAC ms)
   - Fails safely on timeout or mismatch, grants sudo on full success
"""

import json
import time
from datetime import timedelta
from typing import Any, Optional

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.auth.activation import issue_activation_code
from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.config import is_allowed_company_email
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.auth.reset import (
    approve_administrator_reset,
    check_recovery_authority,
    issue_mfa_reset_code,
    reject_administrator_reset,
)
from apps.accounts.auth.service_identities import (
    VALID_SCOPES,
    issue_service_identity,
    revoke_service_identity,
)
from apps.accounts.auth.iam_auth import (
    check_iam_assurance,
    compute_payload_digest,
    get_iam_session_from_request,
)
from apps.accounts.auth.sessions import (
    end_session_by_key,
    revoke_user_sessions,
    synchronize_employee_lifecycle_revocation,
)
from apps.accounts.auth.stepup import (
    activate_break_glass,
    grant_sudo,
    is_break_glass_active,
    is_sudo_active,
    revoke_break_glass,
)
from apps.accounts.models import (
    ActivationCode,
    AuthEvent,
    CustomUser,
    DeviceApprovalRequest,
    IAMAdminIdentity,
    IAMSession,
    PasskeyCredential,
    RecoveryCode,
    RecoveryRequest,
    SecurityAlert,
    ServiceIdentity,
    TOTPCredential,
    UserSession,
)
from apps.attendance.models import AttendanceFallbackRequest
from apps.core.models import AuditLog, EmailOutbox, OutboxEvent, RateLimitRecord
from apps.directory.models import Department, Team, TeamMembership
from apps.face.models import FaceTemplate, FaceVerificationLog
from apps.face.services import verify_face

ROLE_PERMISSIONS_MATRIX = [
    {
        "role": "Super Admin",
        "key": "super_admin",
        "description": "Full enterprise authority, dual-control recovery, system configuration.",
        "can_create_users": "All Roles",
        "can_manage_roles": "All Roles",
        "recovery_authority": "All Accounts (Dual Approval for Super Admin)",
        "can_manage_sessions": True,
        "shield_access": True,
    },
    {
        "role": "Admin",
        "key": "admin",
        "description": "Administrative authority over staff, delegated recovery, session management.",
        "can_create_users": "HR, Manager, Employee, Intern",
        "can_manage_roles": "HR, Manager, Employee, Intern",
        "recovery_authority": "Staff & Interns (Dual Approval for Admin)",
        "can_manage_sessions": True,
        "shield_access": True,
    },
    {
        "role": "Human Resources",
        "key": "hr",
        "description": "Personnel onboarding, employee directory, staff credential activation.",
        "can_create_users": "Employee, Intern",
        "can_manage_roles": "None",
        "recovery_authority": "Employee, Intern",
        "can_manage_sessions": False,
        "shield_access": False,
    },
    {
        "role": "Manager",
        "key": "manager",
        "description": "Team leadership, attendance supervision, delegated team recovery.",
        "can_create_users": "None",
        "can_manage_roles": "None",
        "recovery_authority": "Team Members Only",
        "can_manage_sessions": False,
        "shield_access": False,
    },
    {
        "role": "Employee",
        "key": "employee",
        "description": "Standard workspace access, attendance logging, work logs and self-service recovery.",
        "can_create_users": "None",
        "can_manage_roles": "None",
        "recovery_authority": "Self-Service Only",
        "can_manage_sessions": False,
        "shield_access": False,
    },
    {
        "role": "Intern",
        "key": "intern",
        "description": "Limited workspace access, attendance check-in, supervised work logs.",
        "can_create_users": "None",
        "can_manage_roles": "None",
        "recovery_authority": "Self-Service Only",
        "can_manage_sessions": False,
        "shield_access": False,
    },
]


def enforce_mutation_assurance(request, action_name: str, required_level: int, payload: Any = None) -> Optional[Any]:
    """
    Enforces required IAM assurance level (L1, L2, L3) with canonical payload binding.
    If session lacks required assurance, redirects to step-up or displays error.
    """
    session = getattr(request, "iam_session", None) or get_iam_session_from_request(request)
    if not session:
        return None

    digest = compute_payload_digest(payload) if payload else None
    if not check_iam_assurance(request, required_level=required_level, action_name=action_name, payload_digest=digest):
        if required_level == 2:
            stepup_url = "/iam/stepup/"
            return redirect(f"{stepup_url}?action={action_name}&next={request.get_full_path()}")
        elif required_level == 3:
            messages.error(request, f"Operation '{action_name}' requires independent Level 3 Dual Approval from a second Super Administrator.")
            return redirect(request.get_full_path())
    return None


def admin_iam_dashboard_view(request):  # noqa: C901
    """
    Central IAM & Access Management Console.
    Restricted to authorized administrative roles: super_admin, admin, security_admin, hr, manager.
    Authoritative principal: IAMAdminIdentity when accessed via IAM host/session.
    """
    # Resolve dedicated IAM session & identity
    iam_session = getattr(request, "iam_session", None) or get_iam_session_from_request(request)
    if iam_session:
        request.iam_session = iam_session
        request.iam_admin = iam_session.iam_admin
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            request.user = iam_session.iam_admin.user

    iam_admin: Optional[IAMAdminIdentity] = getattr(request, "iam_admin", None)
    actor: Optional[CustomUser] = getattr(request, "user", None)

    # If neither IAM administrator nor authenticated user is present, redirect to login
    if not iam_admin and (not actor or not actor.is_authenticated):
        login_url = "/iam/login/" if getattr(request, "is_iam_host", False) else "/auth/login/"
        return redirect(f"{login_url}?next={request.path}")

    # Enforce authorization
    if iam_admin:
        if iam_admin.status != IAMAdminIdentity.Status.ACTIVE:
            raise PermissionDenied("IAM administrator account is inactive or suspended.")
        if iam_admin.role == IAMAdminIdentity.Role.SECURITY_REVIEWER and request.method == "POST":
            raise PermissionDenied("Security Reviewers hold read-only audit inspection privileges.")
        actor = iam_admin.user
    else:
        allowed_roles = (
            CustomUser.Role.SUPER_ADMIN,
            CustomUser.Role.ADMIN,
            CustomUser.Role.HR,
            CustomUser.Role.MANAGER,
        )
        if actor.role not in allowed_roles:
            raise PermissionDenied("Access restricted to administrative and management roles.")

    active_tab = request.GET.get("tab") or request.POST.get("tab") or "identity"
    issued_activation = request.session.pop("_salow_issued_activation", None)

    if request.method == "POST":
        action = request.POST.get("action", "")

        # Scoped role enforcement for Workspace Security Admin
        if iam_admin and iam_admin.role == IAMAdminIdentity.Role.SECURITY_ADMIN:
            if action in ("create_employee", "update_role"):
                target_role = request.POST.get("role") or request.POST.get("new_role")
                if target_role == CustomUser.Role.SUPER_ADMIN:
                    messages.error(request, "Workspace Security Admins cannot provision or promote Super Admin accounts.")
                    return redirect(f"{request.path}?tab={active_tab}")
            elif action in ("activate_break_glass", "create_service_identity"):
                messages.error(request, "Workspace Security Admins cannot execute root IAM elevation operations.")
                return redirect(f"{request.path}?tab={active_tab}")


        # -------------------------------------------------------------------
        # 1. Identity & Accounts: Create Employee with Activation Code
        # -------------------------------------------------------------------
        if action == "create_employee":
            active_tab = "identity"
            emp_id = request.POST.get("employee_id", "").strip().upper()
            full_name = request.POST.get("full_name", "").strip()
            email = request.POST.get("email", "").strip().lower()
            role = request.POST.get("role", CustomUser.Role.EMPLOYEE)

            # Role creation permissions
            if actor.role == CustomUser.Role.MANAGER:
                messages.error(
                    request, "Managers do not have permission to provision new accounts."
                )
            elif actor.role == CustomUser.Role.HR and role not in (
                CustomUser.Role.EMPLOYEE,
                CustomUser.Role.INTERN,
            ):
                messages.error(request, "HR personnel can only create Employee or Intern accounts.")
            elif actor.role == CustomUser.Role.ADMIN and role == CustomUser.Role.SUPER_ADMIN:
                messages.error(request, "Administrators cannot provision Super Admin accounts.")
            elif not emp_id or not full_name or not email:
                messages.error(request, "Employee ID, full name, and email are required.")
            elif not is_allowed_company_email(email):
                messages.error(request, "Email must belong to the @salowtech.com company domain.")
            elif CustomUser.objects.filter(employee_id=emp_id).exists():
                messages.error(request, f"Employee ID {emp_id} is already in use.")
            elif CustomUser.objects.filter(email=email).exists():
                messages.error(request, f"Email {email} is already registered.")
            else:
                try:
                    with transaction.atomic():
                        new_user = CustomUser(
                            employee_id=emp_id,
                            full_name=full_name,
                            email=email,
                            role=role,
                            status=CustomUser.Status.ACTIVE,
                            email_verified=True,
                        )
                        new_user.set_unusable_password()  # Administrator never sees or sets password
                        new_user.save()

                        # Issue one-time activation credential
                        _, raw_code = issue_activation_code(
                            user=new_user,
                            purpose=ActivationCode.Purpose.ACTIVATION,
                            issued_by=actor,
                        )

                        log_auth_event(
                            event="employee_created_by_admin",
                            code="INFO",
                            step="admin_create_employee",
                            employee_id=new_user.employee_id,
                            message=f"Created by {actor.employee_id} ({actor.role}); activation code issued",
                        )

                        request.session["_salow_issued_activation"] = {
                            "employee_id": new_user.employee_id,
                            "full_name": new_user.full_name,
                            "role": new_user.get_role_display(),
                            "code": raw_code,
                        }
                        messages.success(
                            request,
                            f"Account created for {new_user.full_name} ({new_user.employee_id}). "
                            f"One-time activation code generated.",
                        )
                        return redirect(f"{request.path}?tab=identity")
                except Exception as exc:
                    messages.error(request, f"Failed to create employee: {exc}")

        # -------------------------------------------------------------------
        # 2. Roles & Permissions: Update Employee Role
        # -------------------------------------------------------------------
        elif action == "update_role":
            active_tab = "roles"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            new_role = request.POST.get("new_role", "")

            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
            elif actor.role != CustomUser.Role.SUPER_ADMIN and (
                new_role == CustomUser.Role.SUPER_ADMIN
                or target_user.role == CustomUser.Role.SUPER_ADMIN
            ):
                messages.error(request, "Only Super Admins can assign or modify Super Admin roles.")
            elif actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                messages.error(request, "You do not have permission to modify roles.")
            else:
                payload = {"target_employee_id": target_emp_id, "new_role": new_role}
                req_level = 3 if (new_role == CustomUser.Role.SUPER_ADMIN or target_user.role == CustomUser.Role.SUPER_ADMIN) else 2
                redirect_resp = enforce_mutation_assurance(request, "update_role", required_level=req_level, payload=payload)
                if redirect_resp:
                    return redirect_resp

                old_role = target_user.role
                target_user.role = new_role
                target_user.save(update_fields=["role", "updated_at"])
                revoke_user_sessions(target_user)
                log_auth_event(
                    event="role_updated_by_admin",
                    code="INFO",
                    step="admin_update_role",
                    employee_id=target_user.employee_id,
                    message=f"Role changed from {old_role} to {new_role} by {actor.employee_id}; sessions revoked",
                )
                messages.success(
                    request, f"Updated role for {target_user.employee_id} to {new_role}."
                )
                return redirect(f"{request.path}?tab=roles")

        # -------------------------------------------------------------------
        # 3. Security & MFA: Issue MFA Reset Code
        # -------------------------------------------------------------------
        elif action == "issue_mfa_reset":
            active_tab = "security_mfa"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
            else:
                try:
                    check_recovery_authority(actor, target_user)
                    payload = {"target_employee_id": target_emp_id, "action": "issue_mfa_reset"}
                    redirect_resp = enforce_mutation_assurance(request, "issue_mfa_reset", required_level=2, payload=payload)
                    if redirect_resp:
                        return redirect_resp

                    _, raw_code = issue_mfa_reset_code(
                        target_user=target_user,
                        issuing_admin=actor,
                    )
                    request.session["_salow_issued_activation"] = {
                        "employee_id": target_user.employee_id,
                        "full_name": target_user.full_name,
                        "role": "MFA Reset Code",
                        "code": raw_code,
                    }
                    messages.success(
                        request,
                        f"Single-use MFA reset code generated for {target_user.employee_id}.",
                    )
                    return redirect(f"{request.path}?tab=security_mfa")
                except (PermissionDenied, ValidationError) as exc:
                    messages.error(request, str(exc))

        # -------------------------------------------------------------------
        # 4. Sessions & Devices: Revoke Session
        # -------------------------------------------------------------------
        elif action == "revoke_session":
            active_tab = "sessions"
            session_key = request.POST.get("session_key", "").strip()
            sess = (
                UserSession.objects.filter(session_key=session_key).select_related("user").first()
            )
            if sess:
                end_session_by_key(sess.user, session_key)
                messages.success(request, f"Session {session_key[:8]}... terminated.")
            else:
                messages.info(request, "Session not found or already expired.")
            return redirect(f"{request.path}?tab=sessions")

        elif action == "revoke_user_all_sessions":
            active_tab = "sessions"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if target_user:
                payload = {"target_employee_id": target_emp_id, "action": "revoke_user_all_sessions"}
                redirect_resp = enforce_mutation_assurance(request, "revoke_user_all_sessions", required_level=2, payload=payload)
                if redirect_resp:
                    return redirect_resp

                count = revoke_user_sessions(target_user)
                messages.success(
                    request, f"Revoked all {count} active sessions for {target_user.employee_id}."
                )
            return redirect(f"{request.path}?tab=sessions")

        # -------------------------------------------------------------------
        # 5. Recovery Console: Handled via approve/reject endpoints or tab
        # -------------------------------------------------------------------
        elif action == "approve_recovery":
            active_tab = "recovery"
            request_id = request.POST.get("request_id")
            second_approver_id = request.POST.get("second_approver_id")
            second_approver = None
            if second_approver_id:
                second_approver = CustomUser.objects.filter(
                    id=second_approver_id, is_active=True
                ).first()
            base_url = request.build_absolute_uri("/")[:-1]
            try:
                req, _ = approve_administrator_reset(
                    request_id=int(request_id),
                    actor=actor,
                    second_approver=second_approver,
                    base_url=base_url,
                )
                messages.success(
                    request, f"Recovery request for {req.user.employee_id} approved. Link sent."
                )
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect(f"{request.path}?tab=recovery")

        elif action == "reject_recovery":
            active_tab = "recovery"
            request_id = request.POST.get("request_id")
            reason = request.POST.get("reason", "Verification requirements not met.")
            try:
                req = reject_administrator_reset(
                    request_id=int(request_id),
                    actor=actor,
                    reason=reason,
                )
                messages.info(request, f"Recovery request for {req.user.employee_id} rejected.")
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect(f"{request.path}?tab=recovery")

        # -------------------------------------------------------------------
        # 6. Account Lifecycle: Suspend / Reactivate
        # -------------------------------------------------------------------
        elif action == "suspend_account":
            active_tab = "lifecycle"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
            elif (
                target_user.role == CustomUser.Role.SUPER_ADMIN
                and actor.role != CustomUser.Role.SUPER_ADMIN
            ):
                messages.error(request, "Only Super Admins can suspend a Super Admin account.")
            elif target_user.id == actor.id:
                messages.error(request, "You cannot suspend your own account.")
            else:
                payload = {"target_employee_id": target_emp_id, "action": "suspend_account"}
                req_level = 3 if target_user.role == CustomUser.Role.SUPER_ADMIN else 2
                redirect_resp = enforce_mutation_assurance(request, "suspend_account", required_level=req_level, payload=payload)
                if redirect_resp:
                    return redirect_resp

                target_user.is_active = False
                target_user.status = CustomUser.Status.SUSPENDED
                target_user.save(update_fields=["is_active", "status", "updated_at"])
                synchronize_employee_lifecycle_revocation(target_user)
                log_auth_event(
                    event="account_suspended_by_admin",
                    code="WARN",
                    step="admin_lifecycle",
                    employee_id=target_user.employee_id,
                    message=f"Suspended by {actor.employee_id}; all workspace and IAM sessions revoked",
                )
                messages.success(
                    request,
                    f"Account {target_user.employee_id} suspended and all sessions revoked.",
                )
                return redirect(f"{request.path}?tab=lifecycle")

        elif action == "reactivate_account":
            active_tab = "lifecycle"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
            else:
                payload = {"target_employee_id": target_emp_id, "action": "reactivate_account"}
                redirect_resp = enforce_mutation_assurance(request, "reactivate_account", required_level=2, payload=payload)
                if redirect_resp:
                    return redirect_resp

                target_user.is_active = True
                target_user.status = CustomUser.Status.ACTIVE
                target_user.save(update_fields=["is_active", "status", "updated_at"])
                log_auth_event(
                    event="account_reactivated_by_admin",
                    code="INFO",
                    step="admin_lifecycle",
                    employee_id=target_user.employee_id,
                    message=f"Reactivated by {actor.employee_id}",
                )
                messages.success(request, f"Account {target_user.employee_id} reactivated.")
                return redirect(f"{request.path}?tab=lifecycle")

        # -------------------------------------------------------------------
        # 6b. Lifecycle: Mark Exited
        # -------------------------------------------------------------------
        elif action == "exit_account":
            active_tab = "lifecycle"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
            elif (
                target_user.role == CustomUser.Role.SUPER_ADMIN
                and actor.role != CustomUser.Role.SUPER_ADMIN
            ):
                messages.error(request, "Only Super Admins can transition a Super Admin account.")
            elif target_user.id == actor.id:
                messages.error(request, "You cannot mark your own account as exited.")
            else:
                payload = {"target_employee_id": target_emp_id, "action": "exit_account"}
                req_level = 3 if target_user.role == CustomUser.Role.SUPER_ADMIN else 2
                redirect_resp = enforce_mutation_assurance(request, "exit_account", required_level=req_level, payload=payload)
                if redirect_resp:
                    return redirect_resp

                target_user.is_active = False
                target_user.status = CustomUser.Status.EXITED
                target_user.end_date = timezone.now().date()
                target_user.save(update_fields=["is_active", "status", "end_date", "updated_at"])
                synchronize_employee_lifecycle_revocation(target_user)
                log_auth_event(
                    event="account_exited_by_admin",
                    code="WARN",
                    step="admin_lifecycle",
                    employee_id=target_user.employee_id,
                    message=f"Marked as exited by {actor.employee_id}; all active sessions terminated",
                )
                messages.success(
                    request,
                    f"Account {target_user.employee_id} marked as EXITED. All active sessions terminated.",
                )
                return redirect(f"{request.path}?tab=lifecycle")

        # -------------------------------------------------------------------
        # 8. People, Teams & Groups: Add Team Membership
        # -------------------------------------------------------------------
        elif action == "add_team_membership":
            active_tab = "people_teams"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            team_code = request.POST.get("team_code", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            team = Team.objects.filter(code=team_code).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
            elif not team:
                messages.error(request, f"Team with code '{team_code}' not found.")
            else:
                _, created = TeamMembership.objects.get_or_create(user=target_user, team=team)
                if created:
                    messages.success(
                        request, f"Assigned {target_user.employee_id} to team '{team.name}'."
                    )
                else:
                    messages.info(
                        request,
                        f"User {target_user.employee_id} is already a member of team '{team.name}'.",
                    )
                return redirect(f"{request.path}?tab=people_teams")

        # -------------------------------------------------------------------
        # 10. Privileged Access: Break-Glass Emergency Elevation
        # -------------------------------------------------------------------
        elif action == "activate_break_glass":
            active_tab = "privileged"
            if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                messages.error(
                    request, "Only system administrators can activate emergency break-glass."
                )
            else:
                reason = request.POST.get("reason", "").strip()
                ticket = request.POST.get("ticket", "").strip()
                try:
                    activate_break_glass(request, reason=reason, justification_ticket=ticket)
                    messages.warning(
                        request,
                        "EMERGENCY BREAK-GLASS ACTIVATED. Privileged elevation active for 15 minutes.",
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc.message if hasattr(exc, "message") else exc))
                return redirect(f"{request.path}?tab=privileged")

        elif action == "revoke_break_glass":
            active_tab = "privileged"
            revoke_break_glass(request)
            messages.info(request, "Emergency break-glass privileges terminated.")
            return redirect(f"{request.path}?tab=privileged")

        # -------------------------------------------------------------------
        # 11. Service Identities: Provision & Revoke M2M Credentials
        # -------------------------------------------------------------------
        elif action == "create_service_identity":
            active_tab = "services"
            if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                messages.error(
                    request, "Only system administrators can provision Service Identities."
                )
            else:
                redirect_resp = enforce_mutation_assurance(request, "create_service_identity", required_level=2)
                if redirect_resp:
                    return redirect_resp

                name = request.POST.get("name", "").strip()
                client_id = request.POST.get("client_id", "").strip().lower()
                scopes = request.POST.getlist("scopes")
                expires_days = request.POST.get("expires_in_days", "").strip()
                exp_dt = None
                if expires_days and expires_days.isdigit() and int(expires_days) > 0:
                    exp_dt = timezone.now() + timedelta(days=int(expires_days))

                try:
                    ident, raw_tok = issue_service_identity(
                        name=name,
                        client_id=client_id,
                        scopes=scopes,
                        created_by=actor,
                        expires_at=exp_dt,
                    )
                    request.session["_salow_issued_service"] = {
                        "name": ident.name,
                        "client_id": ident.client_id,
                        "token": raw_tok,
                        "scopes": ident.scopes,
                    }
                    messages.success(
                        request,
                        f"Service Identity '{ident.name}' ({ident.client_id}) provisioned successfully.",
                    )
                    return redirect(f"{request.path}?tab=services")
                except ValidationError as exc:
                    messages.error(request, str(exc.message if hasattr(exc, "message") else exc))

        elif action == "revoke_service_identity":
            active_tab = "services"
            if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                messages.error(request, "Only system administrators can revoke Service Identities.")
            else:
                redirect_resp = enforce_mutation_assurance(request, "revoke_service_identity", required_level=2)
                if redirect_resp:
                    return redirect_resp

                cid = request.POST.get("client_id", "").strip().lower()
                if revoke_service_identity(cid, actor=actor):
                    messages.success(request, f"Service Identity '{cid}' revoked.")
                else:
                    messages.error(request, f"Service Identity '{cid}' not found.")
                return redirect(f"{request.path}?tab=services")

        # -------------------------------------------------------------------
        # 12. Delivery & Operations: Retry Outbox Item
        # -------------------------------------------------------------------
        elif action == "retry_outbox_item":
            active_tab = "delivery_ops"
            item_id = request.POST.get("item_id", "").strip()
            item_type = request.POST.get("item_type", "email")
            if item_type == "email" and item_id:
                try:
                    email_item = EmailOutbox.objects.filter(id=item_id).first()
                    if email_item:
                        email_item.status = EmailOutbox.Status.QUEUED
                        email_item.retry_count = 0
                        email_item.save(update_fields=["status", "retry_count", "updated_at"])
                        messages.success(
                            request, f"Email notification to {email_item.recipient} requeued."
                        )
                except (ValueError, ValidationError):
                    messages.error(request, f"Invalid email outbox ID: {item_id}")
            elif item_type == "event" and item_id:
                evt = OutboxEvent.objects.filter(event_id=item_id).first()
                if evt:
                    evt.status = OutboxEvent.Status.PENDING
                    evt.retry_count = 0
                    evt.save(update_fields=["status", "retry_count"])
                    messages.success(request, f"Outbox event {evt.event_id[:8]} requeued.")
            return redirect(f"{request.path}?tab=delivery_ops")

    # -----------------------------------------------------------------------
    # Query datasets for the 12 areas
    # -----------------------------------------------------------------------
    issued_service = request.session.pop("_salow_issued_service", None)

    # Search / Filters
    q_search = request.GET.get("q", "").strip()
    q_flow = request.GET.get("q_flow", "").strip()

    users_qs = CustomUser.objects.all().order_by("employee_id")
    if q_search:
        users_qs = users_qs.filter(employee_id__icontains=q_search) | users_qs.filter(
            full_name__icontains=q_search
        )

    # Overview & Telemetry Stats
    total_users_count = CustomUser.objects.count()
    mfa_enrolled_count = TOTPCredential.objects.filter(confirmed_at__isnull=False).count()
    active_recovery_codes_count = RecoveryCode.objects.filter(used_at__isnull=True).count()
    passkeys_count = PasskeyCredential.objects.count()
    active_sessions_count = UserSession.objects.count()
    events_24h_count = AuthEvent.objects.filter(
        time__gte=timezone.now() - timedelta(hours=24)
    ).count()
    rate_limits_count = RateLimitRecord.objects.count()
    service_identities_count = ServiceIdentity.objects.filter(is_active=True).count()

    # Section 4 Sessions
    active_sessions = UserSession.objects.select_related("user").order_by("-last_activity")[:50]

    # Section 5 Recovery
    pending_recovery_requests = RecoveryRequest.objects.filter(
        status=RecoveryRequest.Status.PENDING
    ).select_related("user")
    super_admins = CustomUser.objects.filter(
        role=CustomUser.Role.SUPER_ADMIN, is_active=True
    ).exclude(id=actor.id)
    recent_recoveries = RecoveryRequest.objects.select_related("user", "approved_by").order_by(
        "-created_at"
    )[:25]

    # Section 7 Audit
    audit_events = AuthEvent.objects.order_by("-time")[:60]
    if q_search:
        audit_events = audit_events.filter(employee_id__icontains=q_search)

    # Section 8 People & Teams
    departments = Department.objects.prefetch_related("teams__memberships__user").all()[:20]
    teams = Team.objects.select_related("department", "lead").all()[:30]
    memberships = TeamMembership.objects.select_related("team", "user").order_by("-joined_at")[:50]

    # Section 9 Sign-in Investigation
    investigation_events = AuthEvent.objects.order_by("-time")
    if q_flow:
        investigation_events = investigation_events.filter(
            models.Q(reference_id__icontains=q_flow) | models.Q(employee_id__icontains=q_flow)
        )
    investigation_events = investigation_events[:60]

    # Section 10 Privileged Access
    break_glass_active = is_break_glass_active(request)
    sudo_active = is_sudo_active(request)
    break_glass_history = AuthEvent.objects.filter(code="AUTH-BRK-001").order_by("-time")[:15]

    # Section 11 Service Identities
    service_identities = ServiceIdentity.objects.order_by("-created_at")[:50]

    # Section 12 Delivery & Operations
    email_outbox_stats = {
        s: EmailOutbox.objects.filter(status=s).count() for s in EmailOutbox.Status.values
    }
    recent_emails = EmailOutbox.objects.order_by("-created_at")[:25]
    recent_outbox_events = OutboxEvent.objects.order_by("-created_at")[:25]
    rate_limit_records = RateLimitRecord.objects.order_by("-timestamp")[:25]
    business_audits = AuditLog.objects.order_by("-created_at")[:30]

    # Section 13 & 14 Device Approvals & Security Operations
    pending_device_requests = DeviceApprovalRequest.objects.filter(
        status=DeviceApprovalRequest.Status.PENDING
    ).select_related("user")
    security_alerts_list = SecurityAlert.objects.order_by("-created_at")[:25]
    pending_fallback_requests = AttendanceFallbackRequest.objects.filter(
        status=AttendanceFallbackRequest.Status.PENDING
    ).select_related("user")[:25]

    context = {
        "actor": actor,
        "active_tab": active_tab,
        "issued_activation": issued_activation,
        "issued_service": issued_service,
        "users": users_qs[:100],
        "roles_matrix": ROLE_PERMISSIONS_MATRIX,
        "total_users_count": total_users_count,
        "mfa_enrolled_count": mfa_enrolled_count,
        "active_recovery_codes_count": active_recovery_codes_count,
        "passkeys_count": passkeys_count,
        "active_sessions_count": active_sessions_count,
        "events_24h_count": events_24h_count,
        "rate_limits_count": rate_limits_count,
        "service_identities_count": service_identities_count,
        "active_sessions": active_sessions,
        "pending_recovery_requests": pending_recovery_requests,
        "recent_recoveries": recent_recoveries,
        "super_admins": super_admins,
        "audit_events": audit_events,
        "business_audits": business_audits,
        "departments": departments,
        "teams": teams,
        "memberships": memberships,
        "investigation_events": investigation_events,
        "q_flow": q_flow,
        "break_glass_active": break_glass_active,
        "sudo_active": sudo_active,
        "break_glass_history": break_glass_history,
        "service_identities": service_identities,
        "valid_scopes": VALID_SCOPES,
        "email_outbox_stats": email_outbox_stats,
        "recent_emails": recent_emails,
        "recent_outbox_events": recent_outbox_events,
        "rate_limit_records": rate_limit_records,
        "q_search": q_search,
        "role_choices": CustomUser.Role.choices,
        "pending_device_requests": pending_device_requests,
        "security_alerts_list": security_alerts_list,
        "pending_fallback_requests": pending_fallback_requests,
    }
    return render(request, "accounts/admin/iam_dashboard.html", context)


@login_required
def admin_iam_live_stream_view(request):
    """
    Authenticated Server-Sent Events (SSE) live monitoring stream.
    Streams real-time AuthEvent records to administrative operators.
    Restricted to system administrators: super_admin and admin.
    """
    actor: CustomUser = request.user
    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Live security monitoring is restricted to system administrators.")

    # Parse limit safely
    limit_str = request.GET.get("limit", "20")
    try:
        limit = max(1, min(100, int(limit_str)))
    except (ValueError, TypeError):
        limit = 20

    # HTMX Polling Support (Section 13 & Defect 7)
    if request.headers.get("HX-Request") or request.GET.get("format") == "html":
        from django.http import HttpResponse
        events = AuthEvent.objects.order_by("-time")[:limit]
        rows = []
        for evt in events:
            is_info = (evt.code == "INFO")
            badge_color = "#059669" if is_info else "#dc2626"
            t_str = evt.time.strftime("%H:%M:%S") if evt.time else ""
            row_html = (
                f'<div style="display:flex; justify-content:space-between; align-items:center; '
                f'padding:0.4rem 0.6rem; margin-bottom:4px; border-radius:4px; background:#1e293b; '
                f'border-left:3px solid {badge_color}; color:#f1f5f9; font-size:0.85rem;">'
                f'<div style="display:flex; gap:0.75rem; align-items:center; flex-wrap:wrap;">'
                f'<span style="color:#94a3b8;">{t_str}</span>'
                f'<span style="padding:0.15rem 0.4rem; border-radius:3px; background:{badge_color}; font-weight:700; font-size:0.72rem;">{evt.event}</span>'
                f'<span style="font-weight:600; color:#38bdf8;">{evt.employee_id or "ANONYMOUS"}</span>'
                f'<span style="color:#cbd5e1;">[{evt.step}] {evt.message}</span>'
                f'</div>'
                f'<div style="color:#64748b; font-size:0.72rem;">{evt.reference_id or "-"} | {evt.network_address or "-"}</div>'
                f'</div>'
            )
            rows.append(row_html)
        return HttpResponse("".join(rows) or '<div style="color:#94a3b8; padding:0.5rem;">No security events recorded.</div>', content_type="text/html")

    # Support standard Last-Event-ID header and last_id query param
    last_id_str = request.headers.get("Last-Event-ID") or request.GET.get("last_id")
    last_id = None
    if last_id_str and last_id_str.isdigit():
        last_id = int(last_id_str)

    def event_stream():
        # Initial ping handshake
        yield f"event: ping\ndata: {json.dumps({'status': 'connected', 'time': timezone.now().isoformat()})}\n\n"

        qs = AuthEvent.objects.all()
        if last_id is not None:
            events = qs.filter(id__gt=last_id).order_by("time")[:limit]
        else:
            events = reversed(list(qs.order_by("-time")[:limit]))

        for evt in events:
            payload = {
                "id": evt.id,
                "time": evt.time.strftime("%H:%M:%S"),
                "event": evt.event,
                "code": evt.code,
                "step": evt.step,
                "employee_id": evt.employee_id or "ANONYMOUS",
                "reference_id": evt.reference_id or "-",
                "network_address": str(evt.network_address or "-"),
                "message": evt.message,
            }
            yield f"id: {evt.id}\nevent: auth_event\ndata: {json.dumps(payload)}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required
def admin_security_shield_view(request):  # noqa: C901
    """
    Administrator Security Shield & Verification Panel.
    Restricted to system administrators: super_admin and admin.
    Performs verified multi-factor security checks:
    1. Read-only Admin ID and Role verification
    2. RBAC privilege verification
    3. TOTP code validation
    4. 1:1 Face verification against enrolled biometric template
    Measures and displays exact execution latencies (ms).
    Fails safely on timeout or mismatch, grants elevated sudo on success.
    """
    actor: CustomUser = request.user
    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Access restricted to system administrators.")

    has_enrolled_face = FaceTemplate.objects.filter(user=actor).exists()
    shield_verified = is_sudo_active(request)
    error = None
    latencies = {
        "rbac_ms": 1.2,
        "totp_ms": None,
        "face_ms": None,
        "total_ms": None,
    }
    verification_step_results = {
        "rbac": True,
        "totp": None,
        "face": None,
    }

    if request.method == "POST":
        action = request.POST.get("action", "")
        start_total = time.time()

        if action == "verify_shield":
            totp_code = request.POST.get("totp_code", "").strip()

            # 1. RBAC Evaluation
            t_rbac_start = time.time()
            is_admin_rbac = actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)
            latencies["rbac_ms"] = round((time.time() - t_rbac_start) * 1000, 2)
            verification_step_results["rbac"] = is_admin_rbac

            if not is_admin_rbac:
                error = (
                    "RBAC Authorization Failed: User does not possess administrator credentials."
                )

            # 2. TOTP Code Verification
            if not error:
                if not totp_code:
                    error = "Please enter your 6-digit administrator authenticator code."
                    verification_step_results["totp"] = False
                else:
                    t_totp_start = time.time()
                    try:
                        verify_totp_code(actor, totp_code)
                        latencies["totp_ms"] = round((time.time() - t_totp_start) * 1000, 2)
                        verification_step_results["totp"] = True
                    except AuthException as exc:
                        latencies["totp_ms"] = round((time.time() - t_totp_start) * 1000, 2)
                        verification_step_results["totp"] = False
                        error = f"Authenticator verification failed: {exc.user_message}"

            # 3. 1:1 Face Verification
            if not error:
                t_face_start = time.time()
                try:
                    face_res = verify_face(
                        user=actor,
                        purpose=FaceVerificationLog.Purpose.STEP_UP,
                    )
                    latencies["face_ms"] = round((time.time() - t_face_start) * 1000, 2)
                    if face_res.success:
                        verification_step_results["face"] = True
                    else:
                        if has_enrolled_face:
                            verification_step_results["face"] = False
                            error = f"Biometric verification failed: {face_res.error_reason}"
                        else:
                            # Face template not yet enrolled; biometric check marked false but fallback permitted
                            verification_step_results["face"] = False
                except Exception as exc:
                    latencies["face_ms"] = round((time.time() - t_face_start) * 1000, 2)
                    verification_step_results["face"] = False
                    if has_enrolled_face:
                        error = f"Biometric engine error: {exc}"

            # If all passed, grant sudo
            if (
                not error
                and verification_step_results["totp"]
                and (verification_step_results["face"] or not has_enrolled_face)
            ):
                # When face template is enrolled, face verification must be True
                if has_enrolled_face and not verification_step_results["face"]:
                    shield_verified = False
                else:
                    grant_sudo(request, actor)
                    shield_verified = True
                    latencies["total_ms"] = max(0.01, round((time.time() - start_total) * 1000, 2))
                    messages.success(
                        request,
                        f"Administrator Security Shield Verified ({latencies['total_ms']}ms total latency). "
                        f"Elevated Sudo privilege active for 10 minutes.",
                    )
            else:
                shield_verified = False
                latencies["total_ms"] = max(0.01, round((time.time() - start_total) * 1000, 2))

    context = {
        "actor": actor,
        "shield_verified": shield_verified,
        "has_enrolled_face": has_enrolled_face,
        "error": error,
        "latencies": latencies,
        "step_results": verification_step_results,
    }
    return render(request, "accounts/admin/security_shield.html", context)
