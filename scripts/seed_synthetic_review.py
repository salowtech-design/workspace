"""Seed synthetic users for a LOCAL review. Refuses to run unless the database is local SQLite.

Usage:  python manage.py shell < scripts/seed_synthetic_review.py   (or: python scripts/seed_synthetic_review.py)
Prints the synthetic logins and the base32 TOTP secrets to add to an authenticator app.
"""

import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local_review")

import django  # noqa: E402

django.setup()

import pyotp  # noqa: E402
from django.conf import settings  # noqa: E402
from django.contrib.auth.hashers import make_password  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.auth.iam_auth import create_iam_admin_identity  # noqa: E402
from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring  # noqa: E402
from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes  # noqa: E402
from apps.accounts.models import CustomUser, IAMAdminIdentity, TOTPCredential  # noqa: E402

db = settings.DATABASES["default"]
if db["ENGINE"] != "django.db.backends.sqlite3":
    sys.exit("Refusing to seed: the database is not local SQLite. Unset TIDB_* / USE_TIDB first.")

EMP_PASSWORD = "Rv!" + secrets.token_urlsafe(18)
ADM_PASSWORD = "Rv!" + secrets.token_urlsafe(24)
if CustomUser.objects.filter(employee_id="REVIEW-EMP-1").exists():
    sys.exit(
        "Synthetic review users already exist. Delete local_review.sqlite3 and migrate to start clean."
    )

emp = CustomUser.objects.create(
    employee_id="REVIEW-EMP-1",
    full_name="Review Employee",
    email="review.employee@salowtech.com",
    email_verified=True,
    password=make_password(EMP_PASSWORD),
    role=CustomUser.Role.EMPLOYEE,
    status=CustomUser.Status.ACTIVE,
    is_active=True,
)
emp_secret = pyotp.random_base32(length=32)
ct, nonce, key_id = encrypt_secret(emp_secret, get_auth_key_ring())
TOTPCredential.objects.create(
    user=emp, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now()
)
codes = generate_ten_recovery_codes(emp)

adm_user = CustomUser.objects.create(
    employee_id="REVIEW-ADM-1",
    full_name="Review IAM Admin",
    email="review.admin@salowtech.com",
    email_verified=True,
    password=make_password("Rv!" + secrets.token_urlsafe(24)),
    role=CustomUser.Role.SUPER_ADMIN,
    status=CustomUser.Status.ACTIVE,
    is_active=True,
)
_adm, adm_secret = create_iam_admin_identity(
    user=adm_user, admin_id="IAM-REVIEW-01", password=ADM_PASSWORD, role="SUPER_ADMIN"
)
assert IAMAdminIdentity.objects.filter(admin_id="IAM-REVIEW-01").exists()

print("SYNTHETIC REVIEW DATA (local only)")
print(f"Workspace employee: REVIEW-EMP-1 / {EMP_PASSWORD}  TOTP secret: {emp_secret}")
print(f"IAM administrator:  IAM-REVIEW-01 / {ADM_PASSWORD}  TOTP secret: {adm_secret}")
print("Employee recovery codes (single use):", ", ".join(codes[:3]), "... (10 generated)")
print("Current codes:  python -c \"import pyotp;print(pyotp.TOTP('<SECRET>').now())\"")
