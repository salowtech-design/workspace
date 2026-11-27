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
def test_composer_lists_members_and_escapes_names():
    a, b, c, d, sp = _group()
    r = _c(a).get(reverse("chat:space", args=[sp.public_id]))
    html = r.content.decode()
    assert 'data-mention="@MN-B"' in html and 'data-mention="@MN-C"' in html
    assert 'data-mention="@MN-A"' not in html and "MN-D" not in html
