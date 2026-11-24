from django.core.management.base import BaseCommand

from apps.accounts.auth.alert_escalation import escalate_unacknowledged_alerts


class Command(BaseCommand):
    help = "Escalates unacknowledged security alerts older than 15 minutes to CRITICAL."

    def handle(self, *args, **options):
        escalated = escalate_unacknowledged_alerts()
        count = len(escalated)
        if count > 0:
            self.stdout.write(
                self.style.WARNING(f"Escalated {count} security alert(s) to CRITICAL.")
            )
        else:
            self.stdout.write("No open security alerts exceeded the 15-minute SLA.")
