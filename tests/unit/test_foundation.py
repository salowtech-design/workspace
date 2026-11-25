"""Shared foundation: object permissions, storage boundary, search hook, jobs, notifications, audit."""

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.models import CustomUser
from apps.core.foundation import jobs, notify, permissions, search, storage
from apps.core.foundation.audit import audit
from apps.core.models import AuditLog, Notification, OutboxEvent


def _user(eid, **kw):
    return CustomUser.objects.create(
        employee_id=eid,
        full_name=eid,
        email=f"{eid.lower()}@salowtech.com",
        role="employee",
        status="active",
        is_active=True,
        **kw,
    )


class Doc:
    def __init__(self, pk, owner):
        self.pk = pk
        self.owner = owner


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setattr(permissions, "_POLICIES", {})
    monkeypatch.setattr(search, "_SEARCHERS", {})
    monkeypatch.setattr(storage, "_BACKEND", None)


@pytest.mark.django_db
def test_permissions_default_deny_and_policy(clean):
    a, b = _user("F-A"), _user("F-B")
    d = Doc(1, a)
    assert permissions.can(a, "view", "doc", d) is False  # nothing registered
    permissions.register_policy("doc", lambda u, act, o: o.owner == u)
    assert permissions.can(a, "view", "doc", d) is True
    assert permissions.can(b, "view", "doc", d) is False
    assert permissions.can(None, "view", "doc", d) is False
    b.is_active = False
    assert permissions.can(b, "view", "doc", Doc(2, b)) is False
    with pytest.raises(PermissionDenied):
        permissions.require(_user("F-C"), "view", "doc", d)


@pytest.mark.django_db
def test_policy_error_refuses_and_duplicate_registration_rejected(clean):
    a = _user("F-D")

    def boom(u, act, o):
        raise RuntimeError("bug")

    permissions.register_policy("x", boom)
    assert permissions.can(a, "view", "x", object()) is False
    with pytest.raises(ValueError):
        permissions.register_policy("x", lambda u, act, o: True)


def test_storage_has_no_backend_until_one_is_chosen(clean):
    with pytest.raises(storage.StorageNotConfigured):
        storage.get_storage()

    class Mem(storage.StorageBackend):
        def __init__(self):
            self.d = {}

        def put(self, key, data, *, content_type=""):
            self.d[key] = data

        def open(self, key):
            import io

            return io.BytesIO(self.d[key])

        def delete(self, key):
            self.d.pop(key, None)

        def exists(self, key):
            return key in self.d

    storage.set_storage(Mem())
    s = storage.get_storage()
    s.put("k", b"hi")
    assert s.open("k").read() == b"hi" and s.exists("k")
    s.delete("k")
    assert not s.exists("k")


@pytest.mark.django_db
def test_search_drops_results_the_user_may_not_view(clean):
    a, b = _user("F-E"), _user("F-F")
    docs = [Doc(1, a), Doc(2, b)]
    permissions.register_policy("doc", lambda u, act, o: o.owner == u)
    search.register_searcher(
        "docs", lambda u, q, n: [("doc", d, f"Doc {d.pk}", f"/d/{d.pk}") for d in docs]
    )
    assert [h.object_id for h in search.search(a, "doc")] == ["1"]
    assert [h.object_id for h in search.search(b, "doc")] == ["2"]
    assert search.search(a, "d") == []  # too short
    assert search.search(a, "doc", types=["other"]) == []
    with pytest.raises(ValueError):
        search.register_searcher("docs", lambda u, q, n: [])


@pytest.mark.django_db
def test_non_active_status_is_refused_and_prefiltered_searcher_not_crowded_out(clean):
    a = _user("F-S1")
    permissions.register_policy("doc", lambda u, act, o: o.owner == u)
    a.status = "suspended"
    assert permissions.can(a, "view", "doc", Doc(1, a)) is False
    a.status = "active"
    assert permissions.can(a, "view", "doc", Doc(1, a)) is True
    other = _user("F-S2")
    pool = [Doc(i, other) for i in range(100)] + [Doc(500, a)]

    def searcher(user, q, n):  # pre-filters by owner inside the query, as modules must
        return [("doc", d, f"D{d.pk}", f"/d/{d.pk}") for d in pool if d.owner == user][:n]

    search.register_searcher("docs", searcher)
    assert [h.object_id for h in search.search(a, "doc")] == ["500"]


@pytest.mark.django_db
def test_jobs_run_through_the_outbox_with_retries(monkeypatch):
    from apps.core import outbox

    monkeypatch.setattr(outbox, "EVENT_HANDLERS", {})
    seen = []
    jobs.register_job("ping", lambda p: seen.append(p["n"]))
    jobs.enqueue_job("ping", {"n": 1}, job_id="job-a")
    jobs.enqueue_job("ping", {"n": 1}, job_id="job-a")  # idempotent id
    assert OutboxEvent.objects.filter(event_type="job.ping").count() == 1
    assert jobs.run_jobs() == (1, 0)
    assert seen == [1]

    def bad(p):
        raise RuntimeError("x")

    jobs.register_job("bad", bad)
    jobs.enqueue_job("bad", {})
    for _ in range(3):
        jobs.run_jobs()
    assert OutboxEvent.objects.get(event_type="job.bad").status == "failed"
    with pytest.raises(ValueError):
        jobs.register_job("bad", lambda p: None)
    jobs.register_job("bad", bad)  # same handler again is a no-op


@pytest.mark.django_db
def test_notification_links_are_internal_and_target_permission_is_checked(clean):
    a, b = _user("F-N1"), _user("F-N2")
    for bad in ("//evil.example/x", "https://evil.example", "javascript:alert(1)", "x/y", "/a\\b"):
        with pytest.raises(ValueError):
            notify.notify(a, "k", "t", bad)
    permissions.register_policy("doc", lambda u, act, o: o.owner == u)
    d = Doc(1, a)
    assert notify.notify(a, "k", "t", "/d/1/", check=("doc", d)).link == "/d/1/"
    with pytest.raises(PermissionError):
        notify.notify(b, "k", "t", "/d/1/", check=("doc", d))
    assert Notification.objects.filter(user=b).count() == 0


@pytest.mark.django_db
def test_notifications_and_audit_helper():
    a = _user("F-G")
    notify.notify(a, "chat.mention", "Ana mentioned you", "/chat/1/")
    notify.notify(a, "chat.mention", "x" * 500)
    assert notify.unread_count(a) == 2
    assert Notification.objects.filter(user=a).first().title == "x" * 200
    assert notify.mark_all_read(a) == 2 and notify.unread_count(a) == 0
    row = audit(a, "foundation.test", "doc", 7, note="ok", skip=None, password="p")
    assert row.actor_id == a.pk and row.target_id == "7"
    assert row.payload_summary == {"note": "ok", "password": "[REDACTED]"}
    assert AuditLog.objects.filter(action="foundation.test").count() == 1
