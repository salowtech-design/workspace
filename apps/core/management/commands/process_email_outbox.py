"""
Management command to process transactional email outbox records.
Usage: python manage.py process_email_outbox [--batch-size 50]
"""

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

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        success, failed = process_email_outbox(batch_size=batch_size)
        self.stdout.write(
            self.style.SUCCESS(
                f"Outbox processing completed: {success} sent, {failed} failed/retried."
            )
        )
