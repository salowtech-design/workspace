"""Passkey revoke stores the signed-in user as actor; a call without an actor leaves it blank."""

import pytest
from django.urls import reverse

from apps.accounts.auth.passkeys import revoke_passkey
from apps.accounts.auth.stepup import elevate_sudo
from apps.accounts.models import AuthEvent, CustomUser, PasskeyCredential


def _user(n):
    return CustomUser.objects.create_user(
        employee_id=f"ST-PKA{n}",
        full_name=f"PKA{n}",
        email=f"st-pka{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


def _passkey(user):
    return PasskeyCredential.objects.create(
        user=user,
        credential_id=f"cred-{user.employee_id}".encode(),
        public_key=b"pk",
        sign_count=0,
        display_name="k",
    )


@pytest.mark.django_db
def test_revoke_view_stores_the_signed_in_user_as_actor(client):
    user = _user(1)
    pk = _passkey(user)
    client.force_login(user)
    elevate_sudo(user)
    client.post(reverse("accounts:passkey_delete", args=[pk.id]))
    ev = AuthEvent.objects.get(event="passkey_revoked")
    assert ev.employee_id == user.employee_id
    assert ev.actor_employee_id == user.employee_id
    pk.refresh_from_db()
    assert pk.is_active is False


@pytest.mark.django_db
def test_revoke_without_actor_leaves_it_blank():
    user = _user(2)
    pk = _passkey(user)
    assert revoke_passkey(user, pk.id) is True
    assert AuthEvent.objects.get(event="passkey_revoked").actor_employee_id == ""
