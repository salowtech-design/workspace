"""
Setup exact user accounts per user directive:
- Super Admin: AKIF-CO-001 / mohdhussain123 (TOTP invalidated per security audit; re-enrollment required)
- Synthetic Staging Admin: STAGE-ADM-0001 / StageAdminPass2026!@# (Active, MFA enrolled for test automation)
- Employee: EMP-BD-001 / employee786 (status: invited, for Login)
- Delete all other user accounts.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

import pyotp
from django.utils import timezone

from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes
from apps.accounts.auth.sessions import revoke_user_sessions
from apps.accounts.auth.stepup import _USER_SUDO_CACHE
from apps.accounts.models import (
    CustomUser,
    RecoveryCode,
    TOTPCredential,
)


def setup_accounts():
    # 1. Setup AKIF-CO-001 (Super Admin)
    sa, _ = CustomUser.objects.update_or_create(
        employee_id="AKIF-CO-001",
        defaults={
            "full_name": "Akif Hussain",
            "role": CustomUser.Role.SUPER_ADMIN,
            "status": CustomUser.Status.ACTIVE,
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )
    sa.set_password("mohdhussain123")
    sa.save()

    # Invalidate AKIF-CO-001 TOTP credential due to security audit -> require re-enrolment
    TOTPCredential.objects.filter(user=sa).delete()
    RecoveryCode.objects.filter(user=sa).delete()
    revoke_user_sessions(sa)
    _USER_SUDO_CACHE.pop(sa.id, None)

    # 2. Setup EMP-BD-001 (Employee, invited, for Login)
    emp, _ = CustomUser.objects.update_or_create(
        employee_id="EMP-BD-001",
        defaults={
            "full_name": "Employee BD 001",
            "role": CustomUser.Role.EMPLOYEE,
            "status": CustomUser.Status.INVITED,
            "is_staff": False,
            "is_superuser": False,
            "is_active": True,
        },
    )
    emp.set_password("employee786")
    emp.save()

    # Make sure EMP-BD-001 has no confirmed TOTP or sessions (so they must go through Login / Activation)
    TOTPCredential.objects.filter(user=emp).delete()
    RecoveryCode.objects.filter(user=emp).delete()
    revoke_user_sessions(emp)
    _USER_SUDO_CACHE.pop(emp.id, None)

    # 3. Setup STAGE-ADM-0001 (Dedicated Synthetic Staging Administrator for automated testing)
    stage_adm, _ = CustomUser.objects.update_or_create(
        employee_id="STAGE-ADM-0001",
        defaults={
            "full_name": "Synthetic Staging Admin",
            "email": "staging.admin@salowtech.com",
            "email_verified": True,
            "role": CustomUser.Role.SUPER_ADMIN,
            "status": CustomUser.Status.ACTIVE,
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )
    stage_adm.set_password("StageAdminPass2026!@#")
    stage_adm.save()

    # Dedicated dynamically generated TOTP credential for automated browser/staging testing
    kr = get_auth_key_ring()
    stage_secret = pyotp.random_base32(length=32)
    c, n, k = encrypt_secret(stage_secret, kr)
    TOTPCredential.objects.update_or_create(
        user=stage_adm,
        defaults={
            "encrypted_secret": c,
            "nonce": n,
            "key_id": k,
            "confirmed_at": timezone.now(),
            "last_used_step": 0,
        },
    )
    generate_ten_recovery_codes(stage_adm)
    revoke_user_sessions(stage_adm)
    _USER_SUDO_CACHE.pop(stage_adm.id, None)

    # 4. Clean up protected foreign keys and delete all other users
    other_users = CustomUser.objects.exclude(
        employee_id__in=["AKIF-CO-001", "EMP-BD-001", "STAGE-ADM-0001"]
    )
    deleted_ids = list(other_users.values_list("employee_id", flat=True))

    from django.apps import apps

    # Dynamically delete records referencing other_users across all installed apps
    for model in apps.get_models():
        for field in model._meta.fields:
            if field.is_relation and field.related_model == CustomUser:
                try:
                    filter_kwargs = {f"{field.name}__in": other_users}
                    model.objects.filter(**filter_kwargs).delete()
                except Exception:  # noqa: S110
                    pass

    other_users.delete()

    print(f"Deleted other accounts: {deleted_ids}")
    print("Remaining active accounts in database:")
    for u in CustomUser.objects.all():
        print(
            f" - {u.employee_id} | Role: {u.role} | Status: {u.status} | Has PW: {u.has_usable_password()}"
        )


if __name__ == "__main__":
    setup_accounts()
