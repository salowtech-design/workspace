import pytest

from apps.core.models import Notification
from apps.events import services as es
from tests.unit.test_events_invite_notice import IST, D, _make, _u


def _kinds(u):
    return sorted(Notification.objects.filter(user=u).values_list("kind", flat=True))


def _move(org, ev, title="Secret budget review", hour=12, loc=None):
    return es.update_event(org, ev.public_id, ev.version, title, D(2026, 12, 3, hour), D(2026, 12, 3, hour + 1), IST, location=loc)


@pytest.mark.django_db
def test_time_or_place_change_and_cancel_notify_non_declined_guests_only():
    org, g1, g2, x = _u("CN-O", "Olive"), _u("CN-1", "G One"), _u("CN-2", "G Two"), _u("CN-X", "Out")
    ev = _make(org, [g1, g2])
    es.respond(g2, ev.public_id, "declined")
    ev = _move(org, ev)  # time moves
    assert _kinds(g1) == ["events.changed", "events.invited"]
    assert _kinds(g2) == ["events.invited"]  # declined guest not told
    assert _kinds(org) == [] and _kinds(x) == []
    n = Notification.objects.get(user=g1, kind="events.changed")
    assert n.title == "Olive changed the time or place of an event you were invited to"
    assert "Secret budget" not in n.title
    ev = es.update_event(org, ev.public_id, ev.version, "Renamed only", D(2026, 12, 3, 12), D(2026, 12, 3, 13), IST)  # title only
    assert _kinds(g1).count("events.changed") == 1  # negative control: no new notice
    ev = _move(org, ev, title="Renamed only", hour=12, loc="Room 5")  # place moves
    assert _kinds(g1).count("events.changed") == 2
    es.cancel_event(org, ev.public_id)
    from django.test import Client
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(g1)
    assert c.get(Notification.objects.get(user=g1, kind="events.cancelled").link).status_code == 200
    assert _kinds(g1).count("events.cancelled") == 1 and _kinds(g2).count("events.cancelled") == 0
    es.cancel_event(org, ev.public_id)  # repeat cancel
    assert _kinds(g1).count("events.cancelled") == 1


@pytest.mark.django_db
def test_failed_change_notice_does_not_undo_the_change(monkeypatch):
    org, g1 = _u("CN-O2", "Org"), _u("CN-3", "G")
    ev = _make(org, [g1])

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(es, "notify", boom)
    ev = _move(org, ev)
    assert ev.version == 2
    assert es.cancel_event(org, ev.public_id).status == "cancelled"
