"""effective_entry: trashed visibility is never active sync eligibility."""

import pytest

from apps.accounts.models import CustomUser
from apps.drive import manifest as m
from apps.drive import services as svc


def _u(eid):
    return CustomUser.objects.create(
        employee_id=eid,
        full_name=eid,
        email=f"{eid.lower()}@salowtech.com",
        role="employee",
        status="active",
        is_active=True,
    )


@pytest.mark.django_db
def test_states_for_owner_sharee_stranger_and_trash():
    a, b, c = _u("M-A"), _u("M-B"), _u("M-C")
    top = svc.create_folder(a, "Top")
    sub = svc.create_folder(a, "Sub", top)
    f = svc.create_file(a, "x.txt", sub)
    svc.share(a, top, b, "editor")
    assert m.effective_entry(a, f) == m.Entry(m.ACTIVE, "owner")
    assert m.effective_entry(b, f) == m.Entry(m.ACTIVE, "editor")
    assert m.effective_entry(c, f) == m.Entry(m.NONE, None)
    # b (editor) trashes the sub folder: owner and trasher see it as trash only
    svc.trash(b, sub)
    sub.refresh_from_db()
    f.refresh_from_db()
    for who in (a, b):
        assert m.effective_entry(who, sub).state == m.TRASHED
        assert m.effective_entry(who, f).state in (m.TRASHED, m.NONE)
    assert m.effective_entry(c, sub) == m.Entry(m.NONE, None)
    svc.restore(a, sub)
    sub.refresh_from_db()
    f.refresh_from_db()
    assert m.effective_entry(b, f).state == m.ACTIVE


@pytest.mark.django_db
def test_trashed_never_reported_active_even_when_only_an_ancestor_is_trashed():
    a, b = _u("M-D"), _u("M-G")
    top = svc.create_folder(a, "T")
    sub = svc.create_folder(a, "S", top)
    f = svc.create_file(a, "y.txt", sub)
    svc.share(a, top, b, "viewer")
    # simulate an item whose own flag is clear while an ancestor is in the trash
    type(top).objects.filter(pk=top.pk).update(trashed_at=top.created_at)
    top.refresh_from_db()
    sub.refresh_from_db()
    f.refresh_from_db()
    assert m.effective_entry(a, f).state == m.TRASHED
    assert m.effective_entry(a, sub).state == m.TRASHED
    assert m.effective_entry(b, f) == m.Entry(m.NONE, None)  # sharee loses it, owner sees trash


@pytest.mark.django_db
def test_inactive_person_and_after_unshare_and_after_move_out():
    a, b = _u("M-E"), _u("M-F")
    top = svc.create_folder(a, "Shared")
    other = svc.create_folder(a, "Private")
    inner = svc.create_folder(a, "Inner", top)
    svc.share(a, top, b, "viewer")
    assert m.effective_entry(b, inner).state == m.ACTIVE
    svc.move(a, inner, other)
    inner.refresh_from_db()
    assert m.effective_entry(b, inner) == m.Entry(m.NONE, None)  # inherited access lost by a move
    svc.unshare(a, top, b)
    top.refresh_from_db()
    assert m.effective_entry(b, top) == m.Entry(m.NONE, None)
    svc.share(a, top, b, "viewer")
    CustomUser.objects.filter(pk=b.pk).update(is_active=False, status="inactive")
    b.refresh_from_db()
    assert m.effective_entry(b, top) == m.Entry(m.NONE, None)
