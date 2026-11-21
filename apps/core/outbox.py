"""
Transactional Outbox Event Publisher and Processor.
Provides reliable at-least-once domain event dispatching without Postgres-specific locking.
"""

import uuid
from typing import Callable, Optional

from django.db import connection, transaction
from django.utils import timezone

from apps.core.models import OutboxEvent

EVENT_HANDLERS: dict[str, list[Callable[[dict], None]]] = {}
MAX_RETRIES = 3


def register_handler(event_type: str, handler: Callable[[dict], None]) -> None:
    """Register an event handler for a domain event type."""
    if event_type not in EVENT_HANDLERS:
        EVENT_HANDLERS[event_type] = []
    EVENT_HANDLERS[event_type].append(handler)


def publish_event(event_type: str, payload: dict, event_id: Optional[str] = None) -> OutboxEvent:
    """
    Publish a domain event to the outbox table.
    Should be called inside an atomic transaction alongside business mutations.
    Accepts optional event_id for publisher idempotency.
    """
    eid = event_id or str(uuid.uuid4())
    existing = OutboxEvent.objects.filter(event_id=eid).first()
    if existing:
        return existing
    return OutboxEvent.objects.create(
        event_id=eid,
        event_type=event_type,
        payload=payload,
        status=OutboxEvent.Status.PENDING,
    )


def process_outbox_batch(batch_size: int = 50) -> tuple[int, int]:
    """
    Process pending outbox events with atomic row claiming.
    Guarantees concurrency safety across multiple workers using skip_locked row locks.
    Returns (success_count, failure_count).
    """
    success_count = 0
    failure_count = 0

    with transaction.atomic():
        can_skip = getattr(connection.features, "has_select_for_update_skip_locked", False)
        qs = OutboxEvent.objects.select_for_update(skip_locked=True) if can_skip else OutboxEvent.objects.select_for_update()
        events = list(
            qs.filter(status=OutboxEvent.Status.PENDING)
            .order_by("created_at")[:batch_size]
        )
        if not events:
            return 0, 0

        for event in events:
            handlers = EVENT_HANDLERS.get(event.event_type, [])
            failed = False
            error_msg = ""

            for handler in handlers:
                try:
                    handler(event.payload)
                except Exception as exc:  # noqa: BLE001
                    failed = True
                    error_msg = str(exc)
                    break

            if failed:
                failure_count += 1
                event.retry_count += 1
                event.last_error = error_msg
                if event.retry_count >= MAX_RETRIES:
                    event.status = OutboxEvent.Status.FAILED
                event.save(update_fields=["retry_count", "last_error", "status"])
            else:
                success_count += 1
                event.status = OutboxEvent.Status.PROCESSED
                event.processed_at = timezone.now()
                event.save(update_fields=["status", "processed_at"])

    return success_count, failure_count
