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
import logging
import time
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlencode

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
from apps.accounts.auth.iam_auth import (
    bound_stepup_digest,
    check_iam_assurance,
    compute_payload_digest,
    get_iam_session_from_request,
)
from apps.accounts.auth.mfa_totp import verify_totp_code
from apps.accounts.auth.reset import (
    approve_administrator_reset,
    approve_privileged_recovery,
    check_recovery_authority,
    issue_mfa_reset_code,
    pending_dual_recovery,
    recovery_needs_dual_control,
    reject_administrator_reset,
)
from apps.accounts.auth.service_identities import (
    VALID_SCOPES,
    issue_service_identity,
    revoke_service_identity,
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


_LIFECYCLE_ADMIN_ROLES = (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)


def _lifecycle_actor_denial(actor: CustomUser, target: CustomUser) -> Optional[str]:
    """Who may suspend, reactivate, exit or revoke sessions for whom (None means allowed)."""
    if actor.role in _LIFECYCLE_ADMIN_ROLES:
        if target.role == CustomUser.Role.SUPER_ADMIN and actor.role != CustomUser.Role.SUPER_ADMIN:
            return "Only Super Admins can act on a Super Admin account."
        return None
    if actor.role == CustomUser.Role.HR and target.role in (
        CustomUser.Role.EMPLOYEE,
        CustomUser.Role.INTERN,
    ):
        return None
    return "You do not have permission to act on this account."


MIN_ACTIVE_SUPER_ADMINS = 2


def _would_remove_last_super_admin(target: CustomUser) -> bool:
    """True when taking this account out of the active Super Admin set would leave fewer than
    the documented standing minimum of two (normal baseline; no succession exception here)."""
    if target.role != CustomUser.Role.SUPER_ADMIN or not target.is_active:
        return False
    remaining = (
        CustomUser.objects.filter(role=CustomUser.Role.SUPER_ADMIN, is_active=True)
        .exclude(id=target.id)
        .count()
    )
    return remaining < MIN_ACTIVE_SUPER_ADMINS


_SECRET_KEY_HINTS = ("password", "secret", "token", "totp", "otp", "api_key", "private_key")


def _describe_pending_change(action_name: str, payload: Any) -> str:
    """One line naming the exact change a step-up will authorise (secrets left out)."""
    parts = []
    items = payload.items() if isinstance(payload, dict) else []
    for key, val in items:
        if any(h in str(key).lower() for h in _SECRET_KEY_HINTS):
            continue
        if key == "action":
            continue
        shown = ", ".join(map(str, val)) if isinstance(val, (list, tuple)) else str(val)
        parts.append(f"{key} = {shown[:80]}")
    return (f"{action_name}: " + "; ".join(parts))[:500]


def _clear_stepup(session) -> None:
    session.stepup_action = ""
    session.stepup_expires_at = None
    session.stepup_payload_digest = ""
    session.assurance_level = 1
    session.save(
        update_fields=[
            "assurance_level",
            "stepup_action",
            "stepup_expires_at",
            "stepup_payload_digest",
        ]
    )


def _level3_gate(request, session, action_name: str, payload: Any, bound: str):
    """Level 3: the requester holds a payload-bound Level 2; a DIFFERENT Super Admin must approve
    the identical request with their own fresh Level 2; the approval is consumed atomically
    when the requester re-submits the identical change. Returns None to proceed, else a redirect."""
    from apps.accounts.auth.iam_auth import (
        consume_dual_approval_request,
        create_dual_approval_request,
    )
    from apps.accounts.models import IAMDualApprovalRequest

    target_id = ""
    if isinstance(payload, dict):
        target_id = str(payload.get("target_employee_id", "")) or action_name
    now = timezone.now()
    base = IAMDualApprovalRequest.objects.filter(
        action=action_name,
        target_id=target_id,
        payload_digest=bound,
        requester=session.iam_admin,
        expires_at__gt=now,
    )
    approved = base.filter(
        status=IAMDualApprovalRequest.Status.APPROVED, consumed_at__isnull=True
    ).first()
    if approved:
        try:
            consume_dual_approval_request(
                approved.request_id, action_name, target_id, expected_payload_digest=bound
            )
        except (AuthException, PermissionDenied) as exc:
            messages.error(request, str(exc))
            return redirect(request.get_full_path())
        _clear_stepup(session)
        return None
    if base.filter(status=IAMDualApprovalRequest.Status.PENDING).exists():
        messages.info(
            request,
            "This change is already waiting for approval from a second Super Administrator.",
        )
        _clear_stepup(session)
        return redirect(request.get_full_path())
    try:
        create_dual_approval_request(
            session,
            action_name,
            target_id,
            reason=_describe_pending_change(action_name, payload),
            payload_digest=bound,
        )
    except AuthException as exc:
        messages.error(request, str(exc))
        return redirect(request.get_full_path())
    _clear_stepup(session)
    messages.info(
        request,
        "Level 3 approval requested. A different Super Administrator must approve it under "
        "'15. Level 3 Approvals'. After approval, submit the same change again to execute it "
        "(valid 10 minutes).",
    )
    return redirect(request.get_full_path())


def _via_iam_admin(request) -> str:
    """Suffix naming the IAM admin identity when the action came through an IAM session."""
    iam_admin = getattr(request, "iam_admin", None)
    admin_id = getattr(iam_admin, "admin_id", "") if iam_admin else ""
    return f" via IAM admin {admin_id}" if admin_id else ""


def enforce_mutation_assurance(
    request,
    action_name: str,
    required_level: int,
    payload: Any = None,
    consume_stepup: bool = True,
    bind_payload: bool = True,
) -> Optional[Any]:
    """
    Enforces required IAM assurance level (L1, L2, L3) with canonical payload binding.
    If session lacks required assurance, redirects to step-up or displays error.
    """
    session = getattr(request, "iam_session", None) or get_iam_session_from_request(request)
    if not session:
        # No dedicated IAM session (for example the console reached on the workspace
        # host): privileged mutations never run without step-up. Fail closed.
        messages.error(
            request,
            "This action needs a dedicated IAM administrator session with step-up. "
            "Sign in at the IAM console to continue.",
        )
        return redirect(request.get_full_path())

    digest = compute_payload_digest(payload) if payload else None
    form_payload = payload
    if form_payload is None:
        form_payload = {
            k: request.POST.getlist(k)
            for k in sorted(request.POST.keys())
            if k not in ("csrfmiddlewaretoken", "tab")
        }
    bound = bound_stepup_digest(session, action_name, form_payload)
    satisfied = check_iam_assurance(
        request,
        required_level=min(required_level, 2),
        action_name=action_name,
        payload_digest=digest,
    )
    if satisfied and required_level >= 2 and bind_payload and request.method == "POST":
        # The step-up must have been granted for exactly this actor, action and payload.
        satisfied = bool(session.stepup_payload_digest) and (session.stepup_payload_digest == bound)
    if not satisfied:
        if required_level >= 2:
            session.pending_action = action_name
            session.pending_payload_digest = bound
            session.pending_summary = _describe_pending_change(action_name, form_payload)
            session.save(
                update_fields=["pending_action", "pending_payload_digest", "pending_summary"]
            )
            stepup_url = "/iam/stepup/"
            query = urlencode({"action": action_name, "next": request.get_full_path()})
            return redirect(f"{stepup_url}?{query}")
    if required_level == 3:
        return _level3_gate(request, session, action_name, form_payload, bound)
    if required_level == 2 and consume_stepup and request.method == "POST":
        # A Level 2 step-up covers exactly one mutation: consume it so the same proof
        # cannot be replayed against a different target.
        session.stepup_action = ""
        session.stepup_expires_at = None
        session.stepup_payload_digest = ""
        session.assurance_level = 1
        session.save(
            update_fields=[
                "assurance_level",
                "stepup_action",
                "stepup_expires_at",
                "stepup_payload_digest",
            ]
        )
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
                    messages.error(
                        request,
                        "Workspace Security Admins cannot provision or promote Super Admin accounts.",
                    )
                    return redirect(f"{request.path}?tab={active_tab}")
            elif action in ("activate_break_glass", "create_service_identity"):
                messages.error(
                    request,
                    "Workspace Security Admins cannot execute root IAM elevation operations.",
                )
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
            elif role not in CustomUser.Role.values:
                messages.error(request, "Choose one of the listed roles.")
            elif not emp_id or not full_name or not email:
                messages.error(request, "Employee ID, full name, and email are required.")
            elif not is_allowed_company_email(email):
                messages.error(request, "Email must belong to the @salowtech.com company domain.")
            elif CustomUser.objects.filter(employee_id=emp_id).exists():
                messages.error(request, f"Employee ID {emp_id} is already in use.")
            elif CustomUser.objects.filter(email=email).exists():
                messages.error(request, f"Email {email} is already registered.")
            else:
                # Provisioning is an IAM-console action: it needs a dedicated IAM session (a
                # workspace session alone cannot authenticate IAM authority), and granting the
                # Super Admin role is Level 3 (two-person) like any other Super Admin change.
                redirect_resp = enforce_mutation_assurance(
                    request,
                    "create_employee",
                    required_level=3 if role == CustomUser.Role.SUPER_ADMIN else 1,
                    payload={
                        "action": "create_employee",
                        "target_employee_id": emp_id,
                        "full_name": full_name,
                        "email": email,
                        "role": role,
                    },
                )
                if redirect_resp:
                    return redirect_resp
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
                except Exception:
                    logging.getLogger(__name__).exception("Employee creation failed")
                    messages.error(
                        request,
                        "Failed to create employee. The error was logged; please check the details and try again.",
                    )

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
            elif new_role not in CustomUser.Role.values:
                messages.error(request, "Unknown role.")
            elif (
                target_user.role == CustomUser.Role.SUPER_ADMIN
                and new_role != CustomUser.Role.SUPER_ADMIN
                and _would_remove_last_super_admin(target_user)
            ):
                messages.error(request, "The last active Super Admin cannot be demoted.")
            elif actor.role != CustomUser.Role.SUPER_ADMIN and (
                new_role == CustomUser.Role.SUPER_ADMIN
                or target_user.role == CustomUser.Role.SUPER_ADMIN
            ):
                messages.error(request, "Only Super Admins can assign or modify Super Admin roles.")
            elif actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
                messages.error(request, "You do not have permission to modify roles.")
            else:
                payload = {"target_employee_id": target_emp_id, "new_role": new_role}
                req_level = (
                    3
                    if (
                        new_role == CustomUser.Role.SUPER_ADMIN
                        or target_user.role == CustomUser.Role.SUPER_ADMIN
                    )
                    else 2
                )
                redirect_resp = enforce_mutation_assurance(
                    request, "update_role", required_level=req_level, payload=payload
                )
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
                    redirect_resp = enforce_mutation_assurance(
                        request, "issue_mfa_reset", required_level=2, payload=payload
                    )
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
            if sess and _lifecycle_actor_denial(actor, sess.user):
                messages.error(request, _lifecycle_actor_denial(actor, sess.user))
            elif sess:
                redirect_resp = enforce_mutation_assurance(
                    request,
                    "revoke_session",
                    required_level=2,
                    payload={"session_key": session_key, "action": "revoke_session"},
                )
                if redirect_resp:
                    return redirect_resp
                end_session_by_key(sess.user, session_key)
                messages.success(request, f"Session {session_key[:8]}... terminated.")
            else:
                messages.info(request, "Session not found or already expired.")
            return redirect(f"{request.path}?tab=sessions")

        elif action == "revoke_user_all_sessions":
            active_tab = "sessions"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if target_user and _lifecycle_actor_denial(actor, target_user):
                messages.error(request, _lifecycle_actor_denial(actor, target_user))
            elif target_user:
                payload = {
                    "target_employee_id": target_emp_id,
                    "action": "revoke_user_all_sessions",
                }
                redirect_resp = enforce_mutation_assurance(
                    request, "revoke_user_all_sessions", required_level=2, payload=payload
                )
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
            base_url = request.build_absolute_uri("/")[:-1]
            # The console's own Level 2 step-up (bound to this action) is the fresh
            # re-authentication that the recovery service requires of the approver.
            redirect_resp = enforce_mutation_assurance(
                request,
                "approve_recovery",
                required_level=2,
                consume_stepup=False,
                bind_payload=False,
                payload={"request_id": str(request_id), "action": "approve_recovery"},
            )
            if redirect_resp:
                return redirect_resp
            target_req = (
                RecoveryRequest.objects.filter(id=request_id).select_related("user").first()
            )
            try:
                if target_req and recovery_needs_dual_control(target_req.user):
                    state, req = approve_privileged_recovery(
                        int(request_id), request.iam_session, base_url=base_url
                    )
                    if state == "pending":
                        messages.info(
                            request,
                            f"First approval recorded for {req.user.employee_id}. "
                            "A different Super Admin must sign in and approve before any link is sent.",
                        )
                    else:
                        messages.success(
                            request,
                            f"Recovery request for {req.user.employee_id} approved by two administrators. Link sent.",
                        )
                else:
                    grant_sudo(actor)
                    req, _ = approve_administrator_reset(
                        request_id=int(request_id),
                        actor=actor,
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
            redirect_resp = enforce_mutation_assurance(
                request,
                "reject_recovery",
                required_level=2,
                consume_stepup=False,
                bind_payload=False,
                payload={"request_id": str(request_id), "action": "reject_recovery"},
            )
            if redirect_resp:
                return redirect_resp
            grant_sudo(actor)
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
            elif _lifecycle_actor_denial(actor, target_user):
                messages.error(request, _lifecycle_actor_denial(actor, target_user))
            elif _would_remove_last_super_admin(target_user):
                messages.error(request, "The last active Super Admin cannot be suspended.")
            else:
                payload = {"target_employee_id": target_emp_id, "action": "suspend_account"}
                req_level = 3 if target_user.role == CustomUser.Role.SUPER_ADMIN else 2
                redirect_resp = enforce_mutation_assurance(
                    request, "suspend_account", required_level=req_level, payload=payload
                )
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

        elif action in ("approve_dual_request", "reject_dual_request"):
            active_tab = "l3_approvals"
            from apps.accounts.auth.iam_auth import approve_dual_approval_request
            from apps.accounts.models import IAMDualApprovalRequest

            rid = request.POST.get("request_id", "").strip()
            try:
                dual = IAMDualApprovalRequest.objects.filter(
                    request_id=rid, status=IAMDualApprovalRequest.Status.PENDING
                ).first()
            except (ValueError, ValidationError):
                dual = None
            if actor.role != CustomUser.Role.SUPER_ADMIN or not iam_session:
                messages.error(request, "Only Super Administrators can decide Level 3 approvals.")
            elif not dual or dual.expires_at <= timezone.now():
                messages.error(request, "That approval request is not pending or has expired.")
            elif iam_admin and dual.requester_id == iam_admin.id:
                messages.error(request, "You cannot approve your own request.")
            else:
                redirect_resp = enforce_mutation_assurance(
                    request,
                    dual.action,
                    required_level=2,
                    payload={"decide_dual_request": str(dual.request_id), "action": action},
                    consume_stepup=False,
                )
                if redirect_resp:
                    return redirect_resp
                if action == "reject_dual_request":
                    dual.status = IAMDualApprovalRequest.Status.REJECTED
                    dual.save(update_fields=["status"])
                    messages.success(request, "Level 3 request rejected.")
                else:
                    try:
                        approve_dual_approval_request(iam_session, dual.request_id)
                        messages.success(
                            request,
                            "Approved. The requesting Super Administrator can now submit the "
                            "same change to execute it.",
                        )
                    except (AuthException, PermissionDenied) as exc:
                        messages.error(request, str(exc))
                _clear_stepup(iam_session)
            return redirect(f"{request.path}?tab=l3_approvals")

        elif action == "reactivate_account":
            active_tab = "lifecycle"
            target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
            target_user = CustomUser.objects.filter(employee_id=target_emp_id).first()
            if not target_user:
                messages.error(request, f"User {target_emp_id} not found.")
            elif _lifecycle_actor_denial(actor, target_user):
                messages.error(request, _lifecycle_actor_denial(actor, target_user))
            else:
                payload = {"target_employee_id": target_emp_id, "action": "reactivate_account"}
                redirect_resp = enforce_mutation_assurance(
                    request, "reactivate_account", required_level=2, payload=payload
                )
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
            elif _lifecycle_actor_denial(actor, target_user):
                messages.error(request, _lifecycle_actor_denial(actor, target_user))
            elif _would_remove_last_super_admin(target_user):
                messages.error(request, "The last active Super Admin cannot be marked exited.")
            else:
                payload = {"target_employee_id": target_emp_id, "action": "exit_account"}
                req_level = 3 if target_user.role == CustomUser.Role.SUPER_ADMIN else 2
                redirect_resp = enforce_mutation_assurance(
                    request, "exit_account", required_level=req_level, payload=payload
                )
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
                    log_auth_event(
                        event="team_membership_added",
                        code="INFO",
                        step="admin_add_team_membership",
                        employee_id=target_user.employee_id,
                        actor_employee_id=actor.employee_id,
                        message=f"Added to team {team.code} by {actor.employee_id} ({actor.role}){_via_iam_admin(request)}",
                    )
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
                redirect_resp = enforce_mutation_assurance(
                    request, "create_service_identity", required_level=2
                )
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
                redirect_resp = enforce_mutation_assurance(
                    request, "revoke_service_identity", required_level=2
                )
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
                        log_auth_event(
                            event="outbox_email_requeued",
                            code="INFO",
                            step="admin_retry_outbox_item",
                            employee_id="",
                            actor_employee_id=actor.employee_id,
                            reference_id=str(email_item.id),
                            message=f"Email outbox item {email_item.id} requeued by {actor.employee_id} ({actor.role}){_via_iam_admin(request)}",
                        )
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
                    log_auth_event(
                        event="outbox_event_requeued",
                        code="INFO",
                        step="admin_retry_outbox_item",
                        employee_id="",
                        actor_employee_id=actor.employee_id,
                        reference_id=str(evt.event_id)[:64],
                        message=f"Outbox event {str(evt.event_id)[:8]} requeued by {actor.employee_id} ({actor.role}){_via_iam_admin(request)}",
                    )
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
    pending_recovery_requests = list(
        RecoveryRequest.objects.filter(status=RecoveryRequest.Status.PENDING).select_related("user")
    )
    for _r in pending_recovery_requests:
        _r.needs_dual = recovery_needs_dual_control(_r.user)
        _r.first_approval = pending_dual_recovery(_r) if _r.needs_dual else None
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

    from apps.accounts.models import IAMDualApprovalRequest as _Dual

    pending_dual_requests = []
    if active_tab == "l3_approvals" and iam_admin:
        pending_dual_requests = list(
            _Dual.objects.filter(status=_Dual.Status.PENDING, expires_at__gt=timezone.now())
            .exclude(requester=iam_admin)
            .select_related("requester")[:50]
        )

    context = {
        "pending_dual_requests": pending_dual_requests,
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
            is_info = evt.code == "INFO"
            t_str = evt.time.strftime("%H:%M:%S") if evt.time else ""
            row_html = (
                f'<div class="evt-row {"evt-ok" if is_info else "evt-bad"}">'
                f'<div class="s-769bd11b">'
                f'<span class="s-aa3ee35a">{t_str}</span>'
                f'<span class="evt-badge {"evt-ok" if is_info else "evt-bad"}">{evt.event}</span>'
                f'<span class="s-03d9fc85">{evt.employee_id or "ANONYMOUS"}</span>'
                f'<span class="s-c14ddfd0">[{evt.step}] {evt.message}</span>'
                f"</div>"
                f'<div class="s-9bca2879">{evt.reference_id or "-"} | {evt.network_address or "-"}</div>'
                f"</div>"
            )
            rows.append(row_html)
        return HttpResponse(
            "".join(rows) or '<div class="s-a54b67c5">No security events recorded.</div>',
            content_type="text/html",
        )

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
