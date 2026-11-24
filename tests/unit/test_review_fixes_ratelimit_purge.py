"""Rate limit tables must be purgeable without removing live blocks."""
import time
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.core.models import RateLimitBucket, RateLimitRecord


@pytest.mark.django_db
def test_purge_removes_old_rows_and_keeps_recent_and_future_rows():
    now = time.time()
    RateLimitRecord.objects.create(key="old", timestamp=now - 7200)
    RateLimitRecord.objects.create(key="recent", timestamp=now - 30)
    RateLimitRecord.objects.create(key="blocked-ip:sensitive", timestamp=now + 1800)
    RateLimitBucket.objects.create(bucket_key="old-b", count=3, reset_at=timezone.now() - timedelta(hours=3))
    RateLimitBucket.objects.create(bucket_key="live-b", count=1, reset_at=timezone.now() + timedelta(minutes=1))

    out = StringIO()
    call_command("purge_ratelimit_records", stdout=out)

    assert set(RateLimitRecord.objects.values_list("key", flat=True)) == {"recent", "blocked-ip:sensitive"}
    assert set(RateLimitBucket.objects.values_list("bucket_key", flat=True)) == {"live-b"}
    assert "Purged 1 rate limit records and 1 expired buckets" in out.getvalue()


@pytest.mark.django_db
def test_purge_retention_has_a_floor_of_one_minute():
    RateLimitRecord.objects.create(key="just-now", timestamp=time.time() - 10)
    call_command("purge_ratelimit_records", "--retention-seconds", "0", stdout=StringIO())
    assert RateLimitRecord.objects.filter(key="just-now").exists()
