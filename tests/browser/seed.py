"""Seed one synthetic employee with a fresh random password and TOTP secret. Secrets go to a 0600 file, never to output."""

import json
import os
import secrets
import stat
import sys

import django

django.setup()
import pyotp  # noqa: E402
from django.contrib.auth.hashers import make_password  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring  # noqa: E402
from apps.accounts.models import CustomUser, TOTPCredential  # noqa: E402

out = sys.argv[1]
pw = secrets.token_urlsafe(18) + "aA1!"
emp = CustomUser.objects.create(
    employee_id="ST-LIVE-1",
    full_name="Live Employee",
    email="live.emp@salowtech.com",
    email_verified=True,
    password=make_password(pw),
    role=CustomUser.Role.EMPLOYEE,
    status=CustomUser.Status.ACTIVE,
    is_active=True,
)
sec = pyotp.random_base32(length=32)
ct, nonce, key_id = encrypt_secret(sec, get_auth_key_ring())
TOTPCredential.objects.create(
    user=emp, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now()
)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
with os.fdopen(fd, "w") as f:
    json.dump({"emp": sec, "emp_pw": pw}, f)
print("seeded one synthetic employee")
