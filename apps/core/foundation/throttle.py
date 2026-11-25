"""Per-person fixed-window limiter on the shared RateLimitBucket table (row-locked, so it holds
across workers). Fails closed: if a slot cannot be taken, the action is refused."""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import RateLimitBucket


def throttled(user, prefix, limit, window_seconds=60):
    """Count one action for this person under `prefix`. True means over the limit (refuse)."""
    key = f"{prefix}:{user.pk}"
    now = timezone.now()
    for _ in range(2):
        with transaction.atomic():
            bucket = RateLimitBucket.objects.select_for_update().filter(bucket_key=key).first()
            if bucket is None:
                try:
                    with transaction.atomic():
                        RateLimitBucket.objects.create(
                            bucket_key=key,
                            count=1,
                            reset_at=now + timedelta(seconds=window_seconds),
                        )
                    return False
                except IntegrityError:
                    continue
            if now >= bucket.reset_at:
                bucket.count = 1
                bucket.reset_at = now + timedelta(seconds=window_seconds)
            else:
                bucket.count += 1
            bucket.save(update_fields=["count", "reset_at", "updated_at"])
            return bucket.count > limit
    return True
