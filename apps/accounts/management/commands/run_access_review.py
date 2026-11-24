from django.core.management.base import BaseCommand

from apps.accounts.auth.access_reviews import generate_quarterly_campaign


class Command(BaseCommand):
    help = "Generates or updates the quarterly access review campaign, detecting dormant and exited accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--quarter",
            type=str,
            default=None,
            help="Quarter code (e.g. 2026-Q1). Defaults to current calendar quarter.",
        )

    def handle(self, *args, **options):
        quarter = options["quarter"]
        campaign, total, dormant = generate_quarterly_campaign(quarter_name=quarter)
        self.stdout.write(
            self.style.SUCCESS(
                f"Access Review Campaign [{campaign.quarter}] populated: {total} total accounts, {dormant} flagged as dormant/exited."
            )
        )
