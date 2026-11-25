import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.tasks import projects as pj
from apps.tasks import services as svc
from apps.tasks.models import Project, ProjectMember


def _u(eid):
    u = CustomUser.objects.create_user(employee_id=eid, full_name=f"P {eid}", password="x" * 12)
    u.status, u.is_active = "active", True
    u.save()
    return u


def _c(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_create_makes_creator_lead_with_default_columns_and_no_one_else_sees_it():
    a, b = _u("PJ-A"), _u("PJ-B")
    p = pj.create_project(a, "  Launch  ")
    assert p.name == "Launch"
    m = ProjectMember.objects.get(project=p)
    assert (m.person_id, m.role) == (a.pk, "lead")
    assert [c.name for c in p.columns.all()] == ["To do", "Doing", "Done"]
    assert list(pj.projects_for(a)) == [p] and list(pj.projects_for(b)) == []
    for bad in ("", "   ", "x" * 121):
        with pytest.raises(ValidationError):
            pj.create_project(a, bad)


@pytest.mark.django_db
def test_non_member_gets_404_same_as_missing_and_personal_lists_stay_private():
    a, b = _u("PK-A"), _u("PK-B")
    p = pj.create_project(a, "Secret")
    svc.create_task(a, svc.create_list(a, "Mine"), "personal")
    cb = _c(b)
    r = cb.get(reverse("tasks:project", args=[p.public_id]))
    assert r.status_code == 404
    import uuid

    assert cb.get(reverse("tasks:project", args=[uuid.uuid4()])).status_code == 404
    page = _c(a).get(reverse("tasks:project", args=[p.public_id]))
    assert page.status_code == 200 and b"Secret" in page.content and b"personal" not in page.content
    assert b"Secret" not in cb.get(reverse("tasks:projects")).content


@pytest.mark.django_db
def test_removed_member_and_deleted_project_are_not_visible():
    a = _u("PL-A")
    p = pj.create_project(a, "X")
    ProjectMember.objects.filter(project=p).update(removed_at="2026-01-01T00:00:00Z")
    assert _c(a).get(reverse("tasks:project", args=[p.public_id])).status_code == 404
    ProjectMember.objects.filter(project=p).update(removed_at=None)
    Project.objects.filter(pk=p.pk).update(deleted_at="2026-01-01T00:00:00Z")
    assert _c(a).get(reverse("tasks:project", args=[p.public_id])).status_code == 404


@pytest.mark.django_db
def test_views_create_throttle_csrf_and_logged_out(monkeypatch):
    a = _u("PM-A")
    c = _c(a)
    r = c.post(reverse("tasks:new_project"), {"name": "Alpha"})
    assert r.status_code == 302 and Project.objects.count() == 1
    r = c.post(reverse("tasks:new_project"), {"name": ""}, follow=True)
    assert any("needs a name" in str(m) for m in r.context["messages"])
    from apps.tasks import project_views as pv

    monkeypatch.setattr(pv, "PROJECT_LIMIT", 1)
    c.post(reverse("tasks:new_project"), {"name": "Beta"})
    c.post(reverse("tasks:new_project"), {"name": "Gamma"})
    assert Project.objects.count() == 1  # both later posts refused
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(a)
    assert strict.post(reverse("tasks:new_project"), {"name": "Z"}).status_code == 403
    anon = Client().post(reverse("tasks:new_project"), {"name": "Z"})
    assert anon.status_code == 302 and ("login" in anon["Location"] or "sign" in anon["Location"])


@pytest.mark.django_db
def test_workspace_host_serves_projects_and_iam_host_does_not():
    a = _u("PN-A")
    c = _c(a)
    assert c.get("/tasks/projects/", HTTP_HOST="app.salowtech.com").status_code == 200
    assert c.get("/tasks/projects/", HTTP_HOST="iam.salowtech.com").status_code == 404
    assert (
        b"Everyone listed here can see this project"
        in c.get(
            reverse("tasks:project", args=[pj.create_project(a, "H").public_id]),
            HTTP_HOST="app.salowtech.com",
        ).content
    )
