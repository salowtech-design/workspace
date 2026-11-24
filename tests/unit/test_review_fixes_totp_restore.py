"""Approved policy changes: TOTP window +-1 with a clock hint, and restore failures are loud."""

import logging
from unittest import mock

import pyotp
import pytest
from django.utils import timezone

from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import (
    TOTP_VERIFY_WINDOW,
    encrypt_secret,
    get_auth_key_ring,
    verify_totp_code,
)
from apps.accounts.auth.offline_recovery import execute_post_restore_security_reconciliation
from apps.accounts.models import CustomUser, TOTPCredential


@pytest.fixture
def user_with_totp(db):
    user = CustomUser.objects.create_user(
        employee_id="ST-TOTP-W1",
        full_name="W",
        password="ValidPass2026!@#",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    secret = pyotp.random_base32()
    c, n, k = encrypt_secret(secret, get_auth_key_ring())
    TOTPCredential.objects.create(
        user=user, encrypted_secret=c, nonce=n, key_id=k, confirmed_at=timezone.now()
    )
    return user, pyotp.TOTP(secret)


def _code_at(totp, offset_seconds, now):
    return totp.at(now + offset_seconds)


def test_window_constant_is_one():
    assert TOTP_VERIFY_WINDOW == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "offset,ok", [(-30, True), (30, True), (-60, False), (60, False), (-120, False), (120, False)]
)
def test_totp_accepts_one_step_only(user_with_totp, offset, ok):
    user, totp = user_with_totp
    now = 1_800_000_015.0  # mid-step so +-30 s is exactly one step
    code = _code_at(totp, offset, now)
    with mock.patch("apps.accounts.auth.mfa_totp.time.time", return_value=now):
        if ok:
            assert verify_totp_code(user, code) is True
        else:
            with pytest.raises(AuthException) as exc:
                verify_totp_code(user, code)
            assert exc.value.code == "AUTH-MFA-002"


def test_wrong_code_message_has_clock_hint():
    from apps.accounts.auth.errors import CATALOGUE

    assert "date and time" in CATALOGUE["AUTH-MFA-002"].user_message


class _FailingCursor:
    rowcount = 0

    def execute(self, sql):
        raise RuntimeError("table missing")


def test_restore_reconciliation_failure_is_logged_and_raised(caplog):
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            execute_post_restore_security_reconciliation(_FailingCursor(), "")
    assert "reconciliation failed" in caplog.text
