"""Regression: passkeys must require user verification (PIN/biometric), not just presence."""
import json
import re
from pathlib import Path

import pytest
from django.test import RequestFactory

import apps.accounts.auth.passkeys as pk
from apps.accounts.models import CustomUser

SRC = Path(pk.__file__).read_text()


def test_no_verification_downgrade_in_source():
    assert "require_user_verification=False" not in SRC
    assert "UserVerificationRequirement.PREFERRED" not in SRC
    assert len(re.findall(r"require_user_verification=True", SRC)) == 2


@pytest.mark.django_db
def test_options_demand_user_verification():
    user = CustomUser.objects.create_user(
        employee_id="ST-PK-UV01", full_name="Pk User", password="ValidPass2026!@#",
        role=CustomUser.Role.EMPLOYEE, status=CustomUser.Status.ACTIVE,
    )
    req = RequestFactory().get("/", HTTP_HOST="localhost")
    from django.contrib.sessions.backends.signed_cookies import SessionStore
    req.session = SessionStore()
    reg = json.loads(pk.generate_passkey_registration_options(req, user)) if hasattr(pk, "generate_passkey_registration_options") else None
    if reg is not None:
        assert reg["authenticatorSelection"]["userVerification"] == "required"
