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

    mine = threading.local()
    saw_commit = []

    def pause():
        try:
            barrier.wait(timeout=1.5)
        except threading.BrokenBarrierError:
            pass
        ev = getattr(mine, "wait_for", None)
        if ev is not None:
            # write only after the other side has committed; when the lock keeps that side out
            # the event never comes, so give up after a bounded wait (no deadlock)
            saw_commit.append(ev.wait(timeout=1.0))

    pause.mine = mine
    pause.saw_commit = saw_commit
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
    import threading

    from django.db import transaction

    from tests.unit.test_drive_services import _threads

    _need_mysql()
    _setup_race(monkeypatch, locked, 2)  # sets the unlocked control; the hook is replaced below
    broken = 0
    saw_commit, outcomes = [], []
    for i in range(2 if locked else 3):
        a, m = _u(f"RM-A{i}{locked}"), _u(f"RM-M{i}{locked}")
        p = pj.create_project(a, "P")
        _member(p, m)
        from apps.tasks.models import ProjectTask

        t = ProjectTask.objects.create(project=p, column=p.columns.first(), title="t", created_by=a)

        committed = threading.Event()  # the removal's transaction has committed
        assign_paused = threading.Event()  # assign has judged membership and is about to write

        def hook(committed=committed, assign_paused=assign_paused):
            assign_paused.set()
            # write only after the removal has committed; when the lock keeps the remover out
            # the event never comes, so the wait is bounded (no deadlock)
            saw_commit.append(committed.wait(timeout=2.0))

        monkeypatch.setattr(pj, "_pause", hook)

        def remove(p=p, m=m, committed=committed, assign_paused=assign_paused):
            assert assign_paused.wait(timeout=10), "assign never reached its write"  # assign first
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=p.pk)
                ProjectMember.objects.filter(project=p, person=m).update(
                    removed_at="2026-01-01T00:00:00Z"
                )
                # fixture only: a removed person is not left as assignee (what a future remove
                # does is an open policy choice; this makes the invariant checkable)
                ProjectTask.objects.filter(project=p, assignee=m).update(assignee=None)
            committed.set()  # the transaction block has exited: the removal is committed

        def assign(a=a, t=t, m=m):
            pj.assign_task(a, t, m)

        outcomes.append(_threads([remove, assign]))
        t.refresh_from_db()
        gone = not ProjectMember.objects.filter(
            project=p, person=m, removed_at__isnull=True
        ).exists()
        if gone and t.assignee_id == m.pk:  # removed, yet still the assignee
            broken += 1
    rounds = 2 if locked else 3
    # ordering evidence, not timing: the late writer either saw the removal commit (no lock) or
    # was holding the lock while the remover could not commit (lock held, event never set)
    assert outcomes == [["ok", "ok"]] * rounds, outcomes  # no error, deadlock or timeout hidden
    assert saw_commit == [not locked] * rounds, saw_commit
    # control: the assign judged a membership that was removed before its write, every round
    assert (broken == 0) if locked else (broken == rounds), (broken, rounds)


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


@pytest.mark.django_db
def test_project_task_edit_version_preserve_and_lead_only():
    from apps.tasks.models import ProjectTask

    a, m = _u("PE-A"), _u("PE-M")
    p = pj.create_project(a, "P")
    _member(p, m)
    t = pj.add_task(a, p, p.columns.first().pk, "old")
    ca, cm = _c(a), _c(m)
    url = reverse("tasks:project_edit", args=[t.public_id])
    page = ca.get(url)
    assert page.status_code == 200 and b'name="version" value="1"' in page.content
    assert (
        cm.get(url).status_code == 404
        and cm.post(url, {"version": 1, "title": "x"}).status_code == 404
    )
    ca.post(url, {"version": 1, "title": "new"})  # notes and due not sent: kept
    t.refresh_from_db()
    assert (t.title, t.version) == ("new", 2)
    pj.edit_task(a, t, 2)  # empty edit: nothing changes
    t.refresh_from_db()
    assert t.version == 2
    r = ca.post(url, {"version": 1, "title": 'mine"><script>x</script>'})
    body = r.content.decode()
    assert r.status_code == 409 and "tasks-conflict" in body and "<script>x</script>" not in body
    r = ca.post(url, {"version": 2, "title": "  "})
    assert r.status_code == 400 and "tasks-conflict" not in r.content.decode()
    assert ProjectTask.objects.get(pk=t.pk).title == "new"


@pytest.mark.django_db
def test_assign_noop_stale_and_updated_at_and_personal_pages_stay_separate():
    from apps.tasks.models import ProjectTask
    from apps.tasks.services import StaleVersion

    a, m = _u("PY-A"), _u("PY-M")
    p = pj.create_project(a, "Proj")
    _member(p, m)
    t = pj.add_task(a, p, p.columns.first().pk, "projtask")
    before = ProjectTask.objects.get(pk=t.pk).updated_at
    t2 = pj.assign_task(a, t, m, expected_version=1)
    assert (t2.version, t2.assignee_id) == (2, m.pk) and t2.updated_at > before
    again = pj.assign_task(a, t, m, expected_version=1)  # same assignee: no-op, even if stale
    assert again.version == 2
    with pytest.raises(StaleVersion):
        pj.assign_task(a, t, None, expected_version=1)  # a real change with a stale version
    assert ProjectTask.objects.get(pk=t.pk).assignee_id == m.pk
    cm = _c(m)  # the personal list page and the personal-task service stay free of project tasks;
    # the Assigned page shows them in their own "In projects" section (test_assigned_page_*)
    assert b"projtask" not in cm.get(reverse("tasks:home")).content
    assert list(svc.assigned_to(m)) == []


@pytest.mark.django_db
def test_project_edit_notes_and_due_date_set_and_clear_and_name_stays_off_personal_pages():
    from apps.tasks.models import ProjectTask

    a, m = _u("PD-A"), _u("PD-M")
    p = pj.create_project(a, "Hidden project name")
    _member(p, m)
    t = pj.add_task(a, p, p.columns.first().pk, "t")
    ca = _c(a)
    url = reverse("tasks:project_edit", args=[t.public_id])
    ca.post(url, {"version": 1, "notes": "some notes", "due": "2026-12-24"})
    t.refresh_from_db()
    assert (t.notes, str(t.due_date), t.title, t.version) == ("some notes", "2026-12-24", "t", 2)
    ca.post(url, {"version": 2, "due": ""})  # clear the date, notes and title untouched
    t.refresh_from_db()
    assert (t.notes, t.due_date, t.version) == ("some notes", None, 3)
    assert ca.post(url, {"version": 3, "due": "24/12/2026"}).status_code == 400
    for who in (a, m):
        for name in ("tasks:home", "tasks:assigned"):
            assert b"Hidden project name" not in _c(who).get(reverse(name)).content
    assert ProjectTask.objects.get(pk=t.pk).version == 3


@pytest.mark.django_db
def test_project_task_delete_needs_confirmation_and_is_lead_only():
    from apps.tasks.models import ProjectTask

    a, m, x = _u("PZ-A"), _u("PZ-M"), _u("PZ-X")
    p = pj.create_project(a, "P")
    _member(p, m)
    t = pj.add_task(a, p, p.columns.first().pk, "gone soon")
    url = reverse("tasks:project_delete", args=[t.public_id])
    ca = _c(a)
    page = ca.get(url)
    assert page.status_code == 200 and b"Nothing is permanently erased" in page.content
    ca.post(url, {})  # no confirm field: nothing happens
    assert ProjectTask.objects.get(pk=t.pk).deleted_at is None
    for who in (m, x):
        assert _c(who).get(url).status_code == 404
        assert _c(who).post(url, {"confirm": "yes"}).status_code == 404
    assert ProjectTask.objects.get(pk=t.pk).deleted_at is None
    r = ca.post(url, {"confirm": "yes"})
    assert r.status_code == 302 and ProjectTask.objects.get(pk=t.pk).deleted_at is not None
    assert b"gone soon" not in ca.get(reverse("tasks:project", args=[p.public_id])).content
    assert ca.get(url).status_code == 404  # already deleted: same as missing
    assert (
        ca.post(reverse("tasks:project_edit", args=[t.public_id]), {"version": 1}).status_code
        == 404
    )
    from apps.core.models import AuditLog

    assert AuditLog.objects.filter(action="tasks.project_task.delete").count() == 1


@pytest.mark.django_db
def test_milestones_lead_only_same_project_noop_version_and_progress():
    from django.core.exceptions import PermissionDenied
    from django.http import Http404

    from apps.tasks.models import ProjectTask
    from apps.tasks.services import StaleVersion

    a, m, x = _u("PM1-A"), _u("PM1-M"), _u("PM1-X")
    p = pj.create_project(a, "P")
    other = pj.create_project(a, "Other")
    _member(p, m)
    ms = pj.create_milestone(a, p, " Beta ", None)
    foreign = pj.create_milestone(a, other, "Elsewhere")
    assert ms.name == "Beta"
    for bad in ("", "  ", "x" * 121):
        with pytest.raises(ValidationError):
            pj.create_milestone(a, p, bad)
    with pytest.raises(PermissionDenied):
        pj.create_milestone(m, p, "no")
    with pytest.raises(Http404):
        pj.create_milestone(x, p, "no")
    todo, _doing, done = list(p.columns.all())
    t1 = pj.add_task(a, p, todo.pk, "t1")
    t2 = pj.add_task(a, p, todo.pk, "t2")
    with pytest.raises(ValidationError):  # a milestone of another project
        pj.set_milestone(a, t1, foreign.pk)
    t1 = pj.set_milestone(a, t1, ms.pk, expected_version=1)
    assert (t1.milestone_id, t1.version) == (ms.pk, 2)
    assert pj.set_milestone(a, t1, ms.pk, expected_version=1).version == 2  # same: no-op
    with pytest.raises(StaleVersion):
        pj.set_milestone(a, t1, None, expected_version=1)  # real change, stale version
    pj.set_milestone(a, t2, ms.pk)
    pj.move_task(a, t1, done.pk, 2)
    row = pj.milestones_of(p).get()
    assert (row.total, row.in_done) == (2, 1)
    assert pj.set_milestone(a, t2, "").milestone_id is None
    assert ProjectTask.objects.get(pk=t1.pk).milestone_id == ms.pk


@pytest.mark.django_db
def test_every_project_post_enforces_csrf_and_refuses_non_leads():
    a, m = _u("PC-A"), _u("PC-M")
    p = pj.create_project(a, "P")
    _member(p, m)
    col = p.columns.first()
    t = pj.add_task(a, p, col.pk, "t")
    ms = pj.create_milestone(a, p, "M")
    posts = [
        (reverse("tasks:new_project"), {"name": "z"}),
        (reverse("tasks:project_add", args=[p.public_id]), {"column": col.pk, "title": "z"}),
        (reverse("tasks:project_move", args=[t.public_id]), {"column": col.pk, "version": 1}),
        (reverse("tasks:project_edit", args=[t.public_id]), {"version": 1, "title": "z"}),
        (reverse("tasks:project_delete", args=[t.public_id]), {"confirm": "yes"}),
        (reverse("tasks:milestone_add", args=[p.public_id]), {"name": "z"}),
        (reverse("tasks:project_milestone", args=[t.public_id]), {"milestone": ms.pk}),
    ]
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(a)
    anon = Client()
    cm = _c(m)
    for url, data in posts:
        assert strict.post(url, data).status_code == 403, url
        r = anon.post(url, data)
        assert r.status_code == 302 and ("login" in r["Location"] or "sign" in r["Location"]), url
    assert Project.objects.count() == 1 and pj.milestones_of(p).count() == 1  # nothing was created
    # a plain member's forced POSTs change nothing
    for url, data in posts[1:]:
        cm.post(url, data)
    t.refresh_from_db()
    assert (t.title, t.deleted_at, t.milestone_id, t.version) == ("t", None, None, 1)
    assert p.tasks.count() == 1 and pj.milestones_of(p).count() == 1


@pytest.mark.django_db
def test_milestone_views_escape_and_bad_ids_404():
    a = _u("PV2-A")
    p = pj.create_project(a, "P")
    t = pj.add_task(a, p, p.columns.first().pk, "t")
    ca = _c(a)
    ca.post(
        reverse("tasks:milestone_add", args=[p.public_id]),
        {"name": "<i>x</i>", "due": "2026-12-31"},
    )
    page = ca.get(reverse("tasks:project", args=[p.public_id])).content
    assert b"&lt;i&gt;x&lt;/i&gt;" in page and b"<i>x</i>" not in page and b"0 of 0 in Done" in page
    r = ca.post(
        reverse("tasks:milestone_add", args=[p.public_id]),
        {"name": "d", "due": "31/12/2026"},
        follow=True,
    )
    assert (
        any("Use a date" in str(x) for x in r.context["messages"])
        and pj.milestones_of(p).count() == 1
    )
    assert (
        ca.post(
            reverse("tasks:project_milestone", args=[t.public_id]), {"milestone": "abc"}
        ).status_code
        == 404
    )


def _ptask(lead, title="T"):
    p = pj.create_project(lead, "Cm")
    t = pj.add_task(lead, p, p.columns.first().pk, title)
    return p, t


@pytest.mark.django_db
def test_comment_members_post_plain_text_non_members_and_anonymous_get_nothing():
    from apps.tasks.models import TaskComment

    a, b = _u("CM-A"), _u("CM-B")
    p, t = _ptask(a)
    url = reverse("tasks:project_comments", args=[t.public_id])
    ca = _c(a)
    r = ca.post(url, {"body": "  <script>alert(1)</script> hi  "})
    assert r.status_code == 302
    c = TaskComment.objects.get()
    assert (c.author_id, c.body) == (a.pk, "<script>alert(1)</script> hi")
    page = ca.get(url).content.decode()
    assert "&lt;script&gt;alert(1)&lt;/script&gt; hi" in page and "<script>alert(1)" not in page
    # non-member: same 404 as missing, GET and POST, nothing written, nothing shown
    cb = _c(b)
    assert cb.get(url).status_code == 404
    assert cb.post(url, {"body": "intruder"}).status_code == 404
    assert TaskComment.objects.count() == 1
    # anonymous: login redirect, nothing written
    anon = Client()
    for m in (anon.get, anon.post):
        r = m(url, {"body": "x"})
        assert r.status_code == 302 and "login" in r["Location"]
    assert TaskComment.objects.count() == 1
    # a removed member loses access
    ProjectMember.objects.create(project=p, person=b, added_by=a)
    assert cb.post(url, {"body": "member"}).status_code == 302
    ProjectMember.objects.filter(person=b).update(removed_at=p.created_at)
    assert cb.get(url).status_code == 404 and cb.post(url, {"body": "late"}).status_code == 404
    assert TaskComment.objects.count() == 2


@pytest.mark.django_db
def test_comment_limits_empty_and_deleted_task_and_method():
    from apps.tasks.models import COMMENT_MAX, TaskComment

    a = _u("CM-C")
    p, t = _ptask(a)
    url = reverse("tasks:project_comments", args=[t.public_id])
    ca = _c(a)
    for bad in ("", "   \n ", "x" * (COMMENT_MAX + 1)):
        r = ca.post(url, {"body": bad})
        assert r.status_code == 400
    assert TaskComment.objects.count() == 0
    assert ca.post(url, {"body": "x" * COMMENT_MAX}).status_code == 302
    assert ca.put(url).status_code == 405 and ca.delete(url).status_code == 405
    pj.delete_task(a, t)
    assert ca.get(url).status_code == 404 and ca.post(url, {"body": "z"}).status_code == 404
    assert TaskComment.objects.count() == 1


@pytest.mark.django_db
def test_comment_pages_reach_every_comment_oldest_first_with_no_gap_or_repeat():
    import re

    a = _u("CM-D")
    p, t = _ptask(a)
    n = pj.COMMENTS_SHOWN * 2 + 7
    for i in range(n):
        pj.add_comment(a, t, f"c{i}")
    url = reverse("tasks:project_comments", args=[t.public_id])
    ca = _c(a)
    seen, pages, nxt = [], 0, url
    while nxt:
        page = ca.get(nxt).content.decode()
        pages += 1
        bodies = re.findall(r'class="comment-body">(c\d+)<', page)
        assert bodies == sorted(bodies, key=lambda x: int(x[1:]))  # oldest first inside a page
        seen = bodies + seen  # each step back is older
        m = re.search(r'href="([^"]*\?before=\d+)">Older comments', page)
        nxt = m.group(1) if m else None
        assert ("Newest comments" in page) == (pages > 1)
        assert pages < 10
    assert pages == 3
    assert seen == [f"c{i}" for i in range(n)]
    board = ca.get(reverse("tasks:project", args=[p.public_id])).content.decode()
    assert f'aria-label="Comments on {t.title}"' in board
    for junk in ("before=abc", "before=-1", "before=99999999999999999999", "before=",
                 "before=\u00b2", "before=\u0663", "before=\uff11", "before=1%20", "before=1e3"):
        assert ca.get(url + "?" + junk).status_code == 200


@pytest.mark.django_db
def test_comment_throttle_keeps_typed_text_and_returns_429(monkeypatch):
    from apps.tasks import project_views
    from apps.tasks.models import TaskComment

    monkeypatch.setattr(project_views, "COMMENT_LIMIT", 2)
    a = _u("CM-F")
    p, t = _ptask(a)
    url = reverse("tasks:project_comments", args=[t.public_id])
    ca = _c(a)
    assert ca.post(url, {"body": "one"}).status_code == 302
    assert ca.post(url, {"body": "two"}).status_code == 302
    r = ca.post(url, {"body": "three, typed and must survive"})
    assert r.status_code == 429
    assert "three, typed and must survive" in r.content.decode().split("<textarea")[1]
    assert TaskComment.objects.count() == 2


@pytest.mark.django_db
def test_comments_on_a_deleted_project_or_for_a_deactivated_person_are_404():
    from apps.tasks.models import TaskComment
    from django.utils import timezone

    a = _u("CM-G")
    p, t = _ptask(a)
    url = reverse("tasks:project_comments", args=[t.public_id])
    ca = _c(a)
    assert ca.post(url, {"body": "before"}).status_code == 302
    Project.objects.filter(pk=p.pk).update(deleted_at=timezone.now())
    assert ca.get(url).status_code == 404 and ca.post(url, {"body": "after"}).status_code == 404
    Project.objects.filter(pk=p.pk).update(deleted_at=None)
    assert ca.get(url).status_code == 200
    a.is_active = False
    a.save()
    assert _c(a).get(url).status_code in (302, 403, 404)
    assert pj.can_view(a, p) is False
    assert TaskComment.objects.count() == 1


@pytest.mark.django_db
def test_comment_keeps_four_byte_characters_and_newlines_exactly():
    from apps.tasks.models import TaskComment

    a = _u("CM-H")
    p, t = _ptask(a)
    text = "ok \U0001F600 \U0001F9D1\u200d\U0001F4BB\nline2 \u0915\u094d\u0937"
    _c(a).post(reverse("tasks:project_comments", args=[t.public_id]), {"body": text})
    assert TaskComment.objects.get().body == text


@pytest.mark.django_db
def test_comment_thread_page_query_count_does_not_grow_with_comments():
    a = _u("CM-E")
    p, t = _ptask(a)
    url = reverse("tasks:project_comments", args=[t.public_id])
    ca = _c(a)
    for i in range(3):
        pj.add_comment(a, t, f"c{i}")
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    ca.get(url)  # warm-up: the first request pays one-off session and cache queries
    with CaptureQueriesContext(connection) as small:
        ca.get(url)
    for i in range(40):
        pj.add_comment(a, t, f"d{i}")
    with CaptureQueriesContext(connection) as big:
        ca.get(url)
    assert len(big) == len(small)


@pytest.mark.django_db
def test_comment_route_resolves_in_both_urlconfs_and_is_refused_on_the_iam_host():
    from django.urls import resolve
    from apps.tasks.models import TaskComment

    a = _u("CM-I")
    p, t = _ptask(a)
    path = reverse("tasks:project_comments", args=[t.public_id])
    for conf in ("config.urls", "config.urls_workspace"):
        assert resolve(path, urlconf=conf).view_name == "tasks:project_comments"
    ca = _c(a)
    assert ca.post(path, {"body": "ok"}, HTTP_HOST="app.salowtech.com").status_code == 302
    for client in (ca, Client()):
        for m in (client.get, client.post):
            r = m(path, {"body": "iam"}, HTTP_HOST="iam.salowtech.com")
            assert r.status_code == 404, r.status_code
    assert TaskComment.objects.count() == 1


@pytest.mark.django_db
def test_comment_view_sends_anonymous_users_to_login_even_with_a_post_body():
    from apps.tasks.models import TaskComment

    a = _u("CM-J")
    p, t = _ptask(a)
    path = reverse("tasks:project_comments", args=[t.public_id])
    anon = Client(HTTP_HOST="app.salowtech.com")
    for m in (anon.get, anon.post):
        r = m(path, {"body": "anon"})
        assert r.status_code == 302 and "login" in r["Location"]
    assert TaskComment.objects.count() == 0


def _board_titles(client, p, **params):
    import re

    page = client.get(reverse("tasks:project", args=[p.public_id]), params)
    assert page.status_code == 200
    html = page.content.decode()
    return re.findall(r'<span class="task-title">([^<]*)</span><a class="btn btn-outline btn-small" href="/tasks/projects/task/[^"]*/edit/"', html), html


@pytest.mark.django_db
def test_board_filters_title_milestone_overdue_and_hide_only():
    import datetime

    from zoneinfo import ZoneInfo

    a = _u("BF-A")
    p = pj.create_project(a, "Filt")
    todo, doing, done = list(p.columns.all())
    # today from the wall clock in Asia/Kolkata (settings.TIME_ZONE), not from the code under test
    today = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    y = today - datetime.timedelta(days=1)
    m1 = pj.create_milestone(a, p, "M1")
    t_alpha = pj.add_task(a, p, todo.pk, "Alpha plan", due=y)
    t_beta = pj.add_task(a, p, doing.pk, "Beta 100%_x", due=today)
    t_old_done = pj.add_task(a, p, done.pk, "Gamma done", due=y)
    t_none = pj.add_task(a, p, todo.pk, "Delta")
    pj.set_milestone(a, t_alpha, m1.pk)
    ca = _c(a)
    allt, _ = _board_titles(ca, p)
    assert sorted(allt) == ["Alpha plan", "Beta 100%_x", "Delta", "Gamma done"]
    assert _board_titles(ca, p, q="ALPHA")[0] == ["Alpha plan"], "title search"
    assert _board_titles(ca, p, q="100%_")[0] == ["Beta 100%_x"]  # % and _ are literal
    assert _board_titles(ca, p, q="%")[0] == ["Beta 100%_x"]
    assert _board_titles(ca, p, milestone=str(m1.pk))[0] == ["Alpha plan"], "milestone"
    assert sorted(_board_titles(ca, p, milestone="none")[0]) == ["Beta 100%_x", "Delta", "Gamma done"]
    # overdue = due before today and not in Done: due today is not overdue, Done is excluded
    assert _board_titles(ca, p, overdue="1")[0] == ["Alpha plan"], "overdue"
    titles, html = _board_titles(ca, p, q="alpha", overdue="1", milestone=str(m1.pk))
    assert titles == ["Alpha plan"] and "Showing 1 of 4 tasks" in html, "combined"
    assert _board_titles(ca, p, q="nothing")[0] == [] and "No matching tasks." in _board_titles(ca, p, q="nothing")[1]
    # junk values never error and never filter by an unknown milestone
    for junk in ({"milestone": "\u00b2"}, {"milestone": "99999999999999999999"}, {"milestone": "abc"}, {"overdue": "yes"}, {"q": "x" * 500}):
        assert ca.get(reverse("tasks:project", args=[p.public_id]), junk).status_code == 200
    assert sorted(_board_titles(ca, p, overdue="yes")[0]) == sorted(allt)
    # filtering never changes data
    from apps.tasks.models import ProjectTask

    assert ProjectTask.objects.filter(project=p, deleted_at__isnull=True).count() == 4
    assert t_none.version == 1


@pytest.mark.django_db
def test_board_filter_cannot_use_another_projects_milestone_or_leak_to_a_stranger():
    a, b = _u("BF-B"), _u("BF-C")
    p1, p2 = pj.create_project(a, "One"), pj.create_project(b, "Two")
    foreign = pj.create_milestone(b, p2, "Theirs")
    t = pj.add_task(a, p1, p1.columns.first().pk, "Mine")
    ca = _c(a)
    titles, html = _board_titles(ca, p1, milestone=str(foreign.pk))
    assert titles == ["Mine"] and "Showing" not in html and "Theirs" not in html
    assert _c(b).get(reverse("tasks:project", args=[p1.public_id]), {"q": "Mine"}).status_code == 404
    assert Client(HTTP_HOST="app.salowtech.com").get(reverse("tasks:project", args=[p1.public_id]), {"q": "Mine"}).status_code == 302


@pytest.mark.django_db
def test_board_query_count_does_not_grow_with_tasks_when_filtering():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    a = _u("BF-D")
    p = pj.create_project(a, "Many")
    col = p.columns.first().pk
    ms = pj.create_milestone(a, p, "M")
    pj.add_task(a, p, col, "t0")
    ca = _c(a)
    url = reverse("tasks:project", args=[p.public_id])
    ca.get(url, {"q": "t", "milestone": "none"})
    with CaptureQueriesContext(connection) as small:
        ca.get(url, {"q": "t", "milestone": "none"})
    for i in range(1, 40):
        pj.add_task(a, p, col, f"t{i}")
    with CaptureQueriesContext(connection) as big:
        ca.get(url, {"q": "t", "milestone": "none"})
    assert len(big) == len(small), (len(small), len(big))


def test_overdue_uses_the_configured_time_zone_setting():
    from django.conf import settings

    assert settings.TIME_ZONE == "Asia/Kolkata"


@pytest.mark.django_db
def test_assign_view_lead_only_member_only_stale_checked_and_guarded():
    from apps.tasks.models import ProjectTask

    a, b, c = _u("AS-A"), _u("AS-B"), _u("AS-C")
    p, t = _ptask(a)
    ProjectMember.objects.create(project=p, person=b, added_by=a)  # plain member
    url = reverse("tasks:project_assign", args=[t.public_id])
    ca, cb = _c(a), _c(b)

    def state():
        x = ProjectTask.objects.get(pk=t.pk)
        return x.assignee_id, x.version

    # lead assigns a member
    assert ca.post(url, {"person": b.pk, "version": 1}).status_code == 302
    assert state() == (b.pk, 2)
    # a person who is not a member of this project is refused, nothing changes
    r = ca.post(url, {"person": c.pk, "version": 2}, follow=True)
    assert "not a member" in r.content.decode() and state() == (b.pk, 2)
    r = ca.post(url, {"person": 999999, "version": 2}, follow=True)
    assert "not a member" in r.content.decode() and state() == (b.pk, 2)
    # a stale version is refused
    r = ca.post(url, {"person": a.pk, "version": 1}, follow=True)
    assert "changed while you were looking" in r.content.decode() and state() == (b.pk, 2)
    # a plain member cannot assign
    r = cb.post(url, {"person": "", "version": 2}, follow=True)
    assert "Only a project lead can change project tasks." in r.content.decode()
    assert state() == (b.pk, 2)
    # clear
    assert ca.post(url, {"person": "", "version": 2}).status_code == 302
    assert state() == (None, 3)
    # junk input is 404, never 500, and writes nothing
    for junk in ({"person": "\u00b2", "version": 3}, {"person": "1", "version": "x"}, {"person": "9" * 30, "version": 3}, {"version": ""}):
        assert ca.post(url, junk).status_code == 404, junk
    assert state() == (None, 3)
    # stranger 404, anonymous login, GET 405, IAM host 404, nothing written
    assert _c(c).post(url, {"person": b.pk, "version": 3}).status_code == 404
    anon = Client(HTTP_HOST="app.salowtech.com")
    r = anon.post(url, {"person": b.pk, "version": 3})
    assert r.status_code == 302 and "login" in r["Location"]
    assert ca.get(url).status_code == 405
    assert ca.post(url, {"person": b.pk, "version": 3}, HTTP_HOST="iam.salowtech.com").status_code == 404
    assert state() == (None, 3)
    # the board shows the assignee to members, and the select only to the lead
    ca.post(url, {"person": b.pk, "version": 3})
    assert "Assigned to P AS-B" in cb.get(reverse("tasks:project", args=[p.public_id])).content.decode()
    assert url not in cb.get(reverse("tasks:project", args=[p.public_id])).content.decode()
    assert url in ca.get(reverse("tasks:project", args=[p.public_id])).content.decode()


@pytest.mark.django_db
def test_assign_route_resolves_in_both_urlconfs_and_removed_or_deactivated_people_are_blocked():
    from django.urls import resolve
    from apps.tasks.models import ProjectTask
    from django.utils import timezone

    a, b, c = _u("AS-D"), _u("AS-E"), _u("AS-F")
    p, t = _ptask(a)
    mb = ProjectMember.objects.create(project=p, person=b, added_by=a)
    ProjectMember.objects.create(project=p, person=c, added_by=a)
    url = reverse("tasks:project_assign", args=[t.public_id])
    for conf in ("config.urls", "config.urls_workspace"):
        assert resolve(url, urlconf=conf).view_name == "tasks:project_assign"
    ca = _c(a)

    def state():
        x = ProjectTask.objects.get(pk=t.pk)
        return x.assignee_id, x.version

    # target: a removed member and a deactivated member cannot be assigned
    ProjectMember.objects.filter(pk=mb.pk).update(removed_at=timezone.now())
    board = ca.get(reverse("tasks:project", args=[p.public_id])).content.decode()
    picker = board.split('name="person"')[1].split("</select>")[0]
    assert "P AS-E" not in picker and "P AS-F" in picker and "P AS-D" in picker  # removed member not offered
    ca.post(url, {"person": b.pk, "version": 1})
    assert state() == (None, 1)
    CustomUser.objects.filter(pk=c.pk).update(is_active=False)
    ca.post(url, {"person": c.pk, "version": 1})
    assert state() == (None, 1)
    # actor: a lead who was removed, or deactivated, can no longer assign
    ProjectMember.objects.filter(project=p, person=a).update(role="lead")
    ca.post(url, {"person": a.pk, "version": 1})
    assert state() == (a.pk, 2)
    ProjectMember.objects.filter(project=p, person=a).update(removed_at=timezone.now())
    assert ca.post(url, {"person": "", "version": 2}).status_code == 404
    assert state() == (a.pk, 2)
    ProjectMember.objects.filter(project=p, person=a).update(removed_at=None)
    CustomUser.objects.filter(pk=a.pk).update(is_active=False)
    r = _c(a).post(url, {"person": "", "version": 2})
    assert state() == (a.pk, 2), r.status_code


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_removal_vs_comment(monkeypatch, locked):
    """A member is removed while their comment is between the membership check and the write. With
    the project lock the removal waits for the comment (the comment was posted while they were a
    member); in the control (lock removed) the comment lands after the removal committed.
    Real TiDB only; each round is a fixture removal that takes the project lock like a future remove."""
    import threading

    from django.db import transaction

    from apps.tasks.models import ProjectTask, TaskComment
    from tests.unit.test_drive_services import _threads

    _need_mysql()
    if not locked:
        monkeypatch.setattr(pj, "_lock_project", lambda p: Project.objects.get(pk=p.pk))
    rounds = 2
    saw_commit, outcomes, after_removal = [], [], 0
    for i in range(rounds):
        a, m = _u(f"RC-A{i}{locked}"), _u(f"RC-M{i}{locked}")
        p = pj.create_project(a, "P")
        _member(p, m)
        t = ProjectTask.objects.create(project=p, column=p.columns.first(), title="t", created_by=a)
        committed, paused = threading.Event(), threading.Event()

        def hook(committed=committed, paused=paused):
            paused.set()
            saw_commit.append(committed.wait(timeout=2.0))

        monkeypatch.setattr(pj, "_pause", hook)

        def remove(p=p, m=m, committed=committed, paused=paused):
            assert paused.wait(timeout=10), "the comment never reached its write"
            with transaction.atomic():
                Project.objects.select_for_update().get(pk=p.pk)
                ProjectMember.objects.filter(project=p, person=m).update(removed_at="2026-01-01T00:00:00Z")
            committed.set()

        def comment(t=t, m=m):
            pj.add_comment(m, t, "hello")

        outcomes.append(_threads([remove, comment]))
        removed = not ProjectMember.objects.filter(project=p, person=m, removed_at__isnull=True).exists()
        if removed and TaskComment.objects.filter(task=t, author=m).exists():
            # committed order: the removal committed first and the comment still landed
            after_removal += 1 if saw_commit[-1] else 0
    assert outcomes == [["ok", "ok"]] * rounds, outcomes
    assert saw_commit == [not locked] * rounds, saw_commit
    assert (after_removal == 0) if locked else (after_removal == rounds), (after_removal, rounds)


@pytest.mark.django_db
def test_assigned_page_lists_project_tasks_only_for_current_members():
    from django.test import Client
    from django.urls import reverse
    from django.utils import timezone

    a, m, other = _u("AP-A"), _u("AP-M"), _u("AP-O")
    p = pj.create_project(a, "Assign list proj")
    mem = _member(p, m)
    col = p.columns.first()
    t = pj.add_task(a, p, col.pk, "Given to M")
    pj.add_task(a, p, col.pk, "Not given")
    pj.assign_task(a, t, m)

    def page(u):
        c = Client(HTTP_HOST="app.salowtech.com")
        c.force_login(u)
        r = c.get(reverse("tasks:assigned"))
        assert r.status_code == 200
        return r.content.decode()

    html = page(m)
    assert "Given to M" in html and "Assign list proj" in html and "Not given" not in html
    assert f"/tasks/projects/{p.public_id}/" in html
    assert "Given to M" not in page(other) and "Given to M" not in page(a)  # not the lead, not a stranger
    ProjectMember.objects.filter(pk=mem.pk).update(removed_at=timezone.now())
    assert "Given to M" not in page(m)  # a removed member no longer sees it
    assert "Nothing has been given to you." in page(m)
    ProjectMember.objects.filter(pk=mem.pk).update(removed_at=None)
    assert "Given to M" in page(m)
    Project.objects.filter(pk=p.pk).update(deleted_at=timezone.now())
    assert "Given to M" not in page(m)


@pytest.mark.django_db
def test_assigned_page_query_count_does_not_grow_with_the_number_of_project_tasks(django_assert_max_num_queries):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    a, m = _u("AQ-A"), _u("AQ-M")
    p = pj.create_project(a, "Q")
    _member(p, m)

    def count():
        c = _c(m)
        with CaptureQueriesContext(connection) as ctx:
            assert c.get(reverse("tasks:assigned")).status_code == 200
        return len(ctx)

    pj.assign_task(a, pj.add_task(a, p, p.columns.first().pk, "one"), m)
    count()  # warm the per-process caches (session, permissions) so the two counts compare like with like
    few = count()
    for i in range(12):
        pj.assign_task(a, pj.add_task(a, p, p.columns.first().pk, f"more {i}"), m)
    assert count() == few, "the page runs more queries as tasks are added"


@pytest.mark.django_db
def test_assigned_page_hides_deleted_tasks_and_a_task_whose_assignee_is_not_a_member():
    from django.utils import timezone

    a, m = _u("AE-A"), _u("AE-M")
    p = pj.create_project(a, "Edge proj")
    mem = _member(p, m)
    t1 = pj.add_task(a, p, p.columns.first().pk, "Deleted one")
    t2 = pj.add_task(a, p, p.columns.first().pk, "Stale assignee")
    pj.assign_task(a, t1, m)
    pj.assign_task(a, t2, m)

    def page():
        return _c(m).get(reverse("tasks:assigned")).content.decode()

    assert "Deleted one" in page() and "Stale assignee" in page()  # both listed first
    pj.delete_task(a, t1)
    html = page()
    assert "Deleted one" not in html  # the deleted task is the only change
    assert "Stale assignee" in html  # the other is still listed before the removal
    ProjectMember.objects.filter(pk=mem.pk).update(removed_at=timezone.now())
    assert "Stale assignee" not in page()  # a removed member's old assignment is hidden


@pytest.mark.django_db
def test_assigned_page_deleted_filter_and_membership_filter_each_discriminate(monkeypatch):
    """Controls: with only the deleted-task filter dropped a deleted task shows; with only the
    membership filter dropped a removed member's task shows."""
    from django.utils import timezone

    a, m = _u("AC-A"), _u("AC-M")
    p = pj.create_project(a, "Ctl proj")
    mem = _member(p, m)
    t1 = pj.add_task(a, p, p.columns.first().pk, "Ctl deleted")
    pj.assign_task(a, t1, m)
    pj.delete_task(a, t1)
    real = pj.assigned_in_projects

    def no_deleted_filter(user):
        from apps.tasks.models import ProjectTask

        return ProjectTask.objects.filter(assignee=user, project__members__person=user, project__members__removed_at__isnull=True).select_related("project", "column")

    monkeypatch.setattr(pj, "assigned_in_projects", no_deleted_filter)
    assert "Ctl deleted" in _c(m).get(reverse("tasks:assigned")).content.decode()  # control shows it
    monkeypatch.setattr(pj, "assigned_in_projects", real)
    assert "Ctl deleted" not in _c(m).get(reverse("tasks:assigned")).content.decode()
    t2 = pj.add_task(a, p, p.columns.first().pk, "Ctl removed")
    pj.assign_task(a, t2, m)
    ProjectMember.objects.filter(pk=mem.pk).update(removed_at=timezone.now())

    def no_member_filter(user):
        from apps.tasks.models import ProjectTask

        return ProjectTask.objects.filter(assignee=user, deleted_at__isnull=True).select_related("project", "column")

    monkeypatch.setattr(pj, "assigned_in_projects", no_member_filter)
    assert "Ctl removed" in _c(m).get(reverse("tasks:assigned")).content.decode()  # control shows it
    monkeypatch.setattr(pj, "assigned_in_projects", real)
    assert "Ctl removed" not in _c(m).get(reverse("tasks:assigned")).content.decode()
