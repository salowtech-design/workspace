"""Delete expired rate limit rows so the tables do not grow without limit.

Safe for production. Schedule it (cron or a timer), for example every 15 minutes.
Rows with a timestamp in the future (IP blocks written by spraying detection) are kept
until they expire.
"""

import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import RateLimitBucket, RateLimitRecord


def purge_expired_rate_limits(retention_seconds: int = 3600) -> dict:
    """Remove records and buckets that expired more than retention_seconds ago."""
    cutoff_ts = time.time() - retention_seconds
    cutoff_dt = timezone.now() - timedelta(seconds=retention_seconds)
    records, _ = RateLimitRecord.objects.filter(timestamp__lt=cutoff_ts).delete()
    buckets, _ = RateLimitBucket.objects.filter(reset_at__lt=cutoff_dt).delete()
    return {"records": records, "buckets": buckets}


class Command(BaseCommand):
    help = "Delete expired RateLimitRecord and RateLimitBucket rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-seconds",
            type=int,
            default=3600,
            help="Keep rows that expired less than this many seconds ago (default 3600).",
        )

    def handle(self, *args, **options):
        retention = max(int(options["retention_seconds"]), 60)
        result = purge_expired_rate_limits(retention)
        self.stdout.write(
            f"Purged {result['records']} rate limit records and {result['buckets']} expired buckets."
        )
