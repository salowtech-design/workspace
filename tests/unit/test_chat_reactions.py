import threading

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.urls import reverse

from apps.chat import services as cs
from apps.chat.models import REACTION_KEYS, Message, Reaction
from tests.unit.test_chat import _c, _plain_lock, _u


def _dm():
    a, b = _u("RX-A"), _u("RX-B")
    sp = cs.open_dm(a, b.pk)
    cs.post_message(a, sp.public_id, "hello", "c1")
    return a, b, sp


@pytest.mark.django_db
def test_react_toggles_and_each_person_counts_once_per_kind():
    a, b, sp = _dm()
    assert cs.react(a, sp.public_id, 1, "thumbs_up") is True
    assert cs.react(b, sp.public_id, 1, "thumbs_up") is True
    assert cs.react(a, sp.public_id, 1, "heart") is True
    assert Reaction.objects.count() == 3
    assert cs.react(a, sp.public_id, 1, "thumbs_up") is False  # the same press again removes it
    assert Reaction.objects.filter(kind="thumbs_up").count() == 1
    row = cs.attach_reactions(Message.objects.filter(space=sp), b)[0]
    got = {k: (n, mine) for k, _e, _l, n, mine in row.reacts}
    assert got["thumbs_up"] == (1, True) and got["heart"] == (1, False) and got["tada"] == (0, False)
    assert [k for k, *_ in row.reacts] == list(REACTION_KEYS)


@pytest.mark.django_db
def test_only_the_fixed_set_is_accepted_and_nothing_is_stored_otherwise():
    a, b, sp = _dm()
    for bad in ("", "x", "THUMBS_UP", "\U0001f44d", "thumbs_up ", "a" * 200, None):
        with pytest.raises(ValidationError):
            cs.react(a, sp.public_id, 1, bad)
    assert Reaction.objects.count() == 0


@pytest.mark.django_db
def test_non_member_unknown_space_unknown_message_and_bad_seq_all_404():
    a, b, sp = _dm()
    c = _u("RX-C")
    for who, space, seq in (
        (c, sp.public_id, 1),
        (a, sp.public_id, 99),
        (a, sp.public_id, "x"),
        (a, sp.public_id, None),
    ):
        with pytest.raises(Http404):
            cs.react(who, space, seq, "thumbs_up")
    import uuid

    with pytest.raises(Http404):
        cs.react(a, uuid.uuid4(), 1, "thumbs_up")
    assert Reaction.objects.count() == 0
    # a message of ANOTHER space cannot be reached through this space's id
    s2 = cs.open_dm(a, c.pk)
    cs.post_message(a, s2.public_id, "other", "o1")
    cs.post_message(a, s2.public_id, "other2", "o2")
    with pytest.raises(Http404):
        cs.react(b, sp.public_id, 2, "heart")  # seq 2 exists only in the other space


@pytest.mark.django_db
def test_a_person_who_left_or_is_inactive_cannot_react():
    from django.utils import timezone

    from apps.chat.models import Member

    a, b, sp = _dm()
    Member.objects.filter(space=sp, person=b).update(left_at=timezone.now())
    with pytest.raises(Http404):
        cs.react(b, sp.public_id, 1, "heart")
    a.status = "suspended"
    a.save()
    with pytest.raises(Http404):
        cs.react(a, sp.public_id, 1, "heart")
    assert Reaction.objects.count() == 0


@pytest.mark.django_db
def test_view_shows_counts_and_my_state_and_post_toggles_it():
    a, b, sp = _dm()
    ca, cb = _c(a), _c(b)
    url = reverse("chat:react", args=[sp.public_id])
    r = ca.post(url, {"seq": 1, "kind": "tada"})
    assert r.status_code == 302 and r["Location"].endswith(f"/space/{sp.public_id}/#m1")
    cb.post(url, {"seq": 1, "kind": "tada"})
    html = ca.get(reverse("chat:space", args=[sp.public_id])).content.decode()
    assert 'aria-label="Celebrate, 2, you reacted"' in html and 'aria-pressed="true"' in html
    assert 'aria-label="Heart"' in html
    ca.post(url, {"seq": 1, "kind": "tada"})
    html = ca.get(reverse("chat:space", args=[sp.public_id])).content.decode()
    assert 'aria-label="Celebrate, 1"' in html
    poll = cb.get(reverse("chat:poll", args=[sp.public_id]) + "?after=0").content.decode()
    assert "chat-react" in poll and 'aria-label="Celebrate, 1, you reacted"' in poll


@pytest.mark.django_db
def test_view_refuses_bad_input_without_storing_and_hides_the_space_from_outsiders():
    a, b, sp = _dm()
    c = _u("RX-C")
    url = reverse("chat:react", args=[sp.public_id])
    assert _c(a).post(url, {"seq": 1, "kind": "nope"}).status_code == 302
    assert _c(a).post(url, {"seq": "abc", "kind": "heart"}).status_code == 404
    assert _c(c).post(url, {"seq": 1, "kind": "heart"}).status_code == 404
    assert _c(a).get(url).status_code == 405
    assert Reaction.objects.count() == 0
    from django.test import Client

    anon = Client(HTTP_HOST="app.salowtech.com", enforce_csrf_checks=True)
    assert anon.post(url, {"seq": 1, "kind": "heart"}).status_code in (302, 403)
    assert Reaction.objects.count() == 0


@pytest.mark.django_db
def test_reaction_rate_limit(monkeypatch):
    from apps.chat import views

    a, b, sp = _dm()
    monkeypatch.setattr(views, "REACT_LIMIT", 2)
    url = reverse("chat:react", args=[sp.public_id])
    c = _c(a)
    for k in ("heart", "laugh", "pray"):
        c.post(url, {"seq": 1, "kind": k})
    assert Reaction.objects.filter(person=a).count() == 2  # the third press was refused


@pytest.mark.django_db
def test_wanted_state_makes_a_repeated_or_stale_press_a_no_op():
    a, b, sp = _dm()
    assert cs.react(a, sp.public_id, 1, "heart", True) is True
    assert cs.react(a, sp.public_id, 1, "heart", True) is True  # a second tab pressing "on" again
    assert Reaction.objects.filter(person=a).count() == 1
    assert cs.react(a, sp.public_id, 1, "heart", False) is False
    assert cs.react(a, sp.public_id, 1, "heart", False) is False
    assert Reaction.objects.count() == 0
    assert cs.react(a, sp.public_id, 1, "heart") is True  # no wanted state still toggles


@pytest.mark.django_db
def test_fetch_post_answers_json_without_a_redirect_and_the_form_post_still_redirects():
    a, b, sp = _dm()
    c = _c(a)
    url = reverse("chat:react", args=[sp.public_id])
    r = c.post(url, {"seq": 1, "kind": "heart", "want": "1"}, HTTP_X_CHAT_FETCH="1")
    assert r.status_code == 200 and r.json()["seq"] == 1
    got = {k: (n, mine) for k, n, mine in r.json()["reacts"]}
    assert got["heart"] == (1, True) and got["tada"] == (0, False)
    r = c.post(url, {"seq": 1, "kind": "heart", "want": "1"}, HTTP_X_CHAT_FETCH="1")
    assert r.json()["reacts"][1] == ["heart", 1, True]  # pressed again with want on: unchanged
    assert c.post(url, {"seq": 1, "kind": "bogus"}, HTTP_X_CHAT_FETCH="1").status_code == 400
    assert c.post(url, {"seq": 9, "kind": "heart"}, HTTP_X_CHAT_FETCH="1").status_code == 404
    assert c.post(url, {"seq": 1, "kind": "heart", "want": "0"}).status_code == 302
    assert Reaction.objects.count() == 0


@pytest.mark.django_db
def test_reactions_refresh_endpoint_membership_window_and_privacy():
    a, b, sp = _dm()
    cs.post_message(b, sp.public_id, "second", "c2")
    cs.react(b, sp.public_id, 1, "eyes")
    cs.react(a, sp.public_id, 2, "heart")
    url = reverse("chat:reactions", args=[sp.public_id])
    r = _c(a).get(url + "?first=1&last=2")
    j = r.json()["reacts"]
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    assert [x for x in j["1"] if x[0] == "eyes"] == [["eyes", 1, False]]
    assert [x for x in j["2"] if x[0] == "heart"] == [["heart", 1, True]]
    assert "RX-B" not in r.content.decode() and "person" not in r.content.decode()  # counts only, no names
    assert list(_c(a).get(url + "?first=2&last=2").json()["reacts"]) == ["2"]
    assert _c(a).get(url).status_code == 400
    assert _c(a).get(url + "?first=5&last=2").status_code == 400
    assert _c(_u("RX-C")).get(url + "?first=1&last=2").status_code == 404
    from django.test import Client

    assert Client(HTTP_HOST="app.salowtech.com").get(url + "?first=1&last=2").status_code == 401
    big = _c(a).get(url + "?first=1&last=100000")
    assert big.status_code == 200 and set(big.json()["reacts"]) == {"1", "2"}


@pytest.mark.django_db
def test_react_routes_are_not_served_on_the_iam_host():
    a, b, sp = _dm()
    c = _c(a)
    for path in (f"/chat/space/{sp.public_id}/react/", f"/chat/space/{sp.public_id}/reactions/?first=1&last=1"):
        assert c.post(path, {"seq": 1, "kind": "heart"}, HTTP_HOST="iam.salowtech.com").status_code == 404
        assert c.get(path, HTTP_HOST="iam.salowtech.com").status_code == 404


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_one_person_pressing_twice_at_once(monkeypatch, locked):
    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(cs, "_lock_space", _plain_lock)
    a, b, sp = _dm()
    barrier = threading.Barrier(2)

    def pause():
        try:
            barrier.wait(timeout=1.5 if locked else 10)  # both have seen "no reaction yet"
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr(cs, "_pause", pause)
    out = _threads([lambda: cs.react(a, sp.public_id, 1, "heart"), lambda: cs.react(a, sp.public_id, 1, "heart")])
    n = Reaction.objects.filter(person=a, kind="heart").count()
    if locked:
        assert out == ["ok", "ok"] and n == 0, (out, n)  # one added, the other removed it: two presses, net none
    else:
        assert "IntegrityError" in "".join(out) and n == 1, (out, n)  # the control: without the lock the unique key rejects one
