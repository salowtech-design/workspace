import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.chat import services as cs
from apps.chat.models import Member

HOST = "app.salowtech.com"


def _u(eid, status="active"):
    return CustomUser.objects.create(
        employee_id=eid, full_name=f"Person {eid}", email=f"{eid.lower()}@salowtech.com",
        role="employee", status=status, is_active=True,
    )  # fmt: skip


def _c(u):
    c = Client(HTTP_HOST=HOST)
    c.force_login(u)
    return c


def _get(u, **q):
    return _c(u).get("/search/", q, follow=False)


def _seed():
    a, b, c, d = _u("CS-A"), _u("CS-B"), _u("CS-C"), _u("CS-D")
    grp = cs.create_group(a, "Launch crew", [b.pk, c.pk])
    m1 = cs.post_message(a, grp.public_id, "The quokka launch is on Friday", "cid-1")
    m2 = cs.post_message(b, grp.public_id, "Reply about the QUOKKA plan", "cid-2", m1.seq)
    dm = cs.open_dm(a, d.pk)
    m3 = cs.post_message(a, dm.public_id, "private quokka note for D", "cid-3")
    return a, b, c, d, grp, dm, m1, m2, m3


@pytest.mark.django_db
def test_members_find_chat_text_case_insensitively_with_the_right_link():
    a, b, c, d, grp, dm, m1, m2, m3 = _seed()
    body = _get(b, q="quokka", **{"in": "chat"}).content.decode()
    assert "<h3>Chat</h3>" in body
    assert f'href="/chat/space/{grp.public_id}/"' in body  # the root message opens the conversation
    assert f'href="/chat/space/{grp.public_id}/thread/{m1.seq}/"' in body  # the reply opens its thread
    assert "Launch crew: The quokka launch is on Friday" in body
    assert "private quokka note" not in body  # B is not in the DM


@pytest.mark.django_db
def test_direct_message_hit_is_labelled_with_the_other_person_and_only_the_two_find_it():
    a, b, c, d, grp, dm, m1, m2, m3 = _seed()
    assert "Person CS-D: private quokka note for D" in _get(a, q="private quokka", **{"in": "chat"}).content.decode()
    assert "Person CS-A: private quokka note for D" in _get(d, q="private quokka", **{"in": "chat"}).content.decode()
    for outsider in (b, c):
        assert "note for D" not in _get(outsider, q="private quokka", **{"in": "chat"}).content.decode()


@pytest.mark.django_db
def test_a_person_who_left_or_a_non_member_finds_nothing_and_everywhere_includes_chat():
    a, b, c, d, grp, dm, m1, m2, m3 = _seed()
    assert "launch is on Friday" in _get(c, q="quokka").content.decode()  # Everywhere includes Chat
    Member.objects.filter(space=grp, person=c).update(left_at=timezone.now())
    body = _get(c, q="quokka", **{"in": "chat"}).content.decode()
    assert "Launch crew" not in body and "quokka launch" not in body
    assert "launch is on Friday" not in _get(d, q="launch", **{"in": "chat"}).content.decode()  # never a member


@pytest.mark.django_db
def test_suspended_member_finds_nothing_and_hit_opens_only_for_members():
    a, b, c, d, grp, dm, m1, m2, m3 = _seed()
    CustomUser.objects.filter(pk=b.pk).update(status="suspended")
    b.refresh_from_db()
    assert "launch is on Friday" not in _get(b, q="launch", **{"in": "chat"}).content.decode()
    # the link a member gets opens for a member and not for an outsider
    assert _c(a).get(f"/chat/space/{grp.public_id}/").status_code == 200
    assert _c(d).get(f"/chat/space/{grp.public_id}/").status_code in (302, 403, 404)


@pytest.mark.django_db
def test_like_characters_are_literal_and_short_queries_are_refused():
    a, b, c, d, grp, dm, m1, m2, m3 = _seed()
    cs.post_message(a, grp.public_id, "50% done and a_b", "cid-9")
    assert "50% done" in _get(a, q="50%", **{"in": "chat"}).content.decode()
    assert "launch is on Friday" not in _get(a, q="%%", **{"in": "chat"}).content.decode()
    assert _get(a, q="q", **{"in": "chat"}).status_code == 400
