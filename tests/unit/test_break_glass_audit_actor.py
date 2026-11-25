"""Break-glass audit rows store the signed-in user as actor; a bare user call does not."""

import pytest

from apps.accounts.auth.stepup import activate_break_glass
from apps.accounts.models import AuthEvent, CustomUser


def _admin(n):
    return CustomUser.objects.create_user(
        employee_id=f"ST-BGA{n}",
        full_name=f"BGA{n}",
        email=f"st-bga{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


@pytest.mark.django_db
def test_bare_user_activation_has_no_actor():
    user = _admin(1)
    activate_break_glass(user, reason="Synthetic drill reason text")
    ev = AuthEvent.objects.get(event="break_glass_activated")
    assert ev.employee_id == user.employee_id
    assert ev.actor_employee_id == ""


@pytest.mark.django_db
def test_request_activation_and_revoke_store_the_signed_in_user(rf):
    from apps.accounts.auth.stepup import revoke_break_glass

    user = _admin(2)
    request = rf.post("/")
    request.user = user
    request.session = {}
    activate_break_glass(request, reason="Synthetic drill reason text")
    revoke_break_glass(request)
    on = AuthEvent.objects.get(event="break_glass_activated")
    off = AuthEvent.objects.get(event="break_glass_deactivated")
    assert on.actor_employee_id == user.employee_id
    assert off.actor_employee_id == user.employee_id
