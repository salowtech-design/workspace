"""
Account Recovery & Password Reset Views.
Implements:
1. Public timing-safe forgot password entry point (anti-enumeration).
2. Self-service password reset using single-use backup recovery codes.
3. Passkey-authenticated password reset (W3C WebAuthn assertion - Scenario E).
4. MFA authenticator reset token redemption (Scenario H).
5. Administrative & delegated HR/Manager recovery console with Sudo mode enforcement.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.passkeys import verify_passkey_authentication
from apps.accounts.auth.passwords import set_user_password, validate_employee_password
from apps.accounts.auth.reset import (
    complete_password_reset_with_token,
    issue_administrative_recovery_token,
    issue_mfa_reset_code,
    self_service_reset_password,
    verify_mfa_reset_and_authorize,
)
from apps.accounts.auth.sessions import revoke_user_sessions
from apps.accounts.auth.stepup import is_sudo_active
from apps.accounts.models import CustomUser
from apps.accounts.permissions import (
    PERM_RECOVERY_ADMIN,
    PERM_RECOVERY_DELEGATED,
    has_permission,
)

RECOVERY_PENDING_UID_KEY = "_salow_rec_pending_uid"
RECOVERY_METHOD_KEY = "_salow_rec_method"


def forgot_password_view(request):  # noqa: C901
    """
    Public forgot-password entry point.
    Prevents account enumeration by displaying generic confirmation instructions
    regardless of whether the employee ID exists.
    Supports recovery via backup recovery code, administrative password reset token,
    or MFA reset token for lost authenticators.
    """
    if request.user.is_authenticated:
        return redirect("attendance:panel")

    error = None
    step = request.GET.get("step", "identify")

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "redeem_code":
            emp_id = request.POST.get("employee_id", "").strip().upper()
            code_type = request.POST.get("code_type", "recovery_code")
            code_value = request.POST.get("code_value", "").strip()
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")
            current_password = request.POST.get("current_password", "")

            if code_type != "mfa_reset_token" and new_password != confirm_password:
                error = "Passwords do not match."
            elif not code_value:
                error = "Please enter your recovery code or administrative reset token."
            else:
                try:
                    if code_type == "admin_token":
                        complete_password_reset_with_token(
                            employee_id=emp_id,
                            raw_code=code_value,
                            new_password=new_password,
                        )
                        messages.success(
                            request,
                            "Your password has been successfully reset. Please sign in with your new password.",
                        )
                        return redirect("accounts:login")
                    elif code_type == "mfa_reset_token":
                        if not current_password:
                            error = "Your current password is required to verify your identity for an MFA reset."
                        else:
                            authorized_user = verify_mfa_reset_and_authorize(
                                employee_id=emp_id,
                                raw_code=code_value,
                                password=current_password,
                            )
                            request.session["_salow_act_user_id"] = authorized_user.id
                            messages.success(
                                request,
                                "Old authenticator removed. Please configure your new authenticator app.",
                            )
                            return redirect("accounts:activate_mfa_setup")
                    else:
                        self_service_reset_password(
                            employee_id=emp_id,
                            credential_token=code_value,
                            new_password=new_password,
                            is_recovery_code=True,
                        )
                        messages.success(
                            request,
                            "Your password has been successfully reset. Please sign in with your new password.",
                        )
                        return redirect("accounts:login")
                except AuthException as e:
                    error = e.user_message
                except ValidationError as e:
                    error = e.messages[0] if hasattr(e, "messages") else str(e)
                except Exception:
                    error = "Invalid recovery submission. Please verify your employee ID and code."

    return render(
        request,
        "accounts/recovery/forgot_password.html",
        {"error": error, "step": step},
    )


@require_POST
def passkey_recovery_verify_view(request):
    """
    Scenario E: Forgotten password recovery using registered WebAuthn passkey.
    Validates device assertion, enforces password complexity, sets new password,
    revokes old sessions, and audits the recovery event.
    """
    try:
        body = json.loads(request.body.decode("utf-8"))
        credential_data = body.get("credential")
        emp_id = body.get("employee_id", "").strip().upper()
        new_password = body.get("new_password", "")

        if not credential_data:
            return JsonResponse({"error": "Missing WebAuthn assertion payload."}, status=400)
        if not emp_id:
            return JsonResponse({"error": "Employee ID is required."}, status=400)
        if not new_password:
            return JsonResponse({"error": "New password is required."}, status=400)

        user = CustomUser.objects.filter(employee_id=emp_id, is_active=True).first()
        if not user:
            return JsonResponse({"error": "Invalid employee ID or passkey."}, status=400)

        authenticated_user = verify_passkey_authentication(
            request=request,
            credential_data=credential_data,
            user=user,
        )

        validate_employee_password(new_password, authenticated_user)

        with transaction.atomic():
            set_user_password(authenticated_user, new_password)
            revoke_user_sessions(authenticated_user)

            log_auth_event(
                event="password_reset_passkey",
                code="INFO",
                step="passkey_recovery",
                employee_id=authenticated_user.employee_id,
                message="Password reset successfully via registered WebAuthn passkey assertion",
            )

        return JsonResponse(
            {
                "success": True,
                "message": "Password reset successfully. Please sign in with your new password.",
                "redirect_url": "/auth/login/",
            }
        )
    except AuthException as e:
        return JsonResponse({"error": e.user_message}, status=400)
    except ValidationError as e:
        msg = e.messages[0] if hasattr(e, "messages") else str(e)
        return JsonResponse({"error": msg}, status=400)
    except Exception as e:
        return JsonResponse({"error": f"Passkey recovery failed: {e}"}, status=400)


@login_required
def admin_recovery_console_view(request):  # noqa: C901
    """
    Dedicated recovery management console for Super Admins, Admins, HR, and Managers.
    Enforces server-side authority scope and Sudo mode re-authentication:
    - Super Admin: all accounts (Super Admin target requires second Super Admin approval).
    - Admin: Admin, HR, Manager, Employee, Intern (Admin target requires second Admin approval).
    - HR: Employee, Intern.
    - Manager: Employee, Intern within direct reporting or led team.
    """
    actor: CustomUser = request.user
    if not (
        has_permission(actor, PERM_RECOVERY_DELEGATED) or has_permission(actor, PERM_RECOVERY_ADMIN)
    ):
        messages.error(request, "Access restricted to authorized management roles.")
        return redirect("attendance:panel")

    issued_token = None
    issued_for = None
    issued_type = None
    error = None

    if request.method == "POST":
        # Enforce recent re-authentication / Sudo mode before issuing recovery codes
        if not is_sudo_active(request):
            return redirect("/auth/security/sudo/?next=/auth/recovery/console/")

        target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
        approver_emp_id = request.POST.get("approver_employee_id", "").strip().upper()
        recovery_type = request.POST.get("recovery_type", "password_reset")
        reason = request.POST.get("reason", "").strip()

        target_user = CustomUser.objects.filter(employee_id=target_emp_id, is_active=True).first()
        approving_admin = None
        if approver_emp_id:
            approving_admin = CustomUser.objects.filter(
                employee_id=approver_emp_id, is_active=True
            ).first()

        if not target_user:
            error = f"Employee ID '{target_emp_id}' was not found or is inactive."
        elif not reason:
            error = "A valid business verification reason is required to initiate account recovery."
        else:
            try:
                if recovery_type == "mfa_reset":
                    record, raw_code = issue_mfa_reset_code(
                        target_user=target_user,
                        issuing_admin=actor,
                        approving_admin=approving_admin,
                    )
                    issued_token = raw_code
                    issued_for = target_user
                    issued_type = "MFA Reset"
                    messages.success(
                        request,
                        f"One-time MFA reset token generated for {target_user.employee_id}. "
                        "Provide this token securely to the employee to reconfigure authenticator.",
                    )
                else:
                    record, raw_code = issue_administrative_recovery_token(
                        actor=actor,
                        target_user=target_user,
                        approving_admin=approving_admin,
                        reason=reason,
                    )
                    issued_token = raw_code
                    issued_for = target_user
                    issued_type = "Password Reset"
                    messages.success(
                        request,
                        f"One-time recovery passcode generated for {target_user.employee_id}. "
                        "Provide this passcode securely to the employee.",
                    )
            except (PermissionDenied, ValidationError) as e:
                error = str(e)
            except Exception as e:
                error = f"Failed to issue recovery token: {e}"

    # Query manageable accounts based on actor scope
    manageable_users = []
    if actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        manageable_users = CustomUser.objects.exclude(id=actor.id).order_by("employee_id")[:50]
    elif actor.role == CustomUser.Role.HR:
        manageable_users = CustomUser.objects.filter(
            role__in=[CustomUser.Role.EMPLOYEE, CustomUser.Role.INTERN]
        ).order_by("employee_id")[:50]
    elif actor.role == CustomUser.Role.MANAGER:
        from apps.directory.models import ReportingRelationship, Team

        subordinate_ids = ReportingRelationship.objects.filter(manager=actor).values_list(
            "subordinate_id", flat=True
        )
        team_member_ids = Team.objects.filter(lead=actor).values_list(
            "memberships__user_id", flat=True
        )
        allowed_ids = set(subordinate_ids).union(set(team_member_ids))
        manageable_users = CustomUser.objects.filter(id__in=allowed_ids).order_by("employee_id")

    return render(
        request,
        "accounts/recovery/admin_console.html",
        {
            "manageable_users": manageable_users,
            "issued_token": issued_token,
            "issued_for": issued_for,
            "issued_type": issued_type,
            "error": error,
            "actor": actor,
        },
    )
