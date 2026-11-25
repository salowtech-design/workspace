"""Sudo and backup audit rows store the signed-in user as actor; a bare user elevation does not."""

import pytest
from django.urls import reverse

from apps.accounts.auth.stepup import elevate_sudo
from apps.accounts.models import AuthEvent, CustomUser


def _user(n, role):
    return CustomUser.objects.create_user(
        employee_id=f"ST-SAA{n}",
        full_name=f"SAA{n}",
        email=f"st-saa{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=role,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


@pytest.mark.django_db
def test_sudo_failure_and_grant_store_the_signed_in_user_as_actor(client):
    user = _user(1, CustomUser.Role.ADMIN)
    client.force_login(user)
    client.post(reverse("accounts:sudo"), {"password": "wrong-pass", "next": "/auth/security/"})
    failed = AuthEvent.objects.get(event="sudo_password_failed")
    assert failed.employee_id == user.employee_id
    assert failed.actor_employee_id == user.employee_id
    client.post(
        reverse("accounts:sudo"), {"password": "Xx-pass-2026!ab", "next": "/auth/security/"}
    )
    granted = AuthEvent.objects.get(event="sudo_granted")
    assert granted.actor_employee_id == user.employee_id


@pytest.mark.django_db
def test_bare_user_elevation_has_no_actor():
    user = _user(2, CustomUser.Role.ADMIN)
    elevate_sudo(user)
    ev = AuthEvent.objects.get(event="sudo_granted")
    assert ev.employee_id == user.employee_id
    assert ev.actor_employee_id == ""


@pytest.mark.django_db
def test_queued_backup_records_the_requesting_user_as_actor(rf, monkeypatch):
    from django.contrib.messages.storage.fallback import FallbackStorage

    from apps.accounts.models import BackupRun
    from apps.accounts.views.backups import admin_backup_run_view

    user = _user(3, CustomUser.Role.SUPER_ADMIN)
    # The sudo gate has its own browser proof; here only the audit row is checked.
    monkeypatch.setattr("apps.accounts.views.backups.is_sudo_active", lambda r: True)
    request = rf.post("/auth/admin/backups/run/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    response = admin_backup_run_view(request)
    assert response.status_code == 302
    assert BackupRun.objects.count() == 1
    ev = AuthEvent.objects.get(event="backup_requested")
    assert ev.actor_employee_id == user.employee_id
    assert "queued" in ev.message
