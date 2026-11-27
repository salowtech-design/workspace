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


@pytest.mark.django_db
def test_chat_ranks_the_message_text_before_the_cap_and_never_shows_other_conversations():
    from apps.chat import services as cs

    a, b, c = _u("RK-E"), _u("RK-F"), _u("RK-G")
    grp = cs.create_group(a, "Crew", [b.pk, _u("RK-F2").pk])
    cs.post_message(a, grp.public_id, "quokka", "rk-0")  # exact, oldest
    for i in range(12):  # newer, weaker (word-start) matches
        cs.post_message(a, grp.public_id, f"the quokka thing {i}", f"rk-w{i}")
    other = cs.create_group(c, "Not yours", [_u("RK-G1").pk, _u("RK-G2").pk])
    for i in range(12):  # exact matches in a conversation b is not in
        cs.post_message(c, other.public_id, "quokka", f"rk-x{i}")
    titles, truncated = _titles(b, "quokka", "chat", per=2)
    assert titles[0] == "Crew: quokka" and len(titles) == 2 and truncated is True
    everything, _ = _titles(b, "quokka", "chat", per=40)
    assert len(everything) == 13 and not any("Not yours" in t for t in everything)


@pytest.mark.django_db
def test_drive_ranks_names_before_the_cap_and_other_peoples_files_stay_hidden():
    from apps.drive import services as ds

    a, b = _u("RK-H"), _u("RK-I")
    for i in range(12):  # sort before "Report" by name, so a name-ordered pool of 9 would hold only these
        ds.create_file(a, f"Annual report {i}")
    ds.create_file(a, "Report")
    ds.create_file(b, "Report")
    titles, truncated = _titles(a, "report", "drive", per=2)
    assert titles[0] == "Report" and len(titles) == 2 and truncated is True
    everything, _ = _titles(a, "report", "drive", per=40)
    assert everything.count("Report") == 1 and len(everything) == 13


@pytest.mark.django_db
def test_refused_rows_never_change_what_the_person_sees_or_the_truncation_flag(monkeypatch):
    from apps.core.foundation import permissions as perms
    from apps.core.foundation import search as sr

    a = _u("RK-J")

    class Row:
        def __init__(self, pk):
            self.pk = pk

    def make(refused):
        def fn(user, query, limit):
            # a full pool (9 rows for per_module=2): `refused` rows the backstop will refuse, then the permitted ones
            for i in range(9):
                yield ("rk_thing", Row(i), ("Hidden %d" % i) if i < refused else ("Shown %d" % i), "/x/%d/" % i)
        return fn

    monkeypatch.setitem(perms._POLICIES, "rk_thing", lambda user, action, obj: obj.pk >= 8)
    for refused in (0, 4, 8):
        monkeypatch.setitem(sr._SEARCHERS, "rk%d" % refused, make(refused))
    seen = {}
    for refused in (0, 4, 8):
        hits, truncated = sr.search_grouped(a, "anything", types=("rk%d" % refused,), per_module=2)["rk%d" % refused]
        seen[refused] = (len(hits), truncated)
        assert not any(h.title.startswith("Hidden") for h in hits)
    # only the permitted rows decide the metadata: one permitted row at most (pk 8), so never truncated
    assert seen[8] == (1, False)
    assert seen[4] == (1, False) and seen[0] == (1, False)
    # negative control: permitted rows beyond the cap do set the flag
    monkeypatch.setitem(perms._POLICIES, "rk_thing", lambda user, action, obj: True)
    hits, truncated = sr.search_grouped(a, "anything", types=("rk0",), per_module=2)["rk0"]
    assert len(hits) == 2 and truncated is True
    # and with nothing permitted at all the response is empty and not truncated
    monkeypatch.setitem(perms._POLICIES, "rk_thing", lambda user, action, obj: False)
    hits, truncated = sr.search_grouped(a, "anything", types=("rk8",), per_module=2)["rk8"]
    assert hits == [] and truncated is False


@pytest.mark.django_db
def test_json_search_is_the_pages_own_search_with_only_names_titles_and_urls():
    from django.test import Client

    a, b = _u("RK-K"), _u("RK-L")
    la, lb = ts.create_list(a, "Mine"), ts.create_list(b, "Theirs")
    ts.create_task(a, la, "Palette mine")
    ts.create_task(b, lb, "Palette secret of b")
    c = Client(HTTP_HOST="app.salowtech.com")
    r = c.get("/search/", {"q": "palette", "format": "json"})
    assert r.status_code == 302  # signed out: the same login gate as the page
    c.force_login(a)
    r = c.get("/search/", {"q": "palette", "format": "json"})
    body = r.json()
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    assert [g["name"] for g in body["groups"]] == ["Tasks"]
    assert set(body["groups"][0].keys()) == {"name", "items", "more"}
    assert body["groups"][0]["items"] == [{"title": "Palette mine", "url": body["groups"][0]["items"][0]["url"]}]
    assert body["groups"][0]["items"][0]["url"].startswith("/tasks/")
    assert "secret" not in r.content.decode()
    # nothing found and nothing permitted look the same, and say nothing about hidden items
    none = c.get("/search/", {"q": "palette secret of", "format": "json"}).json()
    assert none == {"groups": []}
    assert c.get("/search/", {"q": "x", "format": "json"}).status_code == 400
    assert c.get("/search/", {"q": "palette", "in": "nope", "format": "json"}).status_code == 400
    assert c.get("/search/", {"q": "palette", "format": "json"}).json() == body  # same answer again
