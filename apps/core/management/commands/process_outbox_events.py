from django.core.management.base import BaseCommand

from apps.core.outbox import process_outbox_batch


class Command(BaseCommand):
    help = "Processes pending transactional domain events from core_outboxevent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of pending events to process per run (default: 50).",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        success, failed = process_outbox_batch(batch_size=batch_size)
        self.stdout.write(
            self.style.SUCCESS(
                f"Outbox processing batch complete: {success} succeeded, {failed} failed."
            )
        )
