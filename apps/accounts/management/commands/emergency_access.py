"""
Management command for Independent Two-Person Emergency Access.
Usage:
    python manage.py emergency_access \
        --c1-id IAM-SUPER-01 --c1-pass <pass> --c1-totp 123456 \
        --c2-id IAM-SUPER-02 --c2-pass <pass> --c2-totp 654321 \
        --incident INC-2026-001 \
        --scope incident_investigation \
        --reason "Production database outage investigation and restore validation" \
        --duration 15
"""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.auth.emergency_access import (
    activate_independent_emergency_access,
    revoke_emergency_access,
)


class Command(BaseCommand):
    help = "Executes two-person independent emergency break-glass procedure."

    def add_arguments(self, parser):
        parser.add_argument("--c1-id", required=True, help="Custodian 1 Super Admin ID")
        parser.add_argument("--c1-pass", required=True, help="Custodian 1 Password")
        parser.add_argument("--c1-totp", required=True, help="Custodian 1 fresh TOTP code")
        parser.add_argument("--c2-id", required=True, help="Custodian 2 Super Admin ID")
        parser.add_argument("--c2-pass", required=True, help="Custodian 2 Password")
        parser.add_argument("--c2-totp", required=True, help="Custodian 2 fresh TOTP code")
        parser.add_argument(
            "--incident", required=True, help="Incident ticket ID (e.g. INC-2026-001)"
        )
        parser.add_argument(
            "--scope",
            default="incident_investigation",
            choices=[
                "incident_investigation",
                "cluster_recovery",
                "credential_remediation",
                "audit_forensics",
            ],
            help="Authorized bounded emergency scope",
        )
        parser.add_argument("--reason", required=True, help="Detailed justification (min 20 chars)")
        parser.add_argument("--duration", type=int, default=15, help="Ceiling in minutes (max 15)")
        parser.add_argument(
            "--revoke", type=str, default="", help="Revoke active emergency session ID"
        )

    def handle(self, *args, **options):
        revoke_id = options.get("revoke")
        if revoke_id:
            ok = revoke_emergency_access(revoke_id, revoked_by=options.get("c1_id", "OPERATIONS"))
            if ok:
                self.stdout.write(
                    self.style.SUCCESS(f"Emergency session [{revoke_id}] successfully revoked.")
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"Emergency session [{revoke_id}] not found or already inactive."
                    )
                )
            return

        try:
            token, meta = activate_independent_emergency_access(
                custodian_1_id=options["c1_id"],
                custodian_1_password=options["c1_pass"],
                custodian_1_totp=options["c1_totp"],
                custodian_2_id=options["c2_id"],
                custodian_2_password=options["c2_pass"],
                custodian_2_totp=options["c2_totp"],
                incident_id=options["incident"],
                scope=options["scope"],
                reason=options["reason"],
                duration_minutes=options["duration"],
            )
            self.stdout.write(self.style.SUCCESS("=" * 65))
            self.stdout.write(self.style.SUCCESS("TWO-PERSON EMERGENCY ACCESS ACTIVATED"))
            self.stdout.write(self.style.SUCCESS("=" * 65))
            self.stdout.write(f"Incident ID:    {meta['incident_id']}")
            self.stdout.write(f"Scope:          {meta['scope']}")
            self.stdout.write(f"Custodian 1:    {meta['custodian_1']}")
            self.stdout.write(f"Custodian 2:    {meta['custodian_2']}")
            self.stdout.write(
                f"Expires At:     {meta['expires_at']} ({meta['duration_minutes']} min)"
            )
            self.stdout.write(f"Session Token:  {token}")
            self.stdout.write(self.style.WARNING("-" * 65))
            self.stdout.write(
                "NOTICE: CRITICAL SecurityAlert logged. Mandatory post-use key/credential rotation drill required."
            )
            self.stdout.write("=" * 65)
        except Exception as e:
            raise CommandError(f"Emergency access failed: {e}")
