from typing import Any, Dict, Optional

from apps.core.models import AuditLog

# Keys that must be stripped/redacted from audit payloads
SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "otp",
    "totp",
    "cookie",
    "frame",
    "embedding",
    "raw_image",
    "ciphertext",
}


def redact_payload(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Recursively redacts secrets and sensitive biometric parameters."""
    if not data or not isinstance(data, dict):
        return {}

    cleaned = {}
    for key, value in data.items():
        if any(sensitive in key.lower() for sensitive in SENSITIVE_KEYS):
            cleaned[key] = "[REDACTED]"
        elif isinstance(value, dict):
            cleaned[key] = redact_payload(value)
        else:
            cleaned[key] = value
    return cleaned


def record_audit_event(
    action: str = "",
    target_type: str = "",
    target_id: str = "",
    actor: Optional[Any] = None,
    payload_summary: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    *,
    event_type: str = "",
    target_entity: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """
    Sole authorized cross-module write interface for appending security audit records.
    Supports action/target_type/payload_summary and event_type/target_entity/details aliases.
    """
    effective_action = action or event_type
    effective_target_type = target_type or target_entity
    effective_summary = payload_summary or details or {}

    safe_summary = redact_payload(effective_summary)

    # Ensure actor is an authenticated user model instance or None
    user_instance = None
    if actor and actor.is_authenticated:
        user_instance = actor

    return AuditLog.objects.create(
        actor=user_instance,
        action=effective_action,
        target_type=effective_target_type,
        target_id=str(target_id),
        payload_summary=safe_summary,
        request_id=request_id,
    )
