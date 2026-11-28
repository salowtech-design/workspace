"""Signed-in cross-account walk: user B (an ordinary employee, no relation to A) requests every app-host route that takes
an object id, filled with the REAL ids of objects owned by user A. Test client, real TiDB when run by tests/tidb_unit.

For every such request (GET and POST) it asserts:
  1. no 5xx;
  2. no response body contains any of A's seeded titles or text (A's markers);
  3. a full before/after snapshot of every row of every model (field values, so updates and deletions show, not only
     counts) is unchanged, except tables that hold throttle counters, sessions and audit or security logs;
  4. control: A herself reaches 200 with the marker visible on the GET detail page of eight kinds only (folder, list,
     project, event, group chat, post, certificate, automation). Not controlled by a GET: the DM, file, task, project task,
     milestone and notification ids; those are NOT controlled by a GET; DM, milestone and notification have no owner control at all (the mutation test below does not cover them).
The requests above are mostly EMPTY POSTs, which mostly stop at form validation. Valid-payload mutations are in
test_valid_mutation_by_owner_succeeds_and_by_unrelated_user_changes_nothing.
Directory: the profile and team GET pages are intended to show colleagues, so only a marker there is allowed on any method of the profile page itself; the
directory mutating route (assign manager) is checked like every other.
Not covered, stated plainly: team detail for a real team (none seeded);
records shared on purpose (a share, a guest, a member) are not walked and not asserted; routes with no id
(B's own pages) are not walked; the test client sends no CSRF token check; one machine, one run; this is a test of the
listed requests, not a proof of object authorization."""

import datetime as dt
import re

import pytest
from django.apps import apps as django_apps
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.accounts.models import CustomUser

HOST = "app.salowtech.com"
MARK = "ZqAlpha"
INTENDED_VISIBLE_GET = {"directory/profile/<str:employee_id>/", "directory/teams/<str:team_code>/"}
# Routes whose objects (passkeys, device approvals, recovery requests, security alerts, sessions, backup runs, IAM
# transactions) this test does not seed. They are NOT covered here; pinned so a new unmapped route fails the test.
# /verify/<id> is public by design and covered by the anonymous walk.
NOT_COVERED = {
    "auth/admin/backups/<int:run_id>/check/", "auth/devices/approval/<uuid:request_id>/admin-approve/",
    "auth/devices/approval/<uuid:request_id>/decision/", "auth/devices/approval/<uuid:request_id>/email-otp/",
    "auth/devices/approval/<uuid:request_id>/face/", "auth/devices/approval/<uuid:request_id>/status/",
    "auth/devices/approval/<uuid:request_id>/wait/", "auth/passkeys/<int:passkey_id>/delete/",
    "auth/recovery/console/<int:request_id>/approve/", "auth/recovery/console/<int:request_id>/reject/",
    "auth/security/alerts/<int:alert_id>/acknowledge/", "auth/security/sessions/<str:session_key>/terminate/",
    "iam/authentication/transactions/<str:transaction_id>/", "verify/<str:identifier>/",
}  # fmt: skip
# Tables an unrelated user's requests may write by design: throttle counters, Django sessions, auth and face logs and the
# public verification log. Everything else, including work sessions and login-session rows, stays in the snapshot.
EXCLUDED = {"accounts.authevent", "accounts.loginattempt", "core.ratelimitbucket", "core.ratelimitrecord", "sessions.session",
            "certificates.certificateverificationlog", "face.faceverificationlog"}  # fmt: skip


def _u(eid, name):
    return CustomUser.objects.create(employee_id=eid, full_name=name, email=f"{eid.lower()}@salowtech.com",
                                     role="employee", status="active", is_active=True)  # fmt: skip


def _routes():
    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                yield prefix + str(p.pattern)

    return sorted(set(walk(get_resolver("config.urls").url_patterns)))


def _snapshot():
    snap = {}
    for m in django_apps.get_models():
        label = m._meta.label_lower
        if m._meta.proxy or label in EXCLUDED:
            continue
        for row in m.objects.all().values():
            row.pop("last_activity", None)  # a session row's own touch time
            row.pop("last_login", None)  # force_login of the requester itself updates this
            snap[(label, str(row.get("id", row.get("pk", row))))] = repr(sorted(row.items(), key=lambda kv: kv[0]))
    return snap


@pytest.fixture
def world(db):
    from apps.certificates.services import issue_certificate
    from apps.chat import services as cs
    from apps.core.models import Notification
    from apps.drive import services as ds
    from apps.events import services as es
    from apps.automation.models import WfDefinition
    from apps.posts.models import Post
    from apps.tasks import projects as pj
    from apps.tasks import services as ts

    a, b, c, d = _u("XA-1", f"{MARK} Person"), _u("XB-1", "Bravo Person"), _u("XC-1", "Charlie Person"), _u("XD-1", "Delta Person")
    w = {"a": a, "b": b, "admin": CustomUser.objects.create(employee_id="XS-2", full_name="Admin Two", email="xs2@salowtech.com", role="admin", status="active", is_active=True)}  # fmt: skip
    w["folder"] = ds.create_folder(a, f"{MARK} folder")
    w["file"] = ds.create_file(a, f"{MARK} file.txt", folder=w["folder"])
    lst = ts.create_list(a, f"{MARK} list")
    w["list"], w["task"] = lst, ts.create_task(a, lst, f"{MARK} task", notes=f"{MARK} notes")
    pr = pj.create_project(a, f"{MARK} project")
    w["project"] = pr
    w["ptask"] = pj.add_task(a, pr, pr.columns.first().pk, f"{MARK} project task")
    w["milestone"] = pj.create_milestone(a, pr, f"{MARK} milestone")
    w["event"] = es.create_event(a, f"{MARK} event", dt.datetime(2026, 12, 3, 10), dt.datetime(2026, 12, 3, 11),
                                 "Asia/Kolkata", [c.pk], description=f"{MARK} description")  # fmt: skip
    w["dm"] = cs.open_dm(a, c.pk)
    w["group"] = cs.create_group(a, f"{MARK} group", [c.pk, d.pk])
    w["post"] = Post.objects.create(author=a, title=f"{MARK} post", content=f"{MARK} content",
                                    category=Post.Category.DAILY_STANDUP, status=Post.Status.SUBMITTED)  # fmt: skip
    w["cert"] = issue_certificate(recipient=a, title=f"{MARK} certificate", description=f"{MARK} citation", issued_by=CustomUser.objects.create_superuser(employee_id="XS-1", full_name="Issuer Admin", password="SuperMasterAdminPassword2026!"))
    w["notice"] = Notification.objects.create(user=a, kind="test", title=f"{MARK} notice", link="/tasks/")
    w["wf"] = WfDefinition.objects.create(name=f"{MARK} automation", owner=a, definition={})
    return w


def _fill(route, w, variant):
    """Substitute each converter with the real id of A's object that the name and route position point to."""
    kind_item = {"file": w["file"], "folder": w["folder"]}

    def val(m):
        name = m.group(2)
        if name == "kind":
            return variant
        if name == "item_id":
            return str(kind_item[variant].public_id)
        if name == "space_id":
            return str((w["dm"] if variant == "dm" else w["group"]).public_id)
        if name == "folder_id":
            return str(w["folder"].public_id)
        if name == "event_id":
            return str(w["event"].public_id)
        if name == "list_id":
            return str(w["list"].public_id)
        if name == "task_id":
            return str((w["ptask"] if route.startswith("tasks/projects/") else w["task"]).public_id)
        if name == "project_id":
            return str(w["project"].public_id)
        if name == "milestone_id":
            return str(w["milestone"].pk)
        if name == "post_id":
            return str(w["post"].pk)
        if name == "cert_uuid":
            return str(w["cert"].uuid)
        if name == "notice_id":
            return str(w["notice"].pk)
        if name == "public_id":
            return str(w["wf"].public_id)
        if name == "root_seq":
            return "1"
        if name == "employee_id":
            return w["a"].employee_id
        if name == "team_code":
            return "x"
        return None

    out = re.sub(r"<(?:(\w+):)?(\w+)>", lambda m: val(m) or "\0", route)
    return None if "\0" in out else "/" + out


def _variants(route):
    if "<str:kind>" in route:
        return ("file", "folder")
    if "<uuid:item_id>" in route:
        return ("file",)
    if "<uuid:space_id>" in route:
        return ("dm", "group")
    return ("-",)


def _marker_in(r):
    headers = " ".join(f"{k}: {v}" for k, v in r.headers.items())  # includes Location and Content-Disposition
    body = b"".join(r.streaming_content) if getattr(r, "streaming", False) else r.content
    return MARK.encode() in body or MARK in headers


def test_cross_account_requests_change_nothing_and_leak_nothing(world):
    w = world
    routes = [r for r in _routes() if re.search(r"<", r)]
    # control: A reaches her own detail pages and sees the marker
    ca = Client(HTTP_HOST=HOST)
    ca.force_login(w["a"])
    controls = {
        "folder": f"/drive/folder/{w['folder'].public_id}/", "list": f"/tasks/list/{w['list'].public_id}/",
        "project": f"/tasks/projects/{w['project'].public_id}/", "event": f"/calendar/event/{w['event'].public_id}/",
        "group": f"/chat/space/{w['group'].public_id}/", "post": f"/posts/{w['post'].pk}/",
        "cert": f"/certificates/{w['cert'].uuid}/", "automation": f"/automation/{w['wf'].public_id}/",
    }  # fmt: skip
    unreached = {k: ca.get(u).status_code for k, u in controls.items() if ca.get(u).status_code != 200 or MARK.encode() not in ca.get(u).content}  # fmt: skip
    assert not unreached, f"control failed (A cannot see her own object): {unreached}"
    before = _snapshot()
    problems, n, unfilled = [], 0, []
    for route in routes:
        for variant in _variants(route):
            url = _fill(route, w, variant)
            if url is None:
                unfilled.append(route)
                continue
            for method in ("get", "post"):
                n += 1
                c = Client(HTTP_HOST=HOST, REMOTE_ADDR=f"10.8.{n // 250}.{n % 250 + 1}")
                c.force_login(w["b"])
                r = getattr(c, method)(url)
                if r.status_code >= 500:
                    problems.append(f"5xx {r.status_code} {method.upper()} {url}")
                if _marker_in(r) and route not in INTENDED_VISIBLE_GET:
                    problems.append(f"marker in {r.status_code} {method.upper()} {url}")
    after = _snapshot()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        problems.append(f"rows changed by B: {changed[:15]} (total {len(changed)})")
    assert set(unfilled) == NOT_COVERED, sorted(set(unfilled) ^ NOT_COVERED)
    assert not problems, "\n".join(problems[:40])
    excluded = sorted(EXCLUDED)
    open("/tmp/cross_account_excluded.txt", "w").write(" ".join(excluded) + "\n")
    open("/tmp/cross_account_counts.txt", "w").write(f"{n} requests, {len(routes)} id routes, {len(routes) - len(NOT_COVERED)} walked, {len(before)} rows snapshotted\n")


def _actions(w):
    """(key, url, valid payload). Each is a delete, move-like, complete or edit with a payload the view accepts."""
    f, t, ev = w["folder"], w["task"], w["event"]
    return {
        "task-delete": (f"/tasks/task/{t.public_id}/delete/", {"confirm": "yes"}),
        "task-done": (f"/tasks/task/{t.public_id}/done/", {"version": t.version}),
        "task-edit": (f"/tasks/task/{t.public_id}/edit/", {"version": t.version, "title": "ZqHijack title"}),
        "list-rename": (f"/tasks/list/{w['list'].public_id}/rename/", {"new_name": "ZqHijack list"}),
        "list-delete": (f"/tasks/list/{w['list'].public_id}/delete/", {"confirm": "yes"}),
        "project-rename": (f"/tasks/projects/{w['project'].public_id}/rename/", {"new_name": "ZqHijack project"}),
        "project-task-delete": (f"/tasks/projects/task/{w['ptask'].public_id}/delete/", {"confirm": "yes"}),
        "drive-rename-file": (f"/drive/file/{w['file'].public_id}/rename/", {"name": "ZqHijack.txt"}),
        "drive-trash-folder": (f"/drive/folder/{f.public_id}/trash/", {}),
        "drive-share-file": (f"/drive/file/{w['file'].public_id}/share/", {"employee_id": w["b"].employee_id, "role": "viewer"}),
        "event-cancel": (f"/calendar/event/{ev.public_id}/cancel/", {"confirm": "yes"}),
        "chat-rename-group": (f"/chat/space/{w['group'].public_id}/rename/", {"name": "ZqHijack group"}),
        "chat-send-group": (f"/chat/space/{w['group'].public_id}/send/", {"body": "ZqHijack message", "client_msg_id": "zq-1"}),
        "directory-assign-manager": (f"/directory/profile/{w['a'].employee_id}/manager/", {"manager": w["b"].employee_id}),
        "automation-toggle": (f"/automation/{w['wf'].public_id}/enable/", {}),
    }  # fmt: skip


ACTION_KEYS = ["task-delete", "task-done", "task-edit", "list-rename", "list-delete", "project-rename", "project-task-delete",
               "drive-rename-file", "drive-trash-folder", "drive-share-file", "event-cancel", "chat-rename-group",
               "chat-send-group", "directory-assign-manager", "automation-toggle"]  # fmt: skip


@pytest.mark.parametrize("key", ACTION_KEYS)
def test_valid_mutation_by_owner_succeeds_and_by_unrelated_user_changes_nothing(world, key):
    """In one seeded state, per route: the unrelated user B sends a valid payload FIRST and nothing changes; then the owner
    sends the same payload and at least one row changes, which shows the payload is valid and the route acts. 'Changes'
    means any field of any row outside the excluded tables; it does not check WHICH row changed. Fifteen routes are
    controlled this way (task delete, done, edit; list rename, delete; project rename; project task delete; Drive file
    rename, folder trash, file share; event cancel; group chat rename and send; automation toggle; directory assign
    manager, whose owner role is an admin, so B's denial there is a role result, not an ownership result). The DM,
    milestone and notification ids are NOT controlled by any owner mutation here. Not tested: move, restore, respond,
    milestone edit, certificate revoke, post comment. last_activity and last_login are stripped from every model's rows
    before comparing, so a change to only those fields is invisible."""
    w = world
    url, payload = _actions(w)[key]
    base = _snapshot()
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR="10.6.0.1")
    c.force_login(w["b"])
    base = _snapshot()
    rb = c.post(url, payload)
    after_b = _snapshot()
    changed_b = sorted(k for k in set(base) | set(after_b) if base.get(k) != after_b.get(k))
    assert rb.status_code < 500, f"B got {rb.status_code}"
    assert not _marker_in(rb), "A's marker in B's response"
    assert not changed_b, f"B changed rows {changed_b[:8]} via {key}"
    ca = Client(HTTP_HOST=HOST, REMOTE_ADDR="10.6.0.2")
    ca.force_login(w["admin"] if key == "directory-assign-manager" else w["a"])  # the role that may assign managers
    base = _snapshot()
    ra = ca.post(url, payload)
    after_a = _snapshot()
    changed_a = sorted(k for k in set(base) | set(after_a) if base.get(k) != after_a.get(k))
    assert ra.status_code < 500, f"A got {ra.status_code}"
    assert changed_a, f"owner A's valid {key} request changed nothing (status {ra.status_code}); the payload is not valid"
    with open("/tmp/cross_account_actions.txt", "a") as fh:
        fh.write(f"{key}: B {rb.status_code} no change; A {ra.status_code} changed {len(changed_a)} row(s)\n")
