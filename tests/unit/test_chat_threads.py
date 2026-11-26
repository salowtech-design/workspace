import uuid

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.urls import reverse

from apps.chat import services as cs
from apps.chat.models import Message
from tests.unit.test_chat import _c, _u


def _dm():
    a, b = _u("TH-A"), _u("TH-B")
    sp = cs.open_dm(a, b.pk)
    cs.post_message(a, sp.public_id, "root text", "c1")
    return a, b, sp


@pytest.mark.django_db
def test_reply_is_stored_in_the_same_space_in_order_and_shows_in_the_timeline():
    a, b, sp = _dm()
    r = cs.post_message(b, sp.public_id, "a reply", "c2", 1)
    assert (r.seq, r.thread_root_seq, r.space_id) == (2, 1, sp.pk)
    plain = cs.post_message(b, sp.public_id, "not a reply", "c3")
    assert plain.thread_root_seq is None
    _sp, rows, _more = cs.messages_after(a, sp.public_id, 0)
    assert [m.seq for m in rows] == [1, 2, 3]  # replies are in the conversation, in order
    listed = {s["space"].pk: s for s in cs.spaces_for(a)}[sp.pk]
    assert listed["unread"] == 2  # a reply counts as unread like any message (unchanged rule)


@pytest.mark.django_db
def test_audience_is_only_the_conversation_members():
    a, b, sp = _dm()
    c = _u("TH-C")
    with pytest.raises(Http404):
        cs.post_message(c, sp.public_id, "sneaky", "x1", 1)
    cs.post_message(b, sp.public_id, "ok", "c2", 1)
    with pytest.raises(Http404):
        cs.thread(c, sp.public_id, 1)
    assert Message.objects.filter(space=sp).count() == 2


@pytest.mark.django_db
def test_root_must_be_a_first_level_message_of_this_space():
    a, b, sp = _dm()
    d = _u("TH-D")
    other = cs.open_dm(a, d.pk)
    cs.post_message(a, other.public_id, "other root", "o1")
    cs.post_message(b, sp.public_id, "reply", "c2", 1)
    for bad in (2, 99, 0, -1, "x", 2**70, 1.5, True, "1.5", "-1", " 1"):
        with pytest.raises(ValidationError):
            cs.post_message(a, sp.public_id, "bad " + str(bad)[:5], "b" + str(abs(hash(str(bad)))), bad)
    # root 1 exists in BOTH spaces (seq is per space); a reply in the other space attaches there only
    r = cs.post_message(d, other.public_id, "reply there", "o2", 1)
    assert r.space_id == other.pk
    assert Message.objects.filter(space=sp, thread_root_seq=1).count() == 1
    with pytest.raises(ValidationError):
        cs.post_message(a, other.public_id, "to a missing one", "o3", 5)
    # a root that exists ONLY in the other space (seq 3 there, none here) must be refused here
    cs.post_message(a, other.public_id, "other root 3", "o4")
    assert Message.objects.filter(space=other, seq=3, thread_root_seq__isnull=True).exists()
    assert not Message.objects.filter(space=sp, seq=3).exists()
    with pytest.raises(ValidationError):
        cs.post_message(a, sp.public_id, "wrong space", "o5", 3)


@pytest.mark.django_db
def test_replay_returns_the_original_and_a_changed_root_is_a_conflict():
    a, b, sp = _dm()
    first = cs.post_message(b, sp.public_id, "same", "c2", 1)
    again = cs.post_message(b, sp.public_id, "same", "c2", "1")
    assert again.pk == first.pk and Message.objects.filter(space=sp).count() == 2
    with pytest.raises(cs.ReplayConflict):
        cs.post_message(b, sp.public_id, "same", "c2", None)
    assert Message.objects.filter(space=sp).count() == 2


@pytest.mark.django_db
def test_thread_page_lists_root_and_replies_and_counts():
    a, b, sp = _dm()
    cs.post_message(b, sp.public_id, "r1", "c2", 1)
    cs.post_message(a, sp.public_id, "r2", "c3", 1)
    _sp, root, replies, more = cs.thread(a, sp.public_id, 1)
    assert root.seq == 1 and [m.body for m in replies] == ["r1", "r2"]
    rows = cs.attach_threads(sp, list(Message.objects.filter(space=sp).order_by("seq")))
    assert rows[0].reply_count == 2 and rows[1].root.seq == 1 and rows[1].reply_count == 0
    with pytest.raises(Http404):
        cs.thread(a, sp.public_id, 2)  # a reply is not a root
    with pytest.raises(Http404):
        cs.thread(a, uuid.uuid4(), 1)


@pytest.mark.django_db
def test_pages_render_reply_link_marker_and_form_without_script_injection():
    a, b, sp = _dm()
    cs.post_message(b, sp.public_id, "<b>x</b> reply", "c2", 1)
    c = _c(a)
    page = c.get(reverse("chat:space", args=[sp.public_id]) + "?reply=1").content.decode()
    assert 'name="thread_root_seq" value="1"' in page and "Reply in thread" in page and "1 reply" in page
    assert "&lt;b&gt;x&lt;/b&gt; reply" in page and "<b>x</b>" not in page
    tp = c.get(reverse("chat:thread", args=[sp.public_id, 1])).content.decode()
    assert "Send reply" in tp and "root text" in tp
    r = c.post(reverse("chat:send", args=[sp.public_id]), {"body": "via form", "client_msg_id": "f1", "thread_root_seq": "1"})
    assert r.status_code == 302 and Message.objects.get(body="via form").thread_root_seq == 1
    r = c.post(reverse("chat:send", args=[sp.public_id]), {"body": "bad", "client_msg_id": "f2", "thread_root_seq": "2"})
    assert r.status_code == 400 and not Message.objects.filter(body="bad").exists()
    assert c.get(reverse("chat:thread", args=[sp.public_id, 2])).status_code == 404


@pytest.mark.django_db
def test_a_long_thread_is_reachable_in_pages_with_nothing_skipped(monkeypatch):
    a, b, sp = _dm()
    monkeypatch.setattr(cs, "THREAD_MAX", 3)
    for i in range(7):
        cs.post_message(b, sp.public_id, "r%d" % i, "L%d" % i, 1)
    seen, after, pages = [], 0, 0
    for _ in range(10):  # bounded, so a broken pager fails instead of hanging
        _sp, _root, rep, more = cs.thread(a, sp.public_id, 1, after)
        seen += [m.body for m in rep]
        pages += 1
        if not more:
            break
        after = rep[-1].seq
    assert seen == ["r%d" % i for i in range(7)] and pages == 3


@pytest.mark.django_db
def test_thread_page_never_polls_and_never_moves_the_read_mark(monkeypatch):
    from apps.chat.models import Member

    a, b, sp = _dm()
    for i in range(3):
        cs.post_message(a, sp.public_id, "unrelated-%d" % i, "u%d" % i)
    cs.post_message(a, sp.public_id, "rep", "ur", 1)
    before = Member.objects.get(space=sp, person=b).last_read_seq
    assert before < sp.last_seq + 5 and before == 0
    c = _c(b)
    page = c.get(reverse("chat:thread", args=[sp.public_id, 1])).content.decode()
    assert "data-poll" not in page and "data-first" not in page and "data-last" not in page
    assert "unrelated-" not in page  # unrelated conversation messages are not on the thread page
    assert Member.objects.get(space=sp, person=b).last_read_seq == before


@pytest.mark.django_db
def test_thread_pages_over_http_and_a_reply_returns_to_the_page_that_holds_it(monkeypatch):
    a, b, sp = _dm()
    monkeypatch.setattr(cs, "THREAD_MAX", 2)
    c = _c(a)
    url = reverse("chat:thread", args=[sp.public_id, 1])
    for i in range(5):
        r = c.post(reverse("chat:send", args=[sp.public_id]), {"body": "rr%d" % i, "client_msg_id": "h%d" % i, "thread_root_seq": "1"})
        assert r.status_code == 302 and r["Location"].startswith(url)
    last = Message.objects.get(body="rr4")
    assert r["Location"] == url + "?after=%d" % Message.objects.get(body="rr3").seq + "#m%d" % last.seq
    got = []
    nxt = url
    for _ in range(5):
        html = c.get(nxt).content.decode()
        got += [x for x in ("rr0", "rr1", "rr2", "rr3", "rr4") if ">" + x + "<" in html]
        import re

        m = re.search(r'href="([^"]+)" class="chat-thread-more"', html)
        if not m:
            break
        nxt = m.group(1)
    assert got == ["rr0", "rr1", "rr2", "rr3", "rr4"]


@pytest.mark.django_db
def test_oversized_root_and_after_values_on_the_read_path_never_error():
    a, b, sp = _dm()
    cs.post_message(b, sp.public_id, "reply", "c2", 1)
    c = _c(a)
    big = 10**30
    base = reverse("chat:thread", args=[sp.public_id, 1])
    assert c.get(reverse("chat:thread", args=[sp.public_id, big])).status_code == 404
    assert c.get(reverse("chat:thread", args=[sp.public_id, 2**63])).status_code == 404
    for after in (str(big), str(2**63), str(2**63 - 1), "-5", "abc", ""):
        r = c.get(base + "?after=" + after)
        assert r.status_code == 200, after
    assert "reply" in c.get(base + "?after=-5").content.decode()  # a negative position means the start
    assert "A reply" not in c.get(base + "?after=" + str(big)).content.decode()  # past the end: no replies
