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
def test_assign_noop_stale_and_updated_at_and_member_pages_stay_separate():
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
    cm = _c(m)  # the assigned member's personal and assigned pages do not show project tasks
    assert b"projtask" not in cm.get(reverse("tasks:assigned")).content
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
def test_comments_are_oldest_first_and_cap_shows_newest():
    a = _u("CM-D")
    p, t = _ptask(a)
    for i in range(pj.COMMENTS_SHOWN + 3):
        pj.add_comment(a, t, f"c{i}")
    rows, total = pj.comments_of(t)
    assert total == pj.COMMENTS_SHOWN + 3 and len(rows) == pj.COMMENTS_SHOWN
    assert rows[0].body == "c3" and rows[-1].body == f"c{pj.COMMENTS_SHOWN + 2}"
    page = _c(a).get(reverse("tasks:project_comments", args=[t.public_id])).content.decode()
    assert f"Showing the newest {pj.COMMENTS_SHOWN} of {pj.COMMENTS_SHOWN + 3}" in page
    # the project board links to the thread only for tasks, and the card shows the link
    board = _c(a).get(reverse("tasks:project", args=[p.public_id])).content.decode()
    assert reverse("tasks:project_comments", args=[t.public_id]) in board


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
