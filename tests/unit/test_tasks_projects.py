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


def _member(p, person, role="member", by=None):
    # test fixture only: member add is not a built feature
    return ProjectMember.objects.create(
        project=p, person=person, role=role, added_by=by or p.created_by
    )


@pytest.mark.django_db
def test_lead_adds_and_moves_project_tasks_with_version_and_noop():
    from apps.tasks.models import ProjectTask
    from apps.tasks.services import StaleVersion

    a = _u("PT-A")
    p = pj.create_project(a, "P")
    todo, doing, _done = list(p.columns.all())
    t1 = pj.add_task(a, p, todo.pk, " first ")
    t2 = pj.add_task(a, p, todo.pk, "second")
    assert (t1.title, t1.position, t2.position) == ("first", 0, 1)
    with pytest.raises(ValidationError):
        pj.add_task(a, p, todo.pk, "  ")
    other = pj.create_project(a, "Other")
    with pytest.raises(ValidationError):  # a column of another project
        pj.add_task(a, p, other.columns.first().pk, "x")
    pj.move_task(a, t1, todo.pk, 1)  # same column: nothing changes
    t1.refresh_from_db()
    assert t1.version == 1
    moved = pj.move_task(a, t1, doing.pk, 1)
    assert (moved.column_id, moved.version) == (doing.pk, 2)
    with pytest.raises(StaleVersion):
        pj.move_task(a, t1, todo.pk, 1)
    assert ProjectTask.objects.get(pk=t1.pk).column_id == doing.pk


@pytest.mark.django_db
def test_only_leads_change_tasks_non_members_get_404_and_assign_needs_a_member():
    from django.core.exceptions import PermissionDenied
    from django.http import Http404

    a, m, x = _u("PU-A"), _u("PU-M"), _u("PU-X")
    p = pj.create_project(a, "P")
    col = p.columns.first()
    t = pj.add_task(a, p, col.pk, "t")
    _member(p, m)
    with pytest.raises(PermissionDenied):
        pj.add_task(m, p, col.pk, "no")
    with pytest.raises(PermissionDenied):
        pj.move_task(m, t, p.columns.last().pk, 1)
    with pytest.raises(Http404):
        pj.add_task(x, p, col.pk, "no")
    with pytest.raises(ValidationError):
        pj.assign_task(a, t, x)  # not a member: refused, project not revealed
    assert pj.assign_task(a, t, m).assignee_id == m.pk
    assert pj.assign_task(a, t, None).assignee_id is None
    ProjectMember.objects.filter(project=p, person=m).update(removed_at="2026-01-01T00:00:00Z")
    with pytest.raises(ValidationError):
        pj.assign_task(a, t, m)  # a removed member is checked fresh


@pytest.mark.django_db
def test_lead_losing_rights_after_page_load_is_refused_under_the_lock(monkeypatch):
    from django.http import Http404

    a = _u("PV-A")
    p = pj.create_project(a, "P")
    col = p.columns.first()
    ProjectMember.objects.filter(project=p, person=a).update(removed_at="2026-01-01T00:00:00Z")
    with pytest.raises(Http404):
        pj.add_task(a, p, col.pk, "late")  # stale object passed in, judged fresh


@pytest.mark.django_db
def test_project_task_views_and_privacy():
    from apps.tasks.models import ProjectTask

    a, m, x = _u("PW-A"), _u("PW-M"), _u("PW-X")
    p = pj.create_project(a, "P")
    _member(p, m)
    todo, doing, _ = list(p.columns.all())
    ca = _c(a)
    r = ca.post(
        reverse("tasks:project_add", args=[p.public_id]),
        {"column": todo.pk, "title": "<b>t</b>", "due": "2026-12-01"},
    )
    assert r.status_code == 302
    t = ProjectTask.objects.get()
    page = ca.get(reverse("tasks:project", args=[p.public_id])).content
    assert b"&lt;b&gt;t&lt;/b&gt;" in page and b"<b>t</b>" not in page
    ca.post(reverse("tasks:project_move", args=[t.public_id]), {"column": doing.pk, "version": 1})
    t.refresh_from_db()
    assert t.column_id == doing.pk
    # a plain member sees the task but has no controls and cannot change it
    cm = _c(m)
    mp = cm.get(reverse("tasks:project", args=[p.public_id])).content
    assert (
        b"&lt;b&gt;t" in mp and b"project_move" not in mp and b'placeholder="Add a task"' not in mp
    )
    r = cm.post(
        reverse("tasks:project_move", args=[t.public_id]),
        {"column": todo.pk, "version": 2},
        follow=True,
    )
    assert any("Only a project lead" in str(msg) for msg in r.context["messages"])
    t.refresh_from_db()
    assert t.column_id == doing.pk
    cx = _c(x)
    assert (
        cx.post(
            reverse("tasks:project_move", args=[t.public_id]), {"column": todo.pk, "version": 2}
        ).status_code
        == 404
    )
    assert (
        cx.post(
            reverse("tasks:project_add", args=[p.public_id]), {"column": todo.pk, "title": "n"}
        ).status_code
        == 404
    )
    assert ProjectTask.objects.count() == 1
    assert cx.post("/tasks/projects/task/not-a-uuid/move/").status_code == 404


@pytest.mark.django_db
def test_a_failure_part_way_through_create_leaves_nothing(monkeypatch):
    from apps.tasks.models import Column

    a = _u("PX-A")

    def boom(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(pj, "audit", boom)  # the last step inside the transaction
    with pytest.raises(RuntimeError):
        pj.create_project(a, "Half")
    assert (Project.objects.count(), ProjectMember.objects.count(), Column.objects.count()) == (
        0,
        0,
        0,
    )


# ---- concurrency (TiDB/MySQL only); each race has a control with the project lock removed ----


def _need_mysql():
    from django.db import connection

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")


def _setup_race(monkeypatch, locked, parties):
    import threading

    barrier = threading.Barrier(parties)

    def pause():
        try:
            barrier.wait(timeout=1.5)
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr(pj, "_pause", pause)
    if not locked:
        monkeypatch.setattr(pj, "_lock_project", lambda p: Project.objects.get(pk=p.pk))
    return pause


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_removal_vs_assign(monkeypatch, locked):
    """Memberships are seeded; no member-add UI exists. The removal is a test fixture that takes
    the project lock the way a future remove would (the control removes the lock on the assign
    side only, so the assign judges a stale membership)."""
    from django.db import transaction

    from tests.unit.test_drive_services import _threads

    _need_mysql()
    pause = _setup_race(monkeypatch, locked, 2)
    broken = 0
    for i in range(2 if locked else 3):
        a, m = _u(f"RM-A{i}{locked}"), _u(f"RM-M{i}{locked}")
        p = pj.create_project(a, "P")
        _member(p, m)
        from apps.tasks.models import ProjectTask

        t = ProjectTask.objects.create(project=p, column=p.columns.first(), title="t", created_by=a)

        def remove(p=p, m=m):
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=p.pk)
                pause()
                ProjectMember.objects.filter(project=p, person=m).update(
                    removed_at="2026-01-01T00:00:00Z"
                )
                # fixture only: a removed person is not left as assignee (what a future remove
                # does is an open policy choice; this makes the invariant checkable)
                ProjectTask.objects.filter(project=p, assignee=m).update(assignee=None)

        def assign(a=a, t=t, m=m):
            try:
                pj.assign_task(a, t, m)
            except ValidationError:
                pass

        _threads([remove, assign])
        t.refresh_from_db()
        gone = not ProjectMember.objects.filter(
            project=p, person=m, removed_at__isnull=True
        ).exists()
        if gone and t.assignee_id == m.pk:  # removed, yet still the assignee
            broken += 1
    assert (broken == 0) if locked else (broken > 0), broken


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_task_positions_are_unique_per_column(monkeypatch, locked):
    from apps.tasks.models import ProjectTask
    from tests.unit.test_drive_services import _threads

    _need_mysql()
    _setup_race(monkeypatch, locked, 3)
    a = _u(f"RP-A{locked}")
    p = pj.create_project(a, "P")
    col = p.columns.first().pk
    _threads([lambda: pj.add_task(a, p, col, "t") for _ in range(3)])
    pos = list(ProjectTask.objects.filter(project=p).values_list("position", flat=True))
    dup = len(pos) != len(set(pos))
    assert (not dup) if locked else dup, pos
