from django.core.management.base import BaseCommand

from apps.accounts.auth.diagnostics import run_auth_doctor_checks


class Command(BaseCommand):
    help = "Runs complete 12-point automated authentication, cryptography, and security self-test."

    def handle(self, *args, **options):
        self.stdout.write("=" * 68)
        self.stdout.write("SALOW WORKSPACE — AUTH DOCTOR SYSTEM DIAGNOSTICS")
        self.stdout.write("=" * 68)

        results = run_auth_doctor_checks()
        all_passed = True

        for res in results:
            if res.passed:
                status_str = self.style.SUCCESS("[PASS]")
            else:
                status_str = self.style.ERROR("[FAIL]")
                all_passed = False

            self.stdout.write(f"{status_str} {res.name:<30} : {res.details}")
            if not res.passed and res.remediation:
                self.stdout.write(self.style.WARNING(f"       -> Remediation: {res.remediation}"))

        self.stdout.write("=" * 68)
        if all_passed:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[OK] All {len(results)} authentication subsystems are healthy."
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR("[WARN] One or more authentication subsystems reported errors.")
            )
            raise SystemExit(1)
