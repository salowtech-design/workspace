import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.chat import services as cs
from apps.chat.models import DmPair, Member, Message, Space

HOST = "app.salowtech.com"


def _u(eid, status="active", role="employee"):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role=role, status=status, is_active=True,
    )  # fmt: skip


def _c(u):
    c = Client(HTTP_HOST=HOST)
    c.force_login(u)
    return c


@pytest.mark.django_db
def test_dm_is_unique_per_pair_and_order_is_per_space_sequence():
    a, b = _u("CH-A"), _u("CH-B")
    s1, s2 = cs.open_dm(a, b.pk), cs.open_dm(b, a.pk)
    assert s1.pk == s2.pk and DmPair.objects.count() == 1
    m1 = cs.post_message(a, s1.public_id, "hello", "c1")
    m2 = cs.post_message(b, s1.public_id, "hi", "c2")
    assert (m1.seq, m2.seq) == (1, 2)
    with pytest.raises(ValidationError):
        cs.open_dm(a, a.pk)


@pytest.mark.django_db
def test_retry_with_same_client_id_gives_one_message_and_limits_apply():
    a, b = _u("CH-A"), _u("CH-B")
    sp = cs.open_dm(a, b.pk)
    x = cs.post_message(a, sp.public_id, "once", "same")
    y = cs.post_message(a, sp.public_id, "once", "same")
    assert x.pk == y.pk and Message.objects.count() == 1
    for bad in ("", "   ", "x" * 8001):
        with pytest.raises(ValidationError):
            cs.post_message(a, sp.public_id, bad, "n1")
    assert cs.post_message(a, sp.public_id, "x" * 8000, "n2").seq == 2


@pytest.mark.django_db
def test_non_member_gets_404_everywhere_even_an_admin_and_unknown_looks_the_same():
    import uuid

    a, b, c = _u("CH-A"), _u("CH-B"), _u("CH-C")
    admin = _u("CH-ADM", role="super_admin")
    sp = cs.open_dm(a, b.pk)
    cs.post_message(a, sp.public_id, "private words", "k")
    for who in (c, admin):
        for call in (
            lambda w=who: cs.messages_after(w, sp.public_id),
            lambda w=who: cs.window(w, sp.public_id),
            lambda w=who: cs.post_message(w, sp.public_id, "x", "z"),
            lambda w=who: cs.mark_read(w, sp.public_id, 1, 1),
        ):
            with pytest.raises(Http404):
                call()
    r = _c(c).get(reverse("chat:space", args=[sp.public_id]))
    r2 = _c(c).get(reverse("chat:space", args=[uuid.uuid4()]))
    assert r.status_code == r2.status_code == 404 and b"private words" not in r.content
    assert Message.objects.count() == 1


@pytest.mark.django_db
def test_groups_unread_catch_up_and_read_marks_never_go_back():
    a, b, c = _u("CH-A"), _u("CH-B"), _u("CH-C")
    g = cs.create_group(a, "Team", [b.pk, c.pk])
    for i in range(3):
        cs.post_message(a, g.public_id, f"m{i}", f"id{i}")
    assert {s["label"]: s["unread"] for s in cs.spaces_for(b)} == {"Team": 3}
    assert cs.spaces_for(a)[0]["unread"] == 0  # the sender has read their own
    _, rows, more = cs.messages_after(b, g.public_id, after_seq=1)
    assert [m.seq for m in rows] == [2, 3] and not more
    cs.mark_read(b, g.public_id, 2, 1)
    cs.mark_read(b, g.public_id, 1, 1)  # backwards: ignored
    cs.mark_read(b, g.public_id, 99, 1)  # past the end: capped
    assert Member.objects.get(space=g, person=b).last_read_seq == 3
    with pytest.raises(ValidationError):
        cs.create_group(a, "Two", [b.pk])
    with pytest.raises(ValidationError):
        cs.create_group(a, "", [b.pk, c.pk])
    gone = _u("CH-OFF", status="suspended")
    with pytest.raises(ValidationError) as e1:
        cs.create_group(a, "X", [b.pk, gone.pk])
    with pytest.raises(ValidationError) as e2:
        cs.create_group(a, "X", [b.pk, 999999])
    assert e1.value.messages == e2.value.messages  # same words for every cause


@pytest.mark.django_db
def test_views_send_poll_throttle_and_workspace_host_only(monkeypatch):
    a, b = _u("CH-A"), _u("CH-B")
    ca = _c(a)
    r = ca.post(reverse("chat:dm"), {"person": "ch-b"})
    assert r.status_code == 302
    sp = Space.objects.get()
    page = ca.get(r["Location"]).content.decode()
    cid = page.split('name="client_msg_id" value="')[1].split('"')[0]
    send = reverse("chat:send", args=[sp.public_id])
    ca.post(send, {"body": "<b>hi</b>", "client_msg_id": cid})
    ca.post(send, {"body": "<b>hi</b>", "client_msg_id": cid})  # double click
    assert Message.objects.count() == 1
    seen = _c(b).get(reverse("chat:poll", args=[sp.public_id]) + "?after=0").content.decode()
    assert "&lt;b&gt;hi&lt;/b&gt;" in seen and "<b>hi</b>" not in seen
    assert _c(b).get(reverse("chat:poll", args=[sp.public_id]) + "?after=zz").status_code == 200
    assert Client(HTTP_HOST=HOST).get("/chat/").status_code == 302
    assert ca.get("/chat/", HTTP_HOST="iam.salowtech.com").status_code == 404
    from apps.chat import views

    monkeypatch.setattr(views, "throttled", lambda *a, **k: True)
    ca.post(send, {"body": "more", "client_msg_id": "other"}, follow=True)
    assert Message.objects.count() == 1


def _plain_lock(public_id):
    return Space.objects.filter(public_id=public_id).first()  # the control: no row lock


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_two_posts_get_distinct_sequence_numbers(monkeypatch, locked):
    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(cs, "_lock_space", _plain_lock)
    a, b = _u(f"RC-A{locked}"), _u(f"RC-B{locked}")
    sp = cs.open_dm(a, b.pk)
    barrier = threading.Barrier(2)

    def pause():
        try:
            barrier.wait(timeout=1.5 if locked else 10)  # both have read last_seq
        except threading.BrokenBarrierError:
            pass  # locked: the other side is held at the row lock

    monkeypatch.setattr(cs, "_pause", pause)
    out = _threads(
        [
            lambda: cs.post_message(a, sp.public_id, "from a", "ra"),
            lambda: cs.post_message(b, sp.public_id, "from b", "rb"),
        ]
    )
    seqs = sorted(Message.objects.filter(space=sp).values_list("seq", flat=True))
    if locked:
        assert out == ["ok", "ok"] and seqs == [1, 2], (out, seqs)
    else:
        # the anomaly: both took seq 1, the unique key rejected one and its message was lost
        assert sorted(o.split(":")[0] for o in out) == ["error", "ok"], out
        assert "IntegrityError" in "".join(out) and seqs == [1], (out, seqs)


def _many(sender, sp, n, prefix="m"):
    for i in range(n):
        cs.post_message(sender, sp.public_id, f"{prefix}{i}", f"{prefix}{i}")


@pytest.mark.django_db
def test_sending_does_not_mark_unseen_messages_read():
    a, b = _u("CH-A"), _u("CH-B")
    sp = cs.open_dm(a, b.pk)
    _many(a, sp, 3)
    cs.post_message(b, sp.public_id, "reply without opening", "r1")  # b never opened the page
    assert Member.objects.get(space=sp, person=b).last_read_seq == 0
    assert cs.spaces_for(b)[0]["unread"] == 3  # a's three; b's own message is not counted
    # a caught-up sender does move their own mark
    cs.mark_read(b, sp.public_id, 4, 1)
    cs.post_message(b, sp.public_id, "now caught up", "r2")
    assert Member.objects.get(space=sp, person=b).last_read_seq == 5


@pytest.mark.django_db
def test_more_than_a_page_unread_starts_at_the_first_unread_and_reads_only_what_was_shown():
    a, b = _u("CH-A"), _u("CH-B")
    sp = cs.open_dm(a, b.pk)
    _many(a, sp, cs.PAGE + 50)
    _, rows = cs.window(b, sp.public_id)
    assert [m.seq for m in rows] == list(range(1, cs.PAGE + 1))  # not the latest page
    cs.mark_read(b, sp.public_id, rows[-1].seq, rows[0].seq)
    assert Member.objects.get(space=sp, person=b).last_read_seq == cs.PAGE
    assert cs.spaces_for(b)[0]["unread"] == 50
    _, nxt = cs.window(b, sp.public_id, after=cs.PAGE)
    assert [m.seq for m in nxt] == list(range(cs.PAGE + 1, cs.PAGE + 51))
    # history: older pages are reachable and a non-contiguous batch never moves the mark
    _, old = cs.window(b, sp.public_id, before=60)
    assert old[0].seq == 1 and old[-1].seq == 59
    cs.mark_read(b, sp.public_id, cs.PAGE + 50, cs.PAGE + 40)  # a gap: 101..139 never shown
    assert Member.objects.get(space=sp, person=b).last_read_seq == cs.PAGE


@pytest.mark.django_db
def test_list_is_most_recently_active_first_not_by_message_count():
    a, b, c = _u("CH-A"), _u("CH-B"), _u("CH-C")
    busy, quiet = cs.open_dm(a, b.pk), cs.open_dm(a, c.pk)
    _many(a, busy, 5)
    cs.post_message(a, quiet.public_id, "latest word", "q1")
    assert [s["space"].pk for s in cs.spaces_for(a)] == [quiet.pk, busy.pk]
    cs.post_message(b, busy.public_id, "bump", "b-bump")
    assert [s["space"].pk for s in cs.spaces_for(a)] == [busy.pk, quiet.pk]


@pytest.mark.django_db
def test_reused_client_id_with_other_text_is_a_conflict_and_same_text_is_idempotent():
    a, b = _u("CH-A"), _u("CH-B")
    sp = cs.open_dm(a, b.pk)
    cs.post_message(a, sp.public_id, "first", "dup")
    assert cs.post_message(a, sp.public_id, " first ", "dup").seq == 1
    with pytest.raises(cs.ReplayConflict):
        cs.post_message(a, sp.public_id, "second text", "dup")
    assert Message.objects.count() == 1


@pytest.mark.django_db
def test_failed_sends_keep_the_draft_and_a_conflict_gives_a_fresh_form_id(monkeypatch):
    a, b = _u("CH-A"), _u("CH-B")
    sp = cs.open_dm(a, b.pk)
    ca = _c(a)
    send = reverse("chat:send", args=[sp.public_id])
    r = ca.post(send, {"body": "x" * 8001, "client_msg_id": "k1"})
    assert r.status_code == 400 and r.content.decode().count("x" * 8001) == 1
    assert 'name="client_msg_id" value="k1"' in r.content.decode()
    ca.post(send, {"body": "kept words", "client_msg_id": "k2"})
    r = ca.post(send, {"body": "different words", "client_msg_id": "k2"})
    html = r.content.decode()
    assert r.status_code == 409 and "different words" in html and "already used" in html
    assert 'value="k2"' not in html  # a fresh id, so pressing Send posts it as new
    assert Message.objects.filter(body="different words").count() == 0
    from apps.chat import views

    monkeypatch.setattr(views, "throttled", lambda *a, **k: True)
    r = ca.post(send, {"body": "typed while throttled", "client_msg_id": "k3"})
    assert r.status_code == 429 and "typed while throttled" in r.content.decode()
    assert Message.objects.count() == 1


@pytest.mark.django_db
def test_every_route_refuses_an_inactive_person_and_poll_answers_401_when_signed_out():
    a, b = _u("CH-A"), _u("CH-B")
    sp = cs.open_dm(a, b.pk)
    cs.post_message(b, sp.public_id, "hello", "h1")
    ca = _c(a)
    a.status = "suspended"
    a.save()
    sid = sp.public_id
    for method, url in (
        ("get", "/chat/"),
        ("get", f"/chat/space/{sid}/"),
        ("get", f"/chat/space/{sid}/poll/?after=0"),
        ("post", f"/chat/space/{sid}/send/"),
        ("post", "/chat/dm/"),
        ("post", "/chat/group/"),
    ):
        # the site-wide sign-in gate may answer first (302 to login, or 401 for poll); it must never be a 200
        assert getattr(ca, method)(url).status_code in (302, 401, 404), url
    # the views' own gate, with the site-wide one out of the way
    from django.test import RequestFactory

    from apps.chat import views

    rf = RequestFactory()
    for fn, req, kw in (
        (views.home, rf.get("/"), {}),
        (views.space_view, rf.get("/"), {"space_id": sid}),
        (views.poll, rf.get("/"), {"space_id": sid}),
        (views.send, rf.post("/", {"body": "x", "client_msg_id": "z"}), {"space_id": sid}),
        (views.start_dm, rf.post("/", {"person": "CH-B"}), {}),
        (views.new_group, rf.post("/", {"name": "n", "people": "CH-B"}), {}),
    ):
        req.user = a
        with pytest.raises(Http404):
            fn(req, **kw)
    anon = Client(HTTP_HOST=HOST)
    r = anon.get(f"/chat/space/{sid}/poll/?after=0")
    assert r.status_code == 401 and b"<html" not in r.content


@pytest.mark.django_db
def test_poll_marks_read_only_when_contiguous_and_is_not_cached():
    a, b = _u("CH-A"), _u("CH-B")
    sp = cs.open_dm(a, b.pk)
    _many(a, sp, 3)
    cb = _c(b)
    url = f"/chat/space/{sp.public_id}/poll/"
    r = cb.get(url + "?after=1")  # b never displayed message 1: the mark must not jump
    assert r["X-Chat-Last"] == "3" and r["Cache-Control"] == "no-store"
    assert Member.objects.get(space=sp, person=b).last_read_seq == 0
    cb.get(url + "?after=0")
    assert Member.objects.get(space=sp, person=b).last_read_seq == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("fallback", [True, False])
def test_race_two_people_opening_the_same_dm_get_one_space(monkeypatch, fallback):
    from django.db import connection

    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("concurrent writers are only meaningful on the TiDB/MySQL backend")
    monkeypatch.setattr(cs, "_CATCH_DM_DUPLICATE", fallback)
    a, b = _u(f"RD-A{fallback}"), _u(f"RD-B{fallback}")
    barrier = threading.Barrier(2)

    def pause():
        try:
            barrier.wait(timeout=10)  # both looked and found no DM yet
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr(cs, "_pause_dm", pause)
    got = []
    out = _threads(
        [
            lambda: got.append(cs.open_dm(a, b.pk).pk),
            lambda: got.append(cs.open_dm(b, a.pk).pk),
        ]
    )
    if fallback:
        assert out == ["ok", "ok"] and len(set(got)) == 1, (out, got)
    else:
        # the anomaly the fallback absorbs: the loser fails on the unique pair key or is chosen as the deadlock victim
        assert sorted(o.split(":")[0] for o in out) == ["error", "ok"], out
        assert "IntegrityError" in "".join(out) or "1213" in "".join(out), out
    assert DmPair.objects.count() == 1 and Space.objects.filter(kind="dm").count() == 1


@pytest.mark.django_db
def test_an_unrelated_integrity_failure_while_opening_a_dm_is_not_swallowed(monkeypatch):
    a, b = _u("CH-A"), _u("CH-B")
    monkeypatch.setattr(cs, "_DM_WAIT", (2, 0.0))

    def boom(*args, **kwargs):
        raise IntegrityError("some other constraint")

    monkeypatch.setattr(cs.Member.objects, "bulk_create", boom)
    with pytest.raises(IntegrityError):
        cs.open_dm(a, b.pk)
    assert Space.objects.count() == 0 and DmPair.objects.count() == 0
