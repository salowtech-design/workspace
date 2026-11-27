import pytest
from django.urls import reverse

from apps.chat import services as cs
from apps.core.models import Notification
from tests.unit.test_chat import _c, _u


def _group():
    a, b, c, d = _u("MN-A"), _u("MN-B"), _u("MN-C"), _u("MN-D")
    sp = cs.create_group(a, "Ops team", [b.pk, c.pk])
    return a, b, c, d, sp


def _n(u):
    return list(Notification.objects.filter(user=u, kind="chat_mention"))


@pytest.mark.django_db
def test_mention_notifies_that_member_only_without_the_message_text():
    a, b, c, d, sp = _group()
    cs.post_message(a, sp.public_id, "secret plan for @mn-b only", "m1")
    nb = _n(b)
    assert len(nb) == 1 and _n(c) == [] and _n(a) == [] and _n(d) == []
    assert nb[0].title == "Person MN-A mentioned you in Ops team"
    assert "secret" not in nb[0].title and "plan" not in nb[0].title
    assert nb[0].link == reverse("chat:space", args=[sp.public_id]) + "#m1"


@pytest.mark.django_db
def test_plain_message_notifies_nobody():
    a, b, c, d, sp = _group()
    cs.post_message(a, sp.public_id, "hello everyone, mail me at x@MN-B.com", "m1")
    assert Notification.objects.count() == 0  # an email-like @ is not a mention (a letter precedes the @)


@pytest.mark.django_db
def test_non_member_self_left_and_suspended_get_nothing():
    a, b, c, d, sp = _group()
    cs.post_message(a, sp.public_id, "@MN-D @MN-A", "m1")  # d is not in the space; a is the author
    assert _n(d) == [] and _n(a) == []
    from apps.chat.models import Member
    Member.objects.filter(space=sp, person=c).update(left_at="2026-01-01T00:00:00Z")
    b.status = "suspended"
    b.save()
    cs.post_message(a, sp.public_id, "@MN-B @MN-C", "m2")
    assert _n(b) == [] and _n(c) == []


@pytest.mark.django_db
def test_retry_with_same_client_id_does_not_notify_twice_and_dupes_in_text_once():
    a, b, c, d, sp = _group()
    cs.post_message(a, sp.public_id, "@MN-B @mn-b @MN-B", "m1")
    cs.post_message(a, sp.public_id, "@MN-B @mn-b @MN-B", "m1")
    assert len(_n(b)) == 1


@pytest.mark.django_db
def test_thread_reply_link_opens_the_thread():
    a, b, c, d, sp = _group()
    cs.post_message(a, sp.public_id, "root", "r1")
    cs.post_message(a, sp.public_id, "ping @MN-C", "r2", 1)
    n = _n(c)[0]
    assert n.link == reverse("chat:thread", args=[sp.public_id, 1]) + "#m2"


@pytest.mark.django_db
def test_dm_title_does_not_name_a_group_and_notice_opens_only_for_the_member():
    a, b = _u("MN-E"), _u("MN-F")
    sp = cs.open_dm(a, b.pk)
    cs.post_message(a, sp.public_id, "hi @MN-F", "d1")
    n = _n(b)[0]
    assert n.title == "Person MN-E mentioned you in a direct message"
    r = _c(b).post(reverse("inbox:open", args=[n.pk]))
    assert r.status_code == 302 and r["Location"].startswith(reverse("chat:space", args=[sp.public_id]))
    assert _c(a).post(reverse("inbox:open", args=[n.pk])).status_code == 404  # someone else's notice


@pytest.mark.django_db
def test_notice_failure_never_blocks_the_message(monkeypatch):
    a, b, c, d, sp = _group()
    monkeypatch.setattr(cs, "notify", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    m = cs.post_message(a, sp.public_id, "@MN-B", "m1")
    assert m.seq == 1 and _n(b) == []


@pytest.mark.django_db
def test_twenty_first_distinct_mention_is_silently_not_notified():
    a = _u("CAP-A")
    others = [_u("CAP-%02d" % i) for i in range(22)]
    sp = cs.create_group(a, "Big", [o.pk for o in others])
    cs.post_message(a, sp.public_id, " ".join("@CAP-%02d" % i for i in range(22)), "m1")
    got = [o for o in others if _n(o)]
    assert [o.employee_id for o in got] == ["CAP-%02d" % i for i in range(20)]  # first 20 in text order, no error shown


@pytest.mark.django_db
def test_group_composer_offers_only_people_who_wrote_and_dm_offers_the_other_person():
    a, b, c, d, sp = _group()
    html = lambda u: _c(u).get(reverse("chat:space", args=[sp.public_id])).content.decode()
    assert 'data-mention="@MN-B"' not in html(a)  # b is a member but has not written: no member list is revealed
    cs.post_message(b, sp.public_id, "hello", "b1")
    h = html(a)
    assert 'data-mention="@MN-B"' in h and 'data-mention="@MN-C"' not in h and 'data-mention="@MN-A"' not in h
    assert "MN-D" not in h  # not a member
    x, y = _u("MN-X"), _u("MN-Y")
    dm = cs.open_dm(x, y.pk)
    assert 'data-mention="@MN-Y"' in _c(x).get(reverse("chat:space", args=[dm.public_id])).content.decode()


@pytest.mark.django_db
def test_hostile_display_name_is_escaped_in_the_mention_button():
    a, b, c, d, sp = _group()
    b.full_name = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    b.save()
    cs.post_message(b, sp.public_id, "hi", "b1")
    h = _c(a).get(reverse("chat:space", args=[sp.public_id])).content.decode()
    assert "<script>alert(1)</script>" not in h and "<img src=x" not in h
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in h


@pytest.mark.django_db
def test_recipient_who_left_or_was_suspended_after_the_mention_cannot_read_anything_through_the_notice():
    from apps.chat.models import Member

    a, b, c, d, sp = _group()
    cs.post_message(a, sp.public_id, "the private text @MN-B @MN-C", "m1")
    nb, nc = _n(b)[0], _n(c)[0]
    # the notice stores only the title and a link, never the text
    assert "private" not in nb.title and "private" not in nb.link
    Member.objects.filter(space=sp, person=b).update(left_at="2026-01-01T00:00:00Z")
    r = _c(b).post(reverse("inbox:open", args=[nb.pk]))
    assert r.status_code == 302 and r["Location"].startswith(reverse("chat:space", args=[sp.public_id]))
    page = _c(b).get(r["Location"])
    assert page.status_code == 404 and b"private text" not in page.content  # the destination refuses on its own check
    c.status = "suspended"
    c.save()
    r2 = _c(c).post(reverse("inbox:open", args=[nc.pk]))  # a suspended account has no session: sent to sign-in, never to the conversation
    assert r2.status_code in (302, 404) and "/chat/" not in r2.get("Location", "")
    r3 = _c(c).get(reverse("chat:space", args=[sp.public_id]))
    assert r3.status_code in (302, 403, 404) and b"private text" not in r3.content
