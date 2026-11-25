import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from apps.accounts.models import CustomUser
from apps.events import services as es
from apps.events.models import Event, Guest

IST = "Asia/Kolkata"
D = dt.datetime


def _u(eid, status="active"):
    u = CustomUser.objects.create_user(employee_id=eid, full_name=eid)
    u.status = status
    u.save()
    return u


def _utc(y, mo, d, h=0, m=0):
    return dt.datetime(y, mo, d, h, m, tzinfo=dt.timezone.utc)


@pytest.mark.django_db
def test_create_validates_times_zone_guests_and_stores_utc():
    org, g1, off = _u("EV-ORG"), _u("EV-G1"), _u("EV-OFF", "suspended")
    ev = es.create_event(org, " Review ", D(2026, 11, 27, 10), D(2026, 11, 27, 11), IST, [g1.pk])
    assert ev.title == "Review" and ev.start_utc == _utc(2026, 11, 27, 4, 30)
    assert Guest.objects.get(event=ev).person_id == g1.pk
    ok = (D(2026, 11, 27, 10), D(2026, 11, 27, 11), IST)
    bad = [
        ("", *ok),
        ("x" * 201, *ok),
        ("t", D(2026, 11, 27, 11), D(2026, 11, 27, 10), IST),  # end before start
        ("t", D(2026, 11, 27, 10), D(2026, 11, 27, 10), IST),  # zero length
        ("t", D(2026, 11, 27, 10), D(2026, 12, 20, 10), IST),  # over 14 days
        ("t", D(2026, 11, 27, 10), D(2026, 11, 27, 11), "Mars/Base"),
        ("t", dt.datetime(2026, 11, 27, 10, tzinfo=dt.timezone.utc), D(2026, 11, 27, 11), IST),
        ("t", D(2026, 3, 8, 2, 30), D(2026, 3, 8, 3, 30), "America/New_York"),  # no such time
    ]
    for args in bad:
        with pytest.raises(ValidationError):
            es.create_event(org, *args)
    with pytest.raises(ValidationError):
        es.create_event(org, "t", *ok, guest_ids=[off.pk])
    with pytest.raises(ValidationError):
        es.create_event(org, "t", *ok, guest_ids=[999999])
    with pytest.raises(ValidationError):
        es.create_event(org, "t", *ok, guest_ids=list(range(1000, 1060)))
    ev2 = es.create_event(org, "self-invite", *ok, guest_ids=[org.pk, g1.pk, g1.pk])
    assert Guest.objects.filter(event=ev2).count() == 1  # organiser and duplicates dropped
    stale = CustomUser.objects.get(pk=org.pk)
    CustomUser.objects.filter(pk=org.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        es.create_event(stale, "t", *ok)
    assert Event.objects.count() == 2


@pytest.mark.django_db
def test_update_cancel_respond_authority_versions_and_noop():
    org, g1, out = _u("EU-ORG"), _u("EU-G1"), _u("EU-OUT")
    a = (D(2026, 11, 27, 10), D(2026, 11, 27, 11), IST)
    ev = es.create_event(org, "Plan", *a, guest_ids=[g1.pk])
    pid = ev.public_id
    for who in (g1, out):
        with pytest.raises(Http404):
            es.update_event(who, pid, 1, "Hack", *a)
        with pytest.raises(Http404):
            es.cancel_event(who, pid)
    with pytest.raises(Http404):
        es.respond(out, pid, "accepted")
    assert es.update_event(org, pid, 1, "Plan", *a).version == 1  # same values: no bump
    assert es.update_event(org, pid, 1, "Plan 2", *a).version == 2
    with pytest.raises(es.StaleEvent):
        es.update_event(org, pid, 1, "Plan 3", *a)
    assert es.respond(g1, pid, "accepted").response == "accepted"
    for bad in ("needs_action", "bogus"):
        with pytest.raises(ValidationError):
            es.respond(g1, pid, bad)
    assert es.cancel_event(org, pid).status == "cancelled"
    assert es.cancel_event(org, pid).version == 3  # repeat: no second bump
    with pytest.raises(ValidationError):
        es.update_event(org, pid, 3, "Late", *a)
    with pytest.raises(ValidationError):
        es.respond(g1, pid, "declined")
    with pytest.raises(Http404):
        es.get_event(out, pid)
    assert es.get_event(g1, pid).pk == ev.pk


@pytest.mark.django_db
def test_own_calendar_and_free_busy_show_only_busy_and_skip_declined_and_cancelled():
    org, g1, other = _u("FB-ORG"), _u("FB-G1"), _u("FB-OTH")
    day = lambda h, m=0: D(2026, 11, 27, h, m)  # noqa: E731
    e1 = es.create_event(org, "Secret board talk", day(10), day(11), IST, [g1.pk])
    es.create_event(org, "Adjacent", day(11), day(12), IST)
    e3 = es.create_event(org, "Dropped", day(14), day(15), IST, [g1.pk])
    e4 = es.create_event(org, "Cancelled", day(16), day(17), IST)
    es.respond(g1, e3.public_id, "declined")
    es.cancel_event(org, e4.public_id)
    lo, hi = _utc(2026, 11, 26), _utc(2026, 11, 29)
    assert [e.title for e in es.events_for(g1, lo, hi)] == ["Secret board talk"]
    assert [e.title for e in es.events_for(org, lo, hi)] == [
        "Secret board talk",
        "Adjacent",
        "Dropped",
    ]
    assert list(es.events_for(other, lo, hi)) == []
    fb = es.free_busy(other, [org.pk, g1.pk], lo, hi)
    assert fb[org.pk] == [
        (_utc(2026, 11, 27, 4, 30), _utc(2026, 11, 27, 6, 30)),  # merged touching
        (_utc(2026, 11, 27, 8, 30), _utc(2026, 11, 27, 9, 30)),
    ]
    assert fb[g1.pk] == [(_utc(2026, 11, 27, 4, 30), _utc(2026, 11, 27, 5, 30))]
    assert "Secret" not in repr(fb) and e1.title not in repr(fb)
    # windows and people are bounded
    with pytest.raises(ValidationError):
        es.free_busy(other, [org.pk], lo, lo + dt.timedelta(days=63))
    with pytest.raises(ValidationError):
        es.free_busy(other, [org.pk], hi, lo)
    with pytest.raises(ValidationError):
        es.free_busy(other, list(range(2000, 2060)), lo, hi)
    with pytest.raises(ValidationError):
        es.free_busy(other, [org.pk], D(2026, 11, 26), D(2026, 11, 27))  # naive
    # an event that ends exactly at the window start is not busy
    assert (
        es.free_busy(other, [org.pk], _utc(2026, 11, 27, 6, 30), _utc(2026, 11, 27, 8, 30))[org.pk]
        == []
    )
