"""
Management command to process transactional email outbox records.
Usage: python manage.py process_email_outbox [--batch-size 50]
"""

import time
from django.core.management.base import BaseCommand

from apps.core.email_service import process_email_outbox


class Command(BaseCommand):
    help = "Dispatches queued IAM notification emails through the transactional outbox."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Maximum number of outbox messages to dispatch per invocation.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Run continuously in a polling daemon loop.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Polling interval in seconds when running in loop mode.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        loop_mode = options["loop"]
        interval = options["interval"]

        if not loop_mode:
            success, failed = process_email_outbox(batch_size=batch_size)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Outbox processing completed: {success} sent, {failed} failed/retried."
                )
            )
            return

        self.stdout.write(
            self.style.NOTICE(
                f"Starting transactional email outbox worker daemon (interval: {interval}s)..."
            )
        )
        try:
            while True:
                success, failed = process_email_outbox(batch_size=batch_size)
                if success > 0 or failed > 0:
                    self.stdout.write(f"Processed batch: {success} sent, {failed} failed/retried.")
                time.sleep(interval)
        except (KeyboardInterrupt, SystemExit):
            self.stdout.write(self.style.WARNING("Email outbox worker stopped gracefully."))
