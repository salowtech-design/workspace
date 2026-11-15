"""
Bootstrap Administrator Logic.
Creates initial Super Admin from hidden terminal prompt without displaying or logging passwords.
"""

from django.db import transaction

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.passwords import validate_employee_password
from apps.accounts.models import CustomUser


def create_bootstrap_admin(
    employee_id: str,
    full_name: str,
    raw_password: str,
    email: str = None,
) -> CustomUser:
    """
    Create first Super Admin without MFA.
    Enforces password complexity, initializes status to ACTIVE, and logs audit event.
    """
    normalized_id = employee_id.strip().upper()

    temp_user = CustomUser(employee_id=normalized_id, full_name=full_name.strip())
    validate_employee_password(raw_password, temp_user)

    with transaction.atomic():
        user = CustomUser.objects.create_superuser(
            employee_id=normalized_id,
            full_name=full_name.strip(),
            password=raw_password,
            email=email.strip() if email else None,
        )
        user.status = CustomUser.Status.ACTIVE
        user.save(update_fields=["status"])

        log_auth_event(
            event="bootstrap_admin_created",
            code="INFO",
            step="bootstrap",
            employee_id=user.employee_id,
            message="Bootstrap Super Admin created via terminal command",
        )

    return user
