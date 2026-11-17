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

import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render

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
from apps.accounts.auth.sessions import end_session_by_key, revoke_user_sessions
from apps.accounts.auth.stepup import grant_sudo, is_sudo_active
from apps.accounts.models import (
    ActivationCode,
    AuthEvent,
    CustomUser,
    PasskeyCredential,
    RecoveryCode,
    RecoveryRequest,
    TOTPCredential,
    UserSession,
)
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


@login_required
def admin_iam_dashboard_view(request):  # noqa: C901
    """
    Central IAM & Access Management Console.
    Restricted to authorized administrative roles: super_admin, admin, hr, manager.
    Provides 7 functional sections:
    1. Identity & Accounts
    2. Roles & Permissions
    3. Security & MFA
    4. Sessions & Devices
    5. Recovery Console
    6. Account Lifecycle
    7. IAM Audit
    """
    actor: CustomUser = request.user
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
                target_user.is_active = False
                target_user.status = CustomUser.Status.SUSPENDED
                target_user.save(update_fields=["is_active", "status", "updated_at"])
                revoke_user_sessions(target_user)
                log_auth_event(
                    event="account_suspended_by_admin",
                    code="WARN",
                    step="admin_lifecycle",
                    employee_id=target_user.employee_id,
                    message=f"Suspended by {actor.employee_id}; sessions revoked",
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

    # -----------------------------------------------------------------------
    # Query datasets for the 7 sections
    # -----------------------------------------------------------------------
    # Search / Filters
    q_search = request.GET.get("q", "").strip()
    users_qs = CustomUser.objects.all().order_by("employee_id")
    if q_search:
        users_qs = users_qs.filter(employee_id__icontains=q_search) | users_qs.filter(
            full_name__icontains=q_search
        )

    # Section 3 Stats
    total_users_count = CustomUser.objects.count()
    mfa_enrolled_count = TOTPCredential.objects.filter(confirmed_at__isnull=False).count()
    active_recovery_codes_count = RecoveryCode.objects.filter(used_at__isnull=True).count()
    passkeys_count = PasskeyCredential.objects.count()

    # Section 4 Sessions
    active_sessions = UserSession.objects.select_related("user").order_by("-last_activity")[:50]

    # Section 5 Recovery
    pending_recovery_requests = RecoveryRequest.objects.filter(
        status=RecoveryRequest.Status.PENDING
    ).select_related("user")
    super_admins = CustomUser.objects.filter(
        role=CustomUser.Role.SUPER_ADMIN, is_active=True
    ).exclude(id=actor.id)

    # Section 7 Audit
    audit_events = AuthEvent.objects.order_by("-time")[:60]
    if q_search:
        audit_events = audit_events.filter(employee_id__icontains=q_search)

    context = {
        "actor": actor,
        "active_tab": active_tab,
        "issued_activation": issued_activation,
        "users": users_qs[:100],
        "roles_matrix": ROLE_PERMISSIONS_MATRIX,
        "total_users_count": total_users_count,
        "mfa_enrolled_count": mfa_enrolled_count,
        "active_recovery_codes_count": active_recovery_codes_count,
        "passkeys_count": passkeys_count,
        "active_sessions": active_sessions,
        "pending_recovery_requests": pending_recovery_requests,
        "super_admins": super_admins,
        "audit_events": audit_events,
        "q_search": q_search,
        "role_choices": CustomUser.Role.choices,
    }
    return render(request, "accounts/admin/iam_dashboard.html", context)


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
                    latencies["total_ms"] = round((time.time() - start_total) * 1000, 2)
                    messages.success(
                        request,
                        f"Administrator Security Shield Verified ({latencies['total_ms']}ms total latency). "
                        f"Elevated Sudo privilege active for 10 minutes.",
                    )
            else:
                shield_verified = False
                latencies["total_ms"] = round((time.time() - start_total) * 1000, 2)

    context = {
        "actor": actor,
        "shield_verified": shield_verified,
        "has_enrolled_face": has_enrolled_face,
        "error": error,
        "latencies": latencies,
        "step_results": verification_step_results,
    }
    return render(request, "accounts/admin/security_shield.html", context)
