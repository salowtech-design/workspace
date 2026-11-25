import threading

import pytest
from django.core.exceptions import ValidationError
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
            lambda w=who: cs.latest_messages(w, sp.public_id),
            lambda w=who: cs.post_message(w, sp.public_id, "x", "z"),
            lambda w=who: cs.mark_read(w, sp.public_id, 1),
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
    cs.mark_read(b, g.public_id, 2)
    cs.mark_read(b, g.public_id, 1)  # backwards: ignored
    cs.mark_read(b, g.public_id, 99)  # past the end: capped
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
