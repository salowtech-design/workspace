"""Background jobs on the existing transactional outbox (same worker, same retries).

Semantics every job handler must respect:
- At-least-once delivery. A handler can run more than once for the same job (retry after a
  crash or a failure), so it must be idempotent. Use enqueue_job(job_id=...) to avoid
  duplicate enqueues; that does not make the handler itself safe to repeat.
- Payloads carry ids, never file or message content, and never a decision that was true at
  enqueue time. Handlers re-check permission and current state when they run
  (can(user, ...) against the live object), because access can change while a job waits.
- One handler per job kind; registering a second, different handler is an error.
"""

from typing import Callable, Optional

from apps.core import outbox
from apps.core.outbox import process_outbox_batch, publish_event

PREFIX = "job."


def register_job(kind: str, handler: Callable[[dict], None]) -> None:
    existing = outbox.EVENT_HANDLERS.get(PREFIX + kind) or []
    if existing:
        if handler in existing:
            return
        raise ValueError(f"job {kind!r} already has a handler")
    outbox.register_handler(PREFIX + kind, handler)


def enqueue_job(kind: str, payload: Optional[dict] = None, *, job_id: Optional[str] = None):
    """Call inside the transaction that makes the work necessary."""
    return publish_event(PREFIX + kind, payload or {}, event_id=job_id)


def run_jobs(batch_size: int = 50) -> tuple[int, int]:
    return process_outbox_batch(batch_size)
