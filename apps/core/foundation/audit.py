from typing import Any, Optional

from apps.core.services import record_audit_event


def audit(actor: Any, action: str, resource_type: str, resource_id: Any, **details: Optional[Any]):
    """One audit row per action. Pass ids and short facts, never message or file content."""
    return record_audit_event(
        action=action,
        target_type=resource_type,
        target_id=str(resource_id),
        actor=actor,
        payload_summary={k: v for k, v in details.items() if v is not None},
    )
