import os

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import CustomUser
from apps.core.services import record_audit_event


class Command(BaseCommand):
    help = "Seeds initial superuser and demo accounts for Salow Workspace."

    def add_arguments(self, parser):
        parser.add_argument(
            "--admin-id",
            type=str,
            default=os.getenv("ADMIN_EMPLOYEE_ID", "EMP001"),
            help="Employee ID for Super Admin",
        )
        parser.add_argument(
            "--admin-name",
            type=str,
            default=os.getenv("ADMIN_NAME", "Salow Workspace Administrator"),
            help="Full Name for Super Admin",
        )
        parser.add_argument(
            "--admin-email",
            type=str,
            default=os.getenv("ADMIN_EMAIL", "admin@salowtech.local"),
            help="Email for Super Admin",
        )
        parser.add_argument(
            "--admin-password",
            type=str,
            default=os.getenv("ADMIN_PASSWORD", "SalowAdmin2026!Secure"),
            help="Initial Password for Super Admin",
        )
        parser.add_argument(
            "--demo-id",
            type=str,
            default="EMP101",
            help="Employee ID for Demo Staff Account",
        )
        parser.add_argument(
            "--demo-name",
            type=str,
            default="Demo Employee",
            help="Full Name for Demo Staff Account",
        )
        parser.add_argument(
            "--demo-password",
            type=str,
            default="DemoEmp2026!Secure",
            help="Initial Password for Demo Staff Account",
        )

    def handle(self, *args, **options):
        admin_id = options["admin_id"].strip().upper()
        admin_name = options["admin_name"].strip()
        admin_email = options["admin_email"].strip()
        admin_password = options["admin_password"]

        demo_id = options["demo_id"].strip().upper()
        demo_name = options["demo_name"].strip()
        demo_password = options["demo_password"]

        with transaction.atomic():
            # 1. Super Admin
            admin_user = CustomUser.objects.filter(employee_id=admin_id).first()
            if not admin_user:
                admin_user = CustomUser.objects.create_superuser(
                    employee_id=admin_id,
                    full_name=admin_name,
                    email=admin_email,
                    password=admin_password,
                )
                record_audit_event(
                    event_type="auth.user_created",
                    actor=admin_user,
                    target_entity="CustomUser",
                    target_id=admin_id,
                    details={"role": "super_admin", "status": "active"},
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Successfully created Super Admin [{admin_id}].")
                )
            else:
                self.stdout.write(self.style.WARNING(f"Super Admin [{admin_id}] already exists."))

            # 2. Demo Employee
            demo_user = CustomUser.objects.filter(employee_id=demo_id).first()
            if not demo_user:
                demo_user = CustomUser.objects.create_user(
                    employee_id=demo_id,
                    full_name=demo_name,
                    password=demo_password,
                    role=CustomUser.Role.EMPLOYEE,
                    status=CustomUser.Status.ACTIVE,
                    attendance_mode=CustomUser.AttendanceMode.MANUAL_ONLY,
                )
                record_audit_event(
                    event_type="auth.user_created",
                    actor=admin_user,
                    target_entity="CustomUser",
                    target_id=demo_id,
                    details={"role": "employee", "mode": "manual_only"},
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Successfully created Demo Employee [{demo_id}].")
                )
            else:
                self.stdout.write(self.style.WARNING(f"Demo Employee [{demo_id}] already exists."))

        self.stdout.write(self.style.SUCCESS("Initial accounts seeding complete."))
