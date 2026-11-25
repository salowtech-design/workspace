"""Operator command: invite an existing Workspace person as a pending IAM admin.

Prints a single-use setup link (10 minutes) once. Nothing else is stored in clear."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.auth.iam_setup import SETUP_TOKEN_MINUTES, invite_iam_admin


class Command(BaseCommand):
    help = "Invite a Workspace person as a pending IAM admin and print the one-time setup link."

    def add_arguments(self, parser):
        parser.add_argument("--employee-id", required=True)
        parser.add_argument("--admin-id", required=True)
        parser.add_argument("--role", default="admin")
        parser.add_argument("--base-url", default="https://iam.salowtech.com")

    def handle(self, *args, **opts):
        try:
            admin, raw = invite_iam_admin(opts["employee_id"], opts["admin_id"], opts["role"])
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from None
        base = opts["base_url"].rstrip("/")
        self.stdout.write(f"IAM admin {admin.admin_id} is pending.")
        self.stdout.write(
            f"Setup link (the part after # never reaches server logs; single use, {SETUP_TOKEN_MINUTES} minutes):"
        )
        self.stdout.write(f"{base}/setup/#{raw}")
