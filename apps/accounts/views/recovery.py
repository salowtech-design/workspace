"""
Account Recovery & Password Reset Views.
Implements:
1. Public timing-safe forgot password entry point (anti-enumeration).
2. Self-service password reset using single-use backup recovery codes.
3. Administrative & delegated HR/Manager recovery console.
4. Token-based password reset completion.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.reset import (
    complete_password_reset_with_token,
    issue_administrative_recovery_token,
    self_service_reset_password,
)
from apps.accounts.models import CustomUser

RECOVERY_PENDING_UID_KEY = "_salow_rec_pending_uid"
RECOVERY_METHOD_KEY = "_salow_rec_method"


def forgot_password_view(request):
    """
    Public forgot-password entry point.
    Prevents account enumeration by displaying identical success/confirmation instructions
    regardless of whether the employee ID exists.
    Supports recovery via backup recovery code or administrative reset token.
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

            if new_password != confirm_password:
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


@login_required
def admin_recovery_console_view(request):  # noqa: C901
    """
    Dedicated recovery management console for Super Admins, Admins, HR, and Managers.
    Enforces server-side authority scope:
    - Super Admin: all accounts (Super Admin target requires second Super Admin approval).
    - Admin: Admin, HR, Manager, Employee, Intern (Admin target requires second Admin approval).
    - HR: Employee, Intern.
    - Manager: Employee, Intern within direct reporting or led team.
    """
    actor: CustomUser = request.user
    if actor.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.HR,
        CustomUser.Role.MANAGER,
    ):
        messages.error(request, "Access restricted to authorized management roles.")
        return redirect("attendance:panel")

    issued_token = None
    issued_for = None
    error = None

    if request.method == "POST":
        target_emp_id = request.POST.get("target_employee_id", "").strip().upper()
        approver_emp_id = request.POST.get("approver_employee_id", "").strip().upper()
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
                record, raw_code = issue_administrative_recovery_token(
                    actor=actor,
                    target_user=target_user,
                    approving_admin=approving_admin,
                    reason=reason,
                )
                issued_token = raw_code
                issued_for = target_user
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
            "error": error,
            "actor": actor,
        },
    )
