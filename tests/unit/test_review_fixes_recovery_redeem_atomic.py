"""Recovery code redemption must not consume a code when its audit write fails."""

from unittest import mock

import pytest
from django.db import OperationalError

from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes, redeem_recovery_code
from apps.accounts.models import CustomUser, RecoveryCode


@pytest.mark.django_db(transaction=True)
def test_failed_audit_write_does_not_consume_code():
    user = CustomUser.objects.create_user(
        employee_id="ST-ATOM-001",
        full_name="Atomic Redeem",
        password="ValidPassword2026!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    code = generate_ten_recovery_codes(user)[0]

    with mock.patch(
        "apps.accounts.auth.recovery_codes.log_auth_event",
        side_effect=OperationalError("database is locked"),
    ):
        with pytest.raises(OperationalError):
            redeem_recovery_code(user, code)

    # The code must still be usable: a retry succeeds exactly once.
    assert RecoveryCode.objects.filter(user=user, used_at__isnull=False).count() == 0
    assert redeem_recovery_code(user, code) is True
    with pytest.raises(Exception, match="invalid or has already been used"):
        redeem_recovery_code(user, code)
