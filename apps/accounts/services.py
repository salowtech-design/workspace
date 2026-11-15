from django.contrib.auth import login, logout

from apps.core.services import record_audit_event


def login_user(request, user) -> None:
    """
    Session-safe login handler.
    Rotates session key to prevent session fixation and appends an audit event.
    """
    login(request, user)
    request.session.cycle_key()

    record_audit_event(
        action="accounts.login.success",
        target_type="user",
        target_id=user.employee_id,
        actor=user,
        payload_summary={
            "role": user.role,
            "ip_address": request.META.get("REMOTE_ADDR", ""),
        },
    )


def logout_user(request) -> None:
    """
    POST-only logout handler.
    Invalidates server-side session and records audit event.
    """
    user = request.user
    if user.is_authenticated:
        record_audit_event(
            action="accounts.logout.success",
            target_type="user",
            target_id=user.employee_id,
            actor=user,
        )
    logout(request)
    request.session.flush()
