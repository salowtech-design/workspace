import pytest

from apps.core.models import OutboxEvent
from apps.core.outbox import (
    MAX_RETRIES,
    process_outbox_batch,
    publish_event,
    register_handler,
)


@pytest.mark.django_db
def test_outbox_publish_and_process_success():
    """Verify outbox event creation, handler dispatch, and state transition to processed."""
    dispatched_payloads = []

    def mock_user_created_handler(payload):
        dispatched_payloads.append(payload)

    event_type = "unit.test.event.success"
    register_handler(event_type, mock_user_created_handler)

    event = publish_event(event_type, {"user_id": "EMP999", "role": "intern"})
    assert event.status == OutboxEvent.Status.PENDING

    success_count, failure_count = process_outbox_batch(batch_size=10)
    assert success_count >= 1
    assert failure_count == 0

    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.PROCESSED
    assert event.processed_at is not None
    assert len(dispatched_payloads) == 1
    assert dispatched_payloads[0]["user_id"] == "EMP999"


@pytest.mark.django_db
def test_outbox_retry_and_dead_letter_queue():
    """Verify failing handlers increment retry count and transition to FAILED after max retries."""

    def failing_handler(payload):
        raise RuntimeError("Simulated transient infrastructure fault")

    event_type = "unit.test.event.failure"
    register_handler(event_type, failing_handler)

    event = publish_event(event_type, {"data": "critical_event"})

    for attempt in range(1, MAX_RETRIES + 1):
        process_outbox_batch(batch_size=10)
        event.refresh_from_db()
        assert event.retry_count == attempt
        assert "Simulated transient infrastructure fault" in event.last_error
        if attempt < MAX_RETRIES:
            assert event.status == OutboxEvent.Status.PENDING
        else:
            assert event.status == OutboxEvent.Status.FAILED
