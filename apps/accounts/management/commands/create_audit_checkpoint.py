from django.core.management.base import BaseCommand
from apps.accounts.auth.audit_chain import create_audit_checkpoint


class Command(BaseCommand):
    help = "Generates a signed cryptographic audit checkpoint for newly recorded AuthEvents."

    def handle(self, *args, **options):
        cp = create_audit_checkpoint()
        if cp:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully created audit checkpoint [{cp.checkpoint_id}] covering {cp.event_count} events (Hash: {cp.cumulative_hash[:16]}...)"
                )
            )
        else:
            self.stdout.write("No new uncheckpointed audit events found.")
