import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.tasks import services as svc
from apps.tasks.models import TaskList
from tests.unit.test_tasks_services import _u


@pytest.fixture
def setup(db):
    a, b = _u("N-A"), _u("N-B")
    return a, b, svc.create_list(a, "Work")


def _c(u):
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    return c


def test_owner_renames_trimmed_and_an_identical_name_writes_no_audit_row(setup):
    a, b, tl = setup
    assert svc.rename_list(a, tl, "  Home  ").name == "Home"
    assert AuditLog.objects.filter(action="tasks.list.rename").count() == 1
    svc.rename_list(a, TaskList.objects.get(pk=tl.pk), "Home")
    assert AuditLog.objects.filter(action="tasks.list.rename").count() == 1


def test_name_rules_match_create_list_and_a_duplicate_name_is_allowed(setup):
    a, b, tl = setup
    for bad in ("", "   ", "x" * 121):
        with pytest.raises(ValidationError, match="up to 120 characters"):
            svc.rename_list(a, tl, bad)
    assert TaskList.objects.get(pk=tl.pk).name == "Work"
    other = svc.create_list(a, "Other")
    assert svc.rename_list(a, other, "Work").name == "Work"  # create_list has no uniqueness rule either
    assert svc.rename_list(a, tl, "x" * 120).name == "x" * 120


def test_a_stranger_and_a_deleted_list_are_refused(setup):
    a, b, tl = setup
    with pytest.raises(PermissionDenied):
        svc.rename_list(b, tl, "Mine now")
    svc.delete_list(a, tl)
    with pytest.raises(PermissionDenied):
        svc.rename_list(a, TaskList.objects.get(pk=tl.pk), "Renamed")
    assert TaskList.objects.get(pk=tl.pk).name == "Work"


def test_view_renames_404s_for_a_stranger_and_keeps_the_typed_name_on_error(setup):
    a, b, tl = setup
    url = reverse("tasks:rename_list", args=[tl.public_id])
    assert _c(b).post(url, {"name": "Nope"}).status_code == 404
    assert _c(a).get(url).status_code == 405
    r = _c(a).post(url, {"name": "x" * 121})
    assert r.status_code == 200 and b"up to 120 characters" in r.content
    assert ("x" * 121).encode() in r.content  # what was typed is still in the box
    assert TaskList.objects.get(pk=tl.pk).name == "Work"
    r = _c(a).post(url, {"name": "Home"}, follow=True)
    assert b"List renamed." in r.content and TaskList.objects.get(pk=tl.pk).name == "Home"
