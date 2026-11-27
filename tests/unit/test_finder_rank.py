import datetime as dt

import pytest

from apps.accounts.models import CustomUser
from apps.core.foundation.search import rank_text, search_grouped
from apps.events import services as es
from apps.tasks import services as ts


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _titles(user, q, name, per=3):
    hits, truncated = search_grouped(user, q, types=(name,), per_module=per)[name]
    return [h.title for h in hits], truncated


def test_rank_text_classes():
    assert [rank_text(t, "budget") for t in ("Budget", "Budget plan", "Q3 budget", "Cashbudgetx", "other")] == [0, 1, 2, 3, 3]


@pytest.mark.django_db
def test_best_match_is_not_cut_by_newer_weaker_matches_and_others_never_crowd_or_appear():
    a, b = _u("RK-A"), _u("RK-B")
    la, lb = ts.create_list(a, "Mine"), ts.create_list(b, "Theirs")
    ts.create_task(a, la, "Cashbudgetx")  # oldest, weakest
    ts.create_task(a, la, "Q3 budget")
    ts.create_task(a, la, "Budget plan")
    ts.create_task(a, la, "Budget")  # exact, but older than the filler below
    for i in range(14):  # newer weaker matches that would fill a newest-first pool of 9
        ts.create_task(a, la, f"Notes-budget {i}")
    for i in range(14):  # another person's exact matches: must neither show nor take a slot
        ts.create_task(b, lb, "Budget")
    titles, truncated = _titles(a, "budget", "tasks", per=3)
    assert titles == ["Budget", "Budget plan", "Q3 budget"]  # exact, prefix, word start, in that order
    assert truncated is True
    everything, _ = _titles(a, "budget", "tasks", per=40)
    assert everything.count("Budget") == 1  # b's fourteen exact matches are absent
    assert len(everything) == 18


@pytest.mark.django_db
def test_events_rank_before_the_cap_and_hide_other_peoples_events():
    a, b = _u("RK-C"), _u("RK-D")
    D = dt.datetime
    es.create_event(a, "Zebra", D(2026, 12, 1, 10), D(2026, 12, 1, 11), "Asia/Kolkata")  # earliest start
    for i in range(12):
        es.create_event(a, f"Zebra review {i}", D(2026, 12, 2 + i, 10), D(2026, 12, 2 + i, 11), "Asia/Kolkata")
    es.create_event(b, "Zebra", D(2027, 1, 1, 10), D(2027, 1, 1, 11), "Asia/Kolkata")
    titles, _ = _titles(a, "zebra", "calendar", per=2)
    assert titles[0] == "Zebra" and len(titles) == 2
    everything, _ = _titles(a, "zebra", "calendar", per=40)
    assert everything.count("Zebra") == 1


@pytest.mark.django_db
def test_people_exact_employee_id_ranks_first():
    me = _u("RK-ME")
    _u("RK-X1")
    other = _u("RK-X")
    other.full_name = "Someone Else"
    other.save()
    titles, _ = _titles(me, "rk-x", "people", per=5)
    assert titles[0] == "Someone Else (RK-X)"
