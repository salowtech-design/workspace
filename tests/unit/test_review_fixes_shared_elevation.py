"""Step-up and break-glass grants live in the database, not in per-process dictionaries."""
import pytest

from apps.accounts.auth import stepup
from apps.accounts.models import CustomUser, PrivilegeElevation


@pytest.fixture
def admin(db):
    return CustomUser.objects.create_user(
        employee_id="ST-ELEV-01", full_name="A", password="ValidPass2026!@#",
        role=CustomUser.Role.SUPER_ADMIN, status=CustomUser.Status.ACTIVE,
    )


def test_stores_are_database_backed_not_dicts():
    assert not isinstance(stepup._USER_SUDO_CACHE, dict)
    assert not isinstance(stepup._USER_BREAK_GLASS_CACHE, dict)


@pytest.mark.django_db
def test_sudo_grant_is_a_database_row_visible_to_another_worker(admin):
    stepup.grant_sudo(admin)
    row = PrivilegeElevation.objects.get(user_id_ref=admin.id, kind="sudo")
    assert row.granted_at > 0
    # A "different worker" has an empty process but reads the same table: simulate by a fresh store object.
    other_worker = stepup._SharedGrantStore("sudo", with_details=False)
    assert other_worker.get(admin.id) == row.granted_at
    assert stepup.is_sudo_active(admin) is True


@pytest.mark.django_db
def test_break_glass_roundtrip_expiry_and_revoke(admin, monkeypatch):
    stepup.activate_break_glass(admin, reason="database outage during payroll", justification_ticket="T-1")
    row = PrivilegeElevation.objects.get(user_id_ref=admin.id, kind="break_glass")
    assert row.reason.startswith("database outage") and row.ticket == "T-1"
    assert stepup.is_break_glass_active(admin) is True
    # expiry: move the grant 16 minutes into the past
    PrivilegeElevation.objects.filter(pk=row.pk).update(granted_at=row.granted_at - 16 * 60)
    assert stepup.is_break_glass_active(admin) is False
    stepup.activate_break_glass(admin, reason="second emergency access reason")
    stepup.revoke_break_glass(admin)
    assert not PrivilegeElevation.objects.filter(user_id_ref=admin.id, kind="break_glass").exists()
    assert PrivilegeElevation.objects.filter(kind="break_glass").count() == 0


@pytest.mark.django_db
def test_regrant_updates_single_row(admin):
    stepup.grant_sudo(admin)
    stepup.grant_sudo(admin)
    assert PrivilegeElevation.objects.filter(user_id_ref=admin.id, kind="sudo").count() == 1
