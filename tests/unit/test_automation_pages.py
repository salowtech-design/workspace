import pytest
from django.test import Client

from apps.accounts.models import CustomUser
from apps.automation import engine
from apps.automation.models import WfDefinition, WfRun
from apps.core.models import Notification

HOST = "app.salowtech.com"
pytestmark = pytest.mark.django_db
EXPECT_INACTIVE = 302  # the sign-in redirect for a suspended session, not a 404


def _u(eid, status="active"):
    return CustomUser.objects.create_user(
        employee_id=eid, full_name=f"P {eid}", password="ValidStrongPassword123!",
        role="employee", status=status,
    )  # fmt: skip


def _c(user):
    c = Client(HTTP_HOST=HOST)
    c.force_login(user)
    return c


def _form(name="Morning check", **extra):
    d = {"name": name, "type0": "set_value", "key0": "mode", "value0": "on",
         "type1": "stop_unless_equals", "key1": "mode", "equals1": "on",
         "type2": "notify_owner", "title2": "Check done"}  # fmt: skip
    d.update(extra)
    return d


def test_pages_need_sign_in_workspace_host_and_active_status():
    a = _u("AU-1")
    for path in ("/automation/", "/automation/new/"):
        assert Client(HTTP_HOST=HOST).get(path).status_code == 302
    ca = _c(a)
    assert ca.get("/automation/").status_code == 200
    assert 'href="/automation/"' in ca.get("/chat/").content.decode()
    assert ca.get("/automation/", HTTP_HOST="iam.salowtech.com").status_code == 404
    off = _u("AU-OFF", status="suspended")
    assert _c(off).get("/automation/").status_code in (302, 404)


def test_create_starts_off_and_shows_honest_limits():
    a = _u("AU-2")
    c = _c(a)
    r = c.post("/automation/new/", _form())
    assert r.status_code == 302
    wf = WfDefinition.objects.get(owner=a)
    assert wf.enabled is False and wf.definition["trigger"] == {"type": "manual"}
    assert [s["type"] for s in wf.definition["steps"]] == [
        "set_value", "stop_unless_equals", "notify_owner",
    ]  # fmt: skip
    html = c.get(r["Location"]).content.decode()
    assert "automation/%s/run/" % wf.public_id not in html and "Switch on" in html
    home = c.get("/automation/").content.decode()
    assert "not connected yet" in home and "cannot be deleted yet" in home


def test_invalid_definitions_are_refused_with_a_message_and_keep_the_form():
    a = _u("AU-3")
    c = _c(a)
    for bad in (
        _form(name=""),
        {"name": "x"},
        {"name": "x", "type0": "notify_owner", "title0": ""},
        {"name": "x", "type0": "run_shell"},
        {"name": "x", "type0": "set_value", "key0": "k", "value0": "v" * 201},
    ):
        r = c.post("/automation/new/", bad)
        assert r.status_code == 400
    assert WfDefinition.objects.count() == 0
    r = c.post(
        "/automation/new/", {"name": "x", "type0": "set_value", "key0": "k", "value0": "v" * 201}
    )
    assert "v" * 50 in r.content.decode()


def test_other_people_cannot_see_edit_switch_or_run_a_workflow():
    a, b = _u("AU-4"), _u("AU-5")
    wf = engine.create_definition(
        a,
        "mine",
        {"trigger": {"type": "manual"}, "steps": [{"type": "notify_owner", "title": "t"}]},
    )
    cb = _c(b)
    p = f"/automation/{wf.public_id}/"
    assert cb.get(p).status_code == 404 and cb.get(p + "edit/").status_code == 404
    assert cb.post(p + "enable/", {"enabled": "1"}).status_code == 404
    assert cb.post(p + "run/").status_code == 404
    assert "mine" not in cb.get("/automation/").content.decode()
    wf.refresh_from_db()
    assert wf.enabled is False and WfRun.objects.count() == 0


def test_run_now_needs_switch_on_then_runs_and_notifies_only_the_owner():
    a, b = _u("AU-6"), _u("AU-7")
    c = _c(a)
    c.post("/automation/new/", _form())
    wf = WfDefinition.objects.get(owner=a)
    p = f"/automation/{wf.public_id}/"
    c.post(p + "run/", {"token": "a" * 32})
    assert WfRun.objects.count() == 0  # off: nothing started
    c.post(p + "enable/", {"enabled": "1"})
    c.post(p + "run/", {"token": "b" * 32})
    run = WfRun.objects.get()
    assert run.state == "succeeded" and run.steps.count() == 3
    assert Notification.objects.filter(user=a, title="Check done").count() == 1
    assert Notification.objects.filter(user=b).count() == 0
    c.post(p + "run/", {"token": "b" * 32})  # a double press with the same page token
    assert WfRun.objects.count() == 1
    assert Notification.objects.filter(user=a, title="Check done").count() == 1
    c.post(p + "run/", {"token": "c" * 32})  # a fresh page is a new run
    assert WfRun.objects.count() == 2
    c.post(p + "run/", {})  # no token: refused
    assert WfRun.objects.count() == 2
    assert "succeeded" in c.get(p).content.decode()


def test_switch_off_cancels_open_runs_and_edit_makes_a_new_version():
    a = _u("AU-8")
    c = _c(a)
    c.post("/automation/new/", _form())
    wf = WfDefinition.objects.get(owner=a)
    p = f"/automation/{wf.public_id}/"
    c.post(p + "enable/", {"enabled": "1"})
    queued = engine.start_run(wf, "k1")
    c.post(p + "enable/", {"enabled": "0"})
    queued.refresh_from_db()
    assert queued.state == "cancelled"
    r = c.post(p + "edit/", {"type0": "notify_owner", "title0": "v2", "version": "1"})
    assert r.status_code == 302
    wf.refresh_from_db()
    assert wf.version == 2 and wf.definition["steps"] == [{"type": "notify_owner", "title": "v2"}]
    assert queued.version == 1
    bad = c.post(p + "edit/", {"type0": "nope", "version": "2"})
    wf.refresh_from_db()
    assert bad.status_code == 400 and wf.version == 2


def test_names_and_errors_are_escaped_and_the_workflow_cap_holds():
    a = _u("AU-9")
    c = _c(a)
    c.post("/automation/new/", _form(name="<script>alert(1)</script>"))
    assert "<script>alert(1)" not in c.get("/automation/").content.decode()
    for i in range(19):
        c.post("/automation/new/", _form(name=f"w{i}"))
    assert WfDefinition.objects.filter(owner=a).count() == 20
    assert c.post("/automation/new/", _form(name="over")).status_code == 400
    assert WfDefinition.objects.filter(owner=a).count() == 20


def test_event_trigger_workflow_is_shown_as_not_connected_and_not_editable_here():
    a = _u("AU-10")
    wf = engine.create_definition(
        a,
        "ev",
        {
            "trigger": {"type": "event", "event_type": "x.y"},
            "steps": [{"type": "notify_owner", "title": "t"}],
        },
    )
    c = _c(a)
    p = f"/automation/{wf.public_id}/"
    assert "not connected yet" in c.get(p).content.decode()
    assert c.get(p + "edit/").status_code == 302


def test_edit_is_refused_for_manual_definitions_the_form_cannot_round_trip():
    a = _u("AU-11")
    c = _c(a)
    shapes = [
        [{"type": "notify_owner", "title": "t", "key": "k"}],
        [{"type": "set_value", "key": "k", "value": 5}],
        [{"type": "notify_owner", "title": " padded "}],
        [{"type": "set_value", "key": "k ", "value": "v"}],
        [{"type": "set_value", "key": "k", "value": "v"}] * 11,
    ]
    for i, steps in enumerate(shapes):
        wf = engine.create_definition(a, f"odd{i}", {"trigger": {"type": "manual"}, "steps": steps})
        r = c.post(
            f"/automation/{wf.public_id}/edit/",
            {"type0": "notify_owner", "title0": "x", "version": "1"},
        )
        assert r.status_code == 302
        wf.refresh_from_db()
        assert wf.version == 1 and wf.definition["steps"] == steps


def test_event_trigger_cannot_be_run_by_hand_even_when_enabled():
    a = _u("AU-12")
    wf = engine.create_definition(
        a,
        "ev",
        {
            "trigger": {"type": "event", "event_type": "x.y"},
            "steps": [{"type": "notify_owner", "title": "t"}],
        },
    )
    engine.set_enabled(a, wf, True)
    c = _c(a)
    p = f"/automation/{wf.public_id}/"
    assert "Run now" not in c.get(p).content.decode()
    c.post(p + "run/", {"token": "d" * 32})
    assert WfRun.objects.count() == 0


def test_stale_edit_is_refused_and_failed_run_is_shown_without_hiding_errors(monkeypatch):
    a = _u("AU-13")
    c = _c(a)
    c.post("/automation/new/", _form())
    wf = WfDefinition.objects.get(owner=a)
    p = f"/automation/{wf.public_id}/"
    assert (
        c.post(p + "edit/", {"type0": "notify_owner", "title0": "x", "version": "1"}).status_code
        == 302
    )
    stale = c.post(p + "edit/", {"type0": "notify_owner", "title0": "y", "version": "1"})
    wf.refresh_from_db()
    assert (
        stale.status_code == 400 and wf.version == 2 and wf.definition["steps"][0]["title"] == "x"
    )
    c.post(p + "enable/", {"enabled": "1"})

    def boom(run, step, ctx):
        raise RuntimeError("step blew up")

    monkeypatch.setattr(engine, "_apply", boom)
    r = c.post(p + "run/", {"token": "e" * 32}, follow=True)
    run = WfRun.objects.get()
    page = r.content.decode()
    assert run.state == "failed" and "Run failed" in page and "failed" in page


def test_csrf_is_enforced_on_state_changes():
    a = _u("AU-14")
    c = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)
    c.force_login(a)
    assert c.post("/automation/new/", _form()).status_code == 403
    assert WfDefinition.objects.count() == 0


def test_inactive_account_is_refused_on_every_page_and_action():
    a = _u("AU-15")
    wf = engine.create_definition(
        a, "w", {"trigger": {"type": "manual"}, "steps": [{"type": "notify_owner", "title": "t"}]}
    )
    engine.set_enabled(a, wf, True)
    a.status = "suspended"
    a.save()
    c = _c(a)
    p = f"/automation/{wf.public_id}/"
    got = [
        c.get("/automation/").status_code,
        c.get(p).status_code,
        c.post(p + "run/", {"token": "f" * 32}).status_code,
        c.post(p + "enable/", {"enabled": "0"}).status_code,
    ]
    assert set(got) == {EXPECT_INACTIVE} and not WfRun.objects.exists()
    wf.refresh_from_db()
    assert wf.enabled
