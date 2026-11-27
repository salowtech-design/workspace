import pytest
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.chat import services as cs
from apps.chat.models import Member, Message
from apps.inbox.context_processors import CHAT_BADGE_CAP, unread_chat


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip


def _n(user):
    req = RequestFactory().get("/")
    req.user = user
    return unread_chat(req)["unread_chat"]


def _list_total(user):
    return sum(s["unread"] for s in cs.spaces_for(user))


@pytest.mark.django_db
def test_badge_matches_the_chat_list_and_excludes_own_read_left_and_others():
    a, b, c, d = _u("NB-A"), _u("NB-B"), _u("NB-C"), _u("NB-D")
    grp = cs.create_group(a, "Crew", [b.pk, c.pk])
    dm = cs.open_dm(a, b.pk)
    cs.post_message(b, grp.public_id, "one", "n-1")
    cs.post_message(b, grp.public_id, "two", "n-2")
    cs.post_message(c, grp.public_id, "three", "n-3")
    cs.post_message(b, dm.public_id, "dm one", "n-4")
    cs.post_message(a, grp.public_id, "my own", "n-5")  # never counts for A
    assert _n(a) == 4 == _list_total(a)
    # read the DM: its message stops counting
    Member.objects.filter(space=dm, person=a).update(last_read_seq=dm.__class__.objects.get(pk=dm.pk).last_seq)
    assert _n(a) == 3 == _list_total(a)
    # B's own view: A's message in the group counts for B, B's own do not
    assert _n(b) == _list_total(b)
    # leaving the group removes its messages from the count
    Member.objects.filter(space=grp, person=a).update(left_at=timezone.now())
    assert _n(a) == 0 == _list_total(a)
    # someone outside any space has none and does not see A's chats
    assert _n(d) == 0


@pytest.mark.django_db
def test_cap_and_nav_badge_and_no_badge_when_nothing_unread():
    a, b, c = _u("NB-E"), _u("NB-F"), _u("NB-G")
    grp = cs.create_group(a, "Big", [b.pk, c.pk])
    Message.objects.bulk_create(
        [Message(space=grp, seq=i, author=b, body="x", client_msg_id=f"b-{i}") for i in range(1, 121)]
    )
    assert _n(a) == CHAT_BADGE_CAP + 1  # read only up to the cap plus one
    cl = Client(HTTP_HOST="app.salowtech.com")
    cl.force_login(a)
    html = cl.get("/tasks/assigned/").content.decode()
    assert f"Chat ({CHAT_BADGE_CAP}+)" in html
    cl2 = Client(HTTP_HOST="app.salowtech.com")
    cl2.force_login(c)
    Member.objects.filter(space=grp, person=c).update(last_read_seq=120)
    html2 = cl2.get("/tasks/assigned/").content.decode()
    assert "Chat (" not in html2 and ">Chat<" in html2
    # no message text or space name leaks into the nav
    assert "Big" not in html.split("</nav>")[0]
