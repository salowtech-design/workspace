import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.urls import reverse

from apps.chat import services as cs
from apps.chat.models import Message, Space
from apps.core.models import AuditLog, Notification
from tests.unit.test_chat import _c, _u


def _group():
    a, b, c, out = _u("RN-A"), _u("RN-B"), _u("RN-C"), _u("RN-OUT")
    sp = cs.create_group(a, "Old name", [b.pk, c.pk])
    return a, b, c, out, sp


@pytest.mark.django_db
def test_manager_renames_silently_and_it_is_audited():
    a, b, c, out, sp = _group()
    n_msg, n_note = Message.objects.count(), Notification.objects.count()
    cs.rename_group(a, sp.public_id, "  New name  ")
    sp.refresh_from_db()
    assert sp.name == "New name"  # trimmed
    assert (Message.objects.count(), Notification.objects.count()) == (n_msg, n_note)  # no message, no notice
    assert AuditLog.objects.filter(action="chat.group.rename", target_id=str(sp.pk), actor=a).count() == 1
    cs.rename_group(a, sp.public_id, "New name")  # unchanged: no second audit row
    assert AuditLog.objects.filter(action="chat.group.rename").count() == 1


@pytest.mark.django_db
def test_member_is_refused_stranger_gets_404_dm_and_bad_names_are_refused():
    a, b, c, out, sp = _group()
    with pytest.raises(PermissionDenied):
        cs.rename_group(b, sp.public_id, "Mine now")
    with pytest.raises(Http404):
        cs.rename_group(out, sp.public_id, "Mine now")
    for bad in ("", "   ", "x" * 121):
        with pytest.raises(ValidationError):
            cs.rename_group(a, sp.public_id, bad)
    dm = cs.open_dm(a, b.pk)
    with pytest.raises(ValidationError, match="direct message"):
        cs.rename_group(a, dm.public_id, "Nope")
    sp.refresh_from_db()
    assert sp.name == "Old name" and Space.objects.get(pk=dm.pk).name == ""
    assert AuditLog.objects.filter(action="chat.group.rename").count() == 0


@pytest.mark.django_db
def test_pages_form_only_for_manager_and_members_see_the_new_name():
    a, b, c, out, sp = _group()
    url = reverse("chat:space", args=[sp.public_id])
    assert b"Rename group" in _c(a).get(url).content
    assert b"Rename group" not in _c(b).get(url).content  # negative control
    assert b"Rename group" not in _c(a).get(reverse("chat:space", args=[cs.open_dm(a, b.pk).public_id])).content
    r = _c(a).post(reverse("chat:rename", args=[sp.public_id]), {"name": "Renamed"})
    assert r.status_code == 302
    assert b"Renamed" in _c(b).get(reverse("chat:home")).content
    r = _c(b).post(reverse("chat:rename", args=[sp.public_id]), {"name": "Hijack"})
    assert r.status_code == 302
    sp.refresh_from_db()
    assert sp.name == "Renamed"
    assert _c(out).post(reverse("chat:rename", args=[sp.public_id]), {"name": "X"}).status_code == 404
    assert _c(a).get(reverse("chat:rename", args=[sp.public_id])).status_code == 405
