import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.tasks import projects as pj
from apps.tasks.models import Milestone, Project, ProjectMember
from tests.unit.test_tasks_projects import _c, _member, _u


@pytest.fixture
def team(db):
    lead, member, stranger = _u("ME-L"), _u("ME-M"), _u("ME-S")
    p = pj.create_project(lead, "P")
    _member(p, member)
    ms = pj.create_milestone(lead, p, "Alpha", datetime.date(2026, 12, 1))
    return lead, member, stranger, p, ms


def _ms(ms):
    return Milestone.objects.get(pk=ms.pk)


def test_lead_edits_name_and_date_clears_date_and_a_same_save_is_silent(team):
    lead, member, stranger, p, ms = team
    pj.edit_milestone(lead, p, ms.pk, "  Beta  ", datetime.date(2027, 1, 2))
    assert (_ms(ms).name, _ms(ms).due_date) == ("Beta", datetime.date(2027, 1, 2))
    n = AuditLog.objects.filter(action="tasks.milestone.edit").count()
    assert n == 1
    pj.edit_milestone(lead, p, ms.pk, "Beta", datetime.date(2027, 1, 2))
    assert AuditLog.objects.filter(action="tasks.milestone.edit").count() == n
    pj.edit_milestone(lead, p, ms.pk, "Beta", None)
    assert _ms(ms).due_date is None and AuditLog.objects.filter(action="tasks.milestone.edit").count() == n + 1


def test_refusals_and_other_projects_milestone_untouched(team):
    lead, member, stranger, p, ms = team
    for bad in ("", " ", "x" * 121):
        with pytest.raises(ValidationError, match="up to 120"):
            pj.edit_milestone(lead, p, ms.pk, bad, None)
    with pytest.raises(ValidationError, match="Use a date"):
        pj.edit_milestone(lead, p, ms.pk, "Ok", "2027-01-01")
    with pytest.raises(PermissionDenied):
        pj.edit_milestone(member, p, ms.pk, "Mine", None)
    with pytest.raises(Http404):
        pj.edit_milestone(stranger, p, ms.pk, "Mine", None)
    other = pj.create_project(lead, "Other")
    with pytest.raises(Http404):
        pj.edit_milestone(lead, other, ms.pk, "Cross", None)  # a milestone of another project
    Milestone.objects.filter(pk=ms.pk).update(deleted_at=timezone.now())
    with pytest.raises(Http404):
        pj.edit_milestone(lead, p, ms.pk, "Gone", None)
    ProjectMember.objects.filter(project=p, person=lead).update(removed_at=timezone.now())
    with pytest.raises(Http404):
        pj.edit_milestone(lead, p, ms.pk, "Late", None)
    assert _ms(ms).name == "Alpha"


def test_view_forms_for_lead_only_and_messages(team):
    lead, member, stranger, p, ms = team
    page = reverse("tasks:project", args=[p.public_id])
    url = reverse("tasks:milestone_edit", args=[p.public_id, ms.pk])
    lp = _c(lead).get(page).content.decode()
    assert 'aria-label="Milestone name for Alpha"' in lp and 'value="2026-12-01"' in lp
    assert "Milestone name for" not in _c(member).get(page).content.decode()
    assert _c(stranger).post(url, {"name": "X"}).status_code == 404
    assert _c(lead).get(url).status_code == 405
    r = _c(member).post(url, {"name": "X"}, follow=True)
    assert b"Only a project lead can change milestones." in r.content and _ms(ms).name == "Alpha"
    r = _c(lead).post(url, {"name": "Beta", "due": "nope"}, follow=True)
    assert b"Use a date like" in r.content and _ms(ms).name == "Alpha"
    r = _c(lead).post(url, {"name": "Beta", "due": ""}, follow=True)
    assert b"Milestone saved." in r.content and _ms(ms).name == "Beta" and _ms(ms).due_date is None
