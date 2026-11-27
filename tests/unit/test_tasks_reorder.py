import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.tasks import services as svc
from apps.tasks.models import Task
from tests.unit.test_tasks_services import _u


@pytest.fixture
def setup(db):
    a, b = _u("RO-A"), _u("RO-B")
    return a, b, svc.create_list(a, "L")


def _c(u):
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    return c


def _v(t):
    return Task.objects.get(pk=t.pk).version


def _order(a, tl, parent=None):
    return [t.title for t in svc.tasks_in(a, tl) if t.parent_id == (parent.pk if parent else None)]


def test_up_and_down_swap_neighbours_and_the_edges_do_nothing(setup):
    a, b, tl = setup
    x, y, z = (svc.create_task(a, tl, n) for n in "XYZ")
    svc.reorder_task(a, z, _v(z), "up")
    assert _order(a, tl) == ["X", "Z", "Y"]
    svc.reorder_task(a, x, _v(x), "down")
    assert _order(a, tl) == ["Z", "X", "Y"]
    before, n = _v(z), AuditLog.objects.filter(action="tasks.task.reorder").count()
    svc.reorder_task(a, z, before, "up")  # already first
    svc.reorder_task(a, y, _v(y), "down")  # already last
    assert _v(z) == before and AuditLog.objects.filter(action="tasks.task.reorder").count() == n
    with pytest.raises(ValidationError, match="up or down"):
        svc.reorder_task(a, z, _v(z), "sideways")


def test_tied_positions_after_a_restore_are_renumbered_as_shown(setup):
    a, b, tl = setup
    one, two, three = (svc.create_task(a, tl, n) for n in ("One", "Two", "Three"))
    svc.delete_task(a, three)
    four = svc.create_task(a, tl, "Four")  # takes Three's old number
    svc.restore_task(a, Task.objects.get(pk=three.pk))
    assert Task.objects.get(pk=four.pk).position == Task.objects.get(pk=three.pk).position  # the tie is real
    shown = _order(a, tl)  # tie broken by id: Three (older id) first
    assert shown == ["One", "Two", "Three", "Four"]
    svc.reorder_task(a, Task.objects.get(pk=four.pk), _v(four), "up")
    assert len({t.position for t in svc.tasks_in(a, tl)}) == 4  # all different now
    assert _order(a, tl) == ["One", "Two", "Four", "Three"]


def test_subtasks_reorder_among_their_own_siblings_only(setup):
    a, b, tl = setup
    top = svc.create_task(a, tl, "Top")
    other = svc.create_task(a, tl, "Other")
    k1, k2 = svc.create_task(a, tl, "K1", parent=top), svc.create_task(a, tl, "K2", parent=top)
    svc.reorder_task(a, k2, _v(k2), "up")
    assert _order(a, tl, top) == ["K2", "K1"] and _order(a, tl) == ["Top", "Other"]
    svc.reorder_task(a, k1, _v(k1), "up")  # K1 is now second: moves above K2 again
    assert _order(a, tl, top) == ["K1", "K2"]
    assert Task.objects.get(pk=other.pk).position == 2


def test_stale_version_stranger_assignee_and_deleted_are_refused(setup):
    a, b, tl = setup
    x, y = svc.create_task(a, tl, "X"), svc.create_task(a, tl, "Y")
    seen = _v(y)
    svc.update_task(a, Task.objects.get(pk=y.pk), seen, title="Y2")
    with pytest.raises(svc.StaleVersion):
        svc.reorder_task(a, Task.objects.get(pk=y.pk), seen, "up")
    with pytest.raises(PermissionDenied):
        svc.reorder_task(b, Task.objects.get(pk=y.pk), _v(y), "up")
    svc.assign(a, Task.objects.get(pk=x.pk), b)
    with pytest.raises(PermissionDenied):
        svc.reorder_task(b, Task.objects.get(pk=x.pk), _v(x), "down")  # the assignee has no edit right
    svc.delete_task(a, Task.objects.get(pk=y.pk))
    with pytest.raises(PermissionDenied):
        svc.reorder_task(a, Task.objects.get(pk=y.pk), _v(y), "up")
    assert _order(a, tl) == ["X"]


def test_view_reorders_with_the_version_and_404s_for_strangers(setup):
    a, b, tl = setup
    x, y = svc.create_task(a, tl, "X"), svc.create_task(a, tl, "Y")
    url = reverse("tasks:reorder", args=[y.public_id])
    assert _c(b).post(url, {"direction": "up", "version": _v(y)}).status_code == 404
    assert _c(a).get(url).status_code == 405
    r = _c(a).post(url, {"direction": "up", "version": _v(y) + 3}, follow=True)
    assert b"changed while you were looking" in r.content and _order(a, tl) == ["X", "Y"]
    page = _c(a).get(reverse("tasks:list", args=[tl.public_id])).content.decode()
    assert "Move Y up" in page and "Move X down" in page and "Move X up" not in page and "Move Y down" not in page
    _c(a).post(url, {"direction": "up", "version": _v(y)})
    assert _order(a, tl) == ["Y", "X"]
