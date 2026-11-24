"""Team add and outbox retry write audit events naming the actor, target, action and time."""

import pytest
from django.urls import reverse

from apps.accounts.models import AuthEvent, CustomUser
from apps.core.models import EmailOutbox
from apps.directory.models import Department, Team


def _user(n, role):
    return CustomUser.objects.create_user(
        employee_id=f"ST-AU{n}",
        full_name=f"AU{n}",
        email=f"st-au{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=role,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


def _dept():
    return Department.objects.get_or_create(code="ENG", defaults={"name": "Engineering"})[0]


@pytest.mark.django_db
def test_team_add_records_actor_target_and_action(client):
    admin, emp = _user(1, CustomUser.Role.ADMIN), _user(2, CustomUser.Role.EMPLOYEE)
    team = Team.objects.create(name="Audit Team", code="AUDT", department=_dept())
    client.force_login(admin)
    client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "add_team_membership",
            "target_employee_id": emp.employee_id,
            "team_code": "AUDT",
        },
    )
    ev = AuthEvent.objects.get(event="team_membership_added")
    assert ev.actor_employee_id == admin.employee_id
    assert ev.employee_id == emp.employee_id
    assert "AUDT" in ev.message and ev.time is not None
    assert team.memberships.filter(user=emp).exists()
    # Repeating the same add changes nothing and logs nothing new.
    client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "add_team_membership",
            "target_employee_id": emp.employee_id,
            "team_code": "AUDT",
        },
    )
    assert AuthEvent.objects.filter(event="team_membership_added").count() == 1


@pytest.mark.django_db
def test_outbox_retry_records_actor_and_item(client):
    admin = _user(1, CustomUser.Role.ADMIN)
    item = EmailOutbox.objects.create(
        recipient="failed@salowtech.com",
        subject="Alert",
        body_text="x",
        notification_type="security_alert",
        status=EmailOutbox.Status.FAILED,
        retry_count=5,
    )
    client.force_login(admin)
    client.post(
        reverse("accounts:admin_iam"),
        {"action": "retry_outbox_item", "item_type": "email", "item_id": str(item.id)},
    )
    ev = AuthEvent.objects.get(event="outbox_email_requeued")
    assert ev.actor_employee_id == admin.employee_id
    assert ev.reference_id == str(item.id)
    assert "failed@salowtech.com" not in ev.message


@pytest.mark.django_db
def test_denied_actor_writes_no_event(client):
    emp, other = _user(1, CustomUser.Role.EMPLOYEE), _user(2, CustomUser.Role.EMPLOYEE)
    Team.objects.create(name="T", code="TT", department=_dept())
    client.force_login(emp)
    client.post(
        reverse("accounts:admin_iam"),
        {
            "action": "add_team_membership",
            "target_employee_id": other.employee_id,
            "team_code": "TT",
        },
    )
    assert not AuthEvent.objects.filter(event="team_membership_added").exists()
