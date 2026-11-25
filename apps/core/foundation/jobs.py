"""Background jobs on the existing transactional outbox (same worker, same retries)."""

from typing import Callable, Optional

from apps.core.outbox import process_outbox_batch, publish_event, register_handler

PREFIX = "job."


def register_job(kind: str, handler: Callable[[dict], None]) -> None:
    register_handler(PREFIX + kind, handler)


def enqueue_job(kind: str, payload: Optional[dict] = None, *, job_id: Optional[str] = None):
    """Call inside the transaction that makes the work necessary."""
    return publish_event(PREFIX + kind, payload or {}, event_id=job_id)


def run_jobs(batch_size: int = 50) -> tuple[int, int]:
    return process_outbox_batch(batch_size)
