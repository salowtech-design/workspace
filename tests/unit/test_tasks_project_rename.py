import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.urls import reverse

from apps.core.models import AuditLog
from apps.tasks import projects as pj
from apps.tasks.models import Project, ProjectMember
from tests.unit.test_tasks_projects import _c, _member, _u


@pytest.fixture
def team(db):
    lead, member, stranger = _u("RN-L"), _u("RN-M"), _u("RN-S")
    p = pj.create_project(lead, "Old name")
    _member(p, member)
    return lead, member, stranger, p


def _name(p):
    return Project.objects.get(pk=p.pk).name


def test_lead_renames_trimmed_and_members_see_the_new_name(team):
    lead, member, stranger, p = team
    pj.rename_project(lead, p, "  New name  ")
    assert _name(p) == "New name"
    assert [x.name for x in pj.projects_for(member)] == ["New name"]
    assert AuditLog.objects.filter(action="tasks.project.rename").count() == 1
    pj.rename_project(lead, p, "New name")  # same name: no write, no audit
    assert AuditLog.objects.filter(action="tasks.project.rename").count() == 1


def test_bad_names_member_stranger_removed_lead_and_deleted_project_are_refused(team):
    lead, member, stranger, p = team
    for bad in ("", "   ", "x" * 121):
        with pytest.raises(ValidationError, match="up to 120"):
            pj.rename_project(lead, p, bad)
    with pytest.raises(PermissionDenied):
        pj.rename_project(member, p, "Mine now")
    with pytest.raises(Http404):
        pj.rename_project(stranger, p, "Mine now")
    from django.utils import timezone

    ProjectMember.objects.filter(project=p, person=lead).update(removed_at=timezone.now())
    with pytest.raises(Http404):
        pj.rename_project(lead, p, "Late")
    ProjectMember.objects.filter(project=p, person=lead).update(removed_at=None)
    Project.objects.filter(pk=p.pk).update(deleted_at=timezone.now())
    with pytest.raises(Http404):
        pj.rename_project(lead, p, "Zombie")
    assert _name(p) == "Old name"


def test_view_lead_form_member_page_without_it_and_refusals(team):
    lead, member, stranger, p = team
    url = reverse("tasks:project_rename", args=[p.public_id])
    page = reverse("tasks:project", args=[p.public_id])
    assert 'aria-label="Project name"' in _c(lead).get(page).content.decode()
    assert 'aria-label="Project name"' not in _c(member).get(page).content.decode()
    assert _c(stranger).post(url, {"new_name": "X"}).status_code == 404
    assert _c(lead).get(url).status_code == 405
    r = _c(member).post(url, {"new_name": "X"}, follow=True)
    assert b"Only a project lead can rename" in r.content and _name(p) == "Old name"
    r = _c(lead).post(url, {"new_name": " "}, follow=True)
    assert b"up to 120" in r.content and _name(p) == "Old name"
    r = _c(lead).post(url, {"new_name": "Fresh"}, follow=True)
    assert b"Project renamed." in r.content and b"<h2>Fresh</h2>" in r.content
