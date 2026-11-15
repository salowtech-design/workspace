"""
Standardized Authentication Error Catalogue & Exception Handling.
Every error code maps to a friendly user sentence and an immutable audit log.
Users receive a plain sentence and a short reference ID, never internal codes.
"""

import secrets
from typing import Optional

from django.core.exceptions import ValidationError


class AuthErrorDefinition:
    def __init__(self, code: str, user_message: str):
        self.code = code
        self.user_message = user_message


CATALOGUE: dict[str, AuthErrorDefinition] = {
    "AUTH-ACT-001": AuthErrorDefinition("AUTH-ACT-001", "Invalid or expired activation code."),
    "AUTH-ACT-002": AuthErrorDefinition(
        "AUTH-ACT-002",
        "Activation code locked after five incorrect attempts. Contact an administrator.",
    ),
    "AUTH-ACT-003": AuthErrorDefinition(
        "AUTH-ACT-003",
        "Activation session expired. Please restart activation with your administrator.",
    ),
    "AUTH-PWD-001": AuthErrorDefinition(
        "AUTH-PWD-001", "Password does not meet company security policy."
    ),
    "AUTH-MFA-001": AuthErrorDefinition(
        "AUTH-MFA-001", "Authenticator credential could not be initialized."
    ),
    "AUTH-MFA-002": AuthErrorDefinition("AUTH-MFA-002", "Authenticator code is incorrect."),
    "AUTH-MFA-003": AuthErrorDefinition(
        "AUTH-MFA-003",
        "Authenticator code has already been used. Please wait for the next 30-second code.",
    ),
    "AUTH-MFA-004": AuthErrorDefinition(
        "AUTH-MFA-004",
        "Authenticator code clock drift detected. Please verify your device's date and time.",
    ),
    "AUTH-MFA-005": AuthErrorDefinition(
        "AUTH-MFA-005", "Failed to render authenticator setup QR code."
    ),
    "AUTH-MFA-006": AuthErrorDefinition(
        "AUTH-MFA-006",
        "Multi-Factor Authentication enrollment is mandatory to access this workspace.",
    ),
    "AUTH-LOG-001": AuthErrorDefinition("AUTH-LOG-001", "Invalid employee ID or password."),
    "AUTH-LOG-002": AuthErrorDefinition(
        "AUTH-LOG-002",
        "Account temporarily locked due to consecutive failed attempts. Try again in 15 minutes.",
    ),
    "AUTH-LOG-003": AuthErrorDefinition(
        "AUTH-LOG-003", "Account is deactivated or expired. Contact Human Resources."
    ),
    "AUTH-SES-001": AuthErrorDefinition(
        "AUTH-SES-001", "Your session has expired or was revoked. Please sign in again."
    ),
    "AUTH-STP-001": AuthErrorDefinition(
        "AUTH-STP-001", "Step-up authentication required for sensitive administrative operations."
    ),
    "AUTH-DB-001": AuthErrorDefinition(
        "AUTH-DB-001", "A database error occurred during authentication processing."
    ),
}


def generate_reference_id() -> str:
    """Generate human-readable 8-char reference identifier (e.g. REF-7A3B9C)."""
    return f"REF-{secrets.token_hex(3).upper()}"


class AuthException(ValidationError):
    """Domain exception carrying error code, friendly message, and reference ID."""

    def __init__(
        self,
        code: str,
        step: str,
        employee_id: str = "",
        message_override: Optional[str] = None,
        exception: Optional[Exception] = None,
    ):
        definition = CATALOGUE.get(
            code, AuthErrorDefinition(code, "An authentication error occurred.")
        )
        self.code = code
        self.step = step
        self.employee_id = employee_id
        self.reference_id = generate_reference_id()
        self.user_message = message_override or definition.user_message
        self.original_exception = exception
        super().__init__(f"{self.user_message} (Reference: {self.reference_id})", code=code)
        self.code = code
