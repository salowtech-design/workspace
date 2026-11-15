"""
Employee Password Security Policy & Management.
Enforces 12-character minimum, common password rejection, and ID/name exclusion.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.accounts.auth.errors import AuthException
from apps.accounts.models import CustomUser

COMMON_PROHIBITED = {
    "password1234",
    "salowtech1234",
    "welcome12345",
    "workspace2026",
    "administrator",
}


def validate_employee_password(password: str, user: CustomUser) -> None:
    """Validate password against strict company security rules."""
    clean_pass = password.strip()

    if len(clean_pass) < 12:
        raise AuthException(
            "AUTH-PWD-001",
            step="password_validation",
            employee_id=user.employee_id,
            message_override="Password must be at least 12 characters in length.",
        )

    if clean_pass.lower() in COMMON_PROHIBITED:
        raise AuthException(
            "AUTH-PWD-001",
            step="password_validation",
            employee_id=user.employee_id,
            message_override="Password is too common and easily guessed.",
        )

    # Reject passwords containing employee ID or parts of full name
    lower_pass = clean_pass.lower()
    if user.employee_id and user.employee_id.lower() in lower_pass:
        raise AuthException(
            "AUTH-PWD-001",
            step="password_validation",
            employee_id=user.employee_id,
            message_override="Password must not contain your employee ID.",
        )

    for part in user.full_name.split():
        if len(part) >= 3 and part.lower() in lower_pass:
            raise AuthException(
                "AUTH-PWD-001",
                step="password_validation",
                employee_id=user.employee_id,
                message_override="Password must not contain parts of your name.",
            )

    try:
        validate_password(clean_pass, user)
    except ValidationError as e:
        msg = e.messages[0] if hasattr(e, "messages") else str(e)
        raise AuthException(
            "AUTH-PWD-001",
            step="password_validation",
            employee_id=user.employee_id,
            message_override=msg,
        ) from e


def set_user_password(user: CustomUser, raw_password: str) -> None:
    """Validate and set user's password using Argon2id."""
    validate_employee_password(raw_password, user)
    user.set_password(raw_password)
    user.save(update_fields=["password", "updated_at"])
