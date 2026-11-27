import pytest
from django.urls import reverse

from apps.events import services as es
from tests.unit.test_chat import _c
from tests.unit.test_events import IST, D, _u

NY = "America/New_York"
DAILY3 = {"freq": "daily", "interval": 1, "weekdays": [], "count": 3, "until": None}


def mk(who, title, s, e, tz=IST, guests=(), rule=None):
    return es.create_event(who, title, s, e, tz, [g.pk for g in guests], rule=rule)


def titles(user, ev):
    shown, total = es.overlaps_for(user, ev)
    return sorted(o.title for o in shown), total


@pytest.mark.django_db
def test_overlap_rules_boundaries_status_and_visibility():
    a, b, x = _u("OV-A"), _u("OV-B"), _u("OV-X")
    main = mk(a, "Main", D(2026, 12, 1, 10), D(2026, 12, 1, 11))
    mk(a, "Inside", D(2026, 12, 1, 10, 30), D(2026, 12, 1, 11, 30))  # overlaps
    mk(a, "Touches end", D(2026, 12, 1, 11), D(2026, 12, 1, 12))  # end == start: no
    mk(a, "Touches start", D(2026, 12, 1, 9), D(2026, 12, 1, 10))  # no
    mk(a, "Different day", D(2026, 12, 2, 10), D(2026, 12, 2, 11))  # no
    mk(b, "Invited", D(2026, 12, 1, 10, 15), D(2026, 12, 1, 10, 45), guests=[a])  # a is a guest: overlaps
    dec = mk(b, "Declined", D(2026, 12, 1, 10), D(2026, 12, 1, 11), guests=[a])
    es.respond(a, dec.public_id, "declined")
    gone = mk(a, "Cancelled", D(2026, 12, 1, 10), D(2026, 12, 1, 11))
    es.cancel_event(a, gone.public_id)
    mk(x, "Someone else's", D(2026, 12, 1, 10), D(2026, 12, 1, 11))  # a is not on it
    assert titles(a, main) == (["Inside", "Invited"], 2)
    assert "Main" not in titles(a, main)[0]


@pytest.mark.django_db
def test_time_zones_compare_the_same_instant():
    a = _u("OV-T")
    ist = mk(a, "India", D(2026, 12, 1, 10), D(2026, 12, 1, 11), IST)  # 04:30 to 05:30 UTC
    mk(a, "New York", D(2026, 11, 30, 23, 45), D(2026, 12, 1, 0, 45), NY)  # 04:45 to 05:45 UTC: overlaps
    mk(a, "New York later", D(2026, 12, 1, 10), D(2026, 12, 1, 11), NY)  # 15:00 UTC: no
    assert titles(a, ist) == (["New York"], 1)


@pytest.mark.django_db
def test_series_occurrences_and_the_62_day_cap():
    a = _u("OV-S")
    series = mk(a, "Daily", D(2026, 12, 1, 10), D(2026, 12, 1, 11), rule=DAILY3)  # Dec 1, 2, 3
    mk(a, "Third day", D(2026, 12, 3, 10, 30), D(2026, 12, 3, 11, 30))  # overlaps the 3rd occurrence only
    mk(a, "After the series", D(2026, 12, 4, 10), D(2026, 12, 4, 11))  # the series has ended: no
    mk(a, "Beyond the window", D(2027, 2, 15, 10), D(2027, 2, 15, 11))  # 76 days later: not checked
    assert titles(a, series) == (["Third day"], 1)
    long = mk(a, "Long", D(2026, 12, 10, 10), D(2026, 12, 10, 11),
              rule={"freq": "daily", "interval": 1, "weekdays": [], "count": 120, "until": None})
    mk(a, "Day 61", D(2027, 2, 8, 10), D(2027, 2, 8, 11))  # day 61: inside the 62-day window
    mk(a, "Day 70", D(2027, 2, 17, 10), D(2027, 2, 17, 11))  # outside: not checked
    assert titles(a, long) == (["Day 61"], 1)


@pytest.mark.django_db
def test_the_list_is_capped_but_the_total_is_exact():
    a = _u("OV-C")
    main = mk(a, "Main", D(2026, 12, 1, 10), D(2026, 12, 1, 12))
    for i in range(8):
        mk(a, f"Clash {i}", D(2026, 12, 1, 10, i), D(2026, 12, 1, 11))
    shown, total = es.overlaps_for(a, main)
    assert len(shown) == 5 and total == 8


@pytest.mark.django_db
def test_create_and_edit_warn_without_blocking_and_name_only_own_events():
    a, x = _u("OV-V"), _u("OV-W")
    mk(a, "Existing", D(2026, 12, 1, 10), D(2026, 12, 1, 11))
    mk(x, "Secret of X", D(2026, 12, 1, 10), D(2026, 12, 1, 11))
    c = _c(a)
    form = {"title": "New one", "start": "2026-12-01T10:30", "end": "2026-12-01T11:30", "tz": IST}
    r = c.post(reverse("events:new"), form, follow=True)
    assert r.redirect_chain and es.Event.objects.filter(title="New one").count() == 1  # saved as entered
    msgs = [str(m) for m in r.context["messages"]]
    warn = [m for m in msgs if m.startswith("Heads up")]
    assert len(warn) == 1 and "overlaps 1 other event" in warn[0] and "Existing (Tue 01 Dec 10:00 to 11:00 Asia/Kolkata)" in warn[0]
    assert "Secret" not in " ".join(msgs) and "New one (" not in warn[0]
    # no overlap: no warning, and the normal success text is unchanged
    r = c.post(reverse("events:new"), {**form, "title": "Alone", "start": "2026-12-05T10:00", "end": "2026-12-05T11:00"}, follow=True)
    msgs = [str(m) for m in r.context["messages"]]
    assert r.redirect_chain and not any(m.startswith("Heads up") for m in msgs) and any(m.startswith("Event created.") for m in msgs)
    # edit it onto the existing event's time: warns once, lists Existing and not itself
    alone = es.Event.objects.get(title="Alone")
    r = c.post(reverse("events:edit", args=[alone.public_id]),
               {"version": alone.version, "title": "Alone", "start": "2026-12-01T10:00", "end": "2026-12-01T11:00", "tz": IST}, follow=True)
    msgs = [str(m) for m in r.context["messages"]]
    warn = [m for m in msgs if m.startswith("Heads up")]
    assert r.redirect_chain and len(warn) == 1 and "Existing" in warn[0] and "New one" in warn[0] and "Alone (" not in warn[0]
    assert any(m.startswith("Event saved.") for m in msgs)
