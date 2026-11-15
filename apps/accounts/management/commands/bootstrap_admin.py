import getpass

from django.core.management.base import BaseCommand

from apps.accounts.auth.bootstrap import create_bootstrap_admin
from apps.accounts.auth.errors import AuthException


class Command(BaseCommand):
    help = (
        "Interactively bootstraps the first Super Admin through a hidden terminal password prompt."
    )

    def handle(self, *args, **options):
        self.stdout.write("=" * 65)
        self.stdout.write("SALOW WORKSPACE — BOOTSTRAP INITIAL SUPER ADMIN")
        self.stdout.write("=" * 65)

        employee_id = input("Enter Employee ID (e.g. ST-EMP-0001): ").strip()
        if not employee_id:
            self.stdout.write(self.style.ERROR("Employee ID cannot be empty."))
            return

        full_name = input("Enter Full Name: ").strip()
        if not full_name:
            self.stdout.write(self.style.ERROR("Full Name cannot be empty."))
            return

        email = input("Enter Email Address (optional): ").strip() or None

        self.stdout.write("Enter permanent password (input is hidden):")
        pass1 = getpass.getpass("Password: ")
        pass2 = getpass.getpass("Confirm Password: ")

        if pass1 != pass2:
            self.stdout.write(self.style.ERROR("Passwords do not match."))
            return

        try:
            admin_user = create_bootstrap_admin(
                employee_id=employee_id,
                full_name=full_name,
                raw_password=pass1,
                email=email,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n[SUCCESS] Super Admin account [{admin_user.employee_id}] created.\n"
                    "Next step: Sign in via browser and complete initial authenticator enrollment."
                )
            )
        except AuthException as e:
            self.stdout.write(self.style.ERROR(f"Error: {e.user_message}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error creating account: {e}"))
