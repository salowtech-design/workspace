import datetime as dt

import pytest
from django.test import Client

from apps.accounts.models import CustomUser
from apps.core.models import Notification
from apps.events import services as es

IST = "Asia/Kolkata"
D = dt.datetime


def _u(eid, name):
    return CustomUser.objects.create(
        employee_id=eid, full_name=name, email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _make(org, guests, title="Secret budget review"):
    return es.create_event(org, title, D(2026, 12, 3, 10), D(2026, 12, 3, 11), IST, [g.pk for g in guests])


@pytest.mark.django_db
def test_each_guest_gets_one_notice_the_organizer_none_and_no_event_content():
    org, g1, g2 = _u("IN-O", "Olive Organizer"), _u("IN-1", "Guest One"), _u("IN-2", "Guest Two")
    ev = _make(org, [g1, g2])
    for g in (g1, g2):
        rows = list(Notification.objects.filter(user=g))
        assert len(rows) == 1
        assert rows[0].kind == "events.invited"
        assert rows[0].title == "Olive Organizer invited you to an event"
        assert "Secret budget review" not in rows[0].title
        assert str(ev.public_id) in rows[0].link
    assert Notification.objects.filter(user=org).count() == 0
    # the guest can open the link
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(g1)
    assert c.get(Notification.objects.get(user=g1).link).status_code == 200


@pytest.mark.django_db
def test_a_person_not_invited_gets_nothing_and_a_failed_notice_does_not_undo_the_event(monkeypatch):
    org, g1, other = _u("IN-O2", "Org Two"), _u("IN-3", "Guest Three"), _u("IN-4", "Bystander")
    _make(org, [g1])
    assert Notification.objects.filter(user=other).count() == 0

    def boom(*a, **k):
        raise RuntimeError("notice failed")

    monkeypatch.setattr(es, "notify", boom)
    ev = _make(org, [g1], title="Second event")
    assert ev.pk is not None and es.Guest.objects.filter(event=ev, person=g1).exists()
