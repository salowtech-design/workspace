from django.core.management.base import BaseCommand, CommandError

from apps.accounts.auth.audit_chain import verify_audit_chain


class Command(BaseCommand):
    help = (
        "Verifies cryptographic hash chaining and digital signatures across all audit checkpoints."
    )

    def handle(self, *args, **options):
        success, message = verify_audit_chain()
        if success:
            self.stdout.write(self.style.SUCCESS(f"Audit chain verification PASSED: {message}"))
        else:
            self.stderr.write(self.style.ERROR(f"Audit chain verification FAILED: {message}"))
            raise CommandError(f"Audit tamper detected: {message}")
