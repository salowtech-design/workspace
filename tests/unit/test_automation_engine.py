import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.automation import engine as en
from apps.automation.models import WfRun, WfStepResult
from apps.core.models import Notification

pytestmark = pytest.mark.django_db


def _u(eid):
    return CustomUser.objects.create_user(
        employee_id=eid, full_name=f"P {eid}", password="ValidStrongPassword123!",
        role="employee", status="active",
    )  # fmt: skip


def _wf(owner, steps, enabled=True):
    wf = en.create_definition(owner, "w", {"trigger": {"type": "manual"}, "steps": steps})
    return en.set_enabled(owner, wf, enabled)


NOTE = {"type": "notify_owner", "title": "Done"}


def test_definition_rejects_unknown_steps_and_extra_keys():
    me = _u("AU-1")
    bad = [
        {"type": "run_python", "title": "x"},
        {"type": "notify_owner", "title": "x", "to": "someone"},
        {"type": "notify_owner", "title": ""},
        {"type": "set_value", "key": "a", "value": {"nested": 1}},
    ]
    for step in bad:
        with pytest.raises(ValidationError):
            en.create_definition(me, "w", {"trigger": {"type": "manual"}, "steps": [step]})
    with pytest.raises(ValidationError):
        en.create_definition(me, "w", {"trigger": {"type": "manual"}, "steps": [NOTE] * 21})
    with pytest.raises(ValidationError):
        en.create_definition(me, "w", {"trigger": {"type": "cron"}, "steps": [NOTE]})


def test_duplicate_trigger_gives_one_run_and_one_effect():
    me = _u("AU-2")
    wf = _wf(me, [NOTE])
    a, b = en.start_run(wf, "evt-1"), en.start_run(wf, "evt-1")
    assert a.pk == b.pk and WfRun.objects.count() == 1
    assert en.execute(a.pk) == "succeeded"
    assert en.execute(a.pk) is None  # finished runs are not claimed again
    assert Notification.objects.filter(user=me, kind="automation").count() == 1


def test_notice_goes_to_the_owner_only():
    me, other = _u("AU-3"), _u("AU-4")
    en.execute(en.start_run(_wf(me, [NOTE]), "k").pk)
    assert Notification.objects.filter(user=other).count() == 0


def test_lease_blocks_second_worker_then_expired_lease_resumes_at_unfinished_step():
    me = _u("AU-5")
    wf = _wf(me, [NOTE, {"type": "set_value", "key": "a", "value": 1}, NOTE])
    run = en.start_run(wf, "k")
    now = timezone.now()
    assert en._claim(run.pk, now) is not None  # worker A holds the lease
    assert en.execute(run.pk, now=now) is None  # worker B refused
    # worker A did step 0 and died
    WfStepResult.objects.create(run=run, step_index=0, state="ok", idempotency_key=en._key(run, 0))
    Notification.objects.create(user=me, kind="automation", title="Done")
    WfRun.objects.filter(pk=run.pk).update(next_step=1)
    later = now + en.LEASE + dt.timedelta(seconds=1)
    assert en.execute(run.pk, now=later) == "succeeded"
    assert list(run.steps.order_by("step_index").values_list("step_index", flat=True)) == [0, 1, 2]
    assert (
        Notification.objects.filter(user=me, kind="automation").count() == 2
    )  # step 0 not repeated


def test_a_failing_effect_rolls_back_its_step_and_fails_the_run():
    me = _u("AU-6")
    wf = _wf(me, [NOTE, {"type": "notify_owner", "title": "x" * 200}])
    run = en.start_run(wf, "k")
    # make step 1 fail by deactivating nothing: break the owner row reference instead
    from unittest import mock

    with mock.patch.object(en, "notify", side_effect=[None, RuntimeError("boom")]):
        assert en.execute(run.pk) == "failed"
    run.refresh_from_db()
    assert run.state == "failed" and "boom" in run.error
    assert list(run.steps.values_list("step_index", flat=True)) == [0]  # step 1 left no record


def test_condition_stops_the_rest_and_disabling_cancels_queued_runs():
    me = _u("AU-7")
    wf = _wf(me, [
        {"type": "set_value", "key": "a", "value": 1},
        {"type": "stop_unless_equals", "key": "a", "equals": 2},
        NOTE,
    ])  # fmt: skip
    run = en.start_run(wf, "k")
    assert en.execute(run.pk) == "succeeded"
    assert Notification.objects.filter(user=me, kind="automation").count() == 0
    assert run.steps.get(step_index=1).state == "skipped"
    queued = en.start_run(wf, "k2")
    en.set_enabled(me, wf, False)
    queued.refresh_from_db()
    assert queued.state == "cancelled" and en.start_run(wf, "k3") is None


def test_edit_makes_a_new_version_and_started_runs_keep_theirs_and_only_owner_edits():
    me, other = _u("AU-8"), _u("AU-9")
    wf = _wf(me, [{"type": "notify_owner", "title": "old"}])
    run = en.start_run(wf, "k")
    en.update_definition(me, wf, {"trigger": {"type": "manual"}, "steps": [NOTE]})
    en.execute(run.pk)
    assert Notification.objects.get(user=me, kind="automation").title == "old"
    with pytest.raises(PermissionError):
        en.update_definition(other, wf, {"trigger": {"type": "manual"}, "steps": [NOTE]})
    with pytest.raises(PermissionError):
        en.set_enabled(other, wf, True)


def test_expired_worker_cannot_write_state_or_apply_effects_after_takeover():
    me = _u("AU-10")
    wf = _wf(me, [NOTE, NOTE])
    run = en.start_run(wf, "k")
    t0 = timezone.now()
    _, a_attempt = en._claim(run.pk, t0)  # worker A claims, then stalls
    later = t0 + en.LEASE + dt.timedelta(seconds=1)
    assert en.execute(run.pk, now=later) == "succeeded"  # worker B takes over and finishes
    assert Notification.objects.filter(user=me, kind="automation").count() == 2
    with pytest.raises(en.Fenced):
        with en.transaction.atomic():
            en._own(run.pk, a_attempt, timezone.now())
    assert en._finish(run.pk, a_attempt, "failed", "late") is False  # cannot overwrite
    run.refresh_from_db()
    assert run.state == "succeeded" and run.error == "" and run.attempt == 2


def test_disable_during_a_run_stops_the_remaining_steps():
    from unittest import mock

    me = _u("AU-11")
    wf = _wf(me, [NOTE, NOTE, NOTE])
    run = en.start_run(wf, "k")
    real = en.notify

    def notify_then_disable(*a, **k):
        real(*a, **k)
        if Notification.objects.filter(user=me, kind="automation").count() == 1:
            en.set_enabled(me, wf, False)  # the owner disables while step 0 is in flight

    with mock.patch.object(en, "notify", side_effect=notify_then_disable):
        assert en.execute(run.pk) is None
    run.refresh_from_db()
    assert run.state == "cancelled"
    assert Notification.objects.filter(user=me, kind="automation").count() == 1  # not 3
    assert list(run.steps.values_list("step_index", flat=True)) == [0]


def test_trigger_keys_are_never_truncated_into_one_another():
    me = _u("AU-12")
    wf = _wf(me, [NOTE])
    base = "e" * 64
    assert en.start_run(wf, base).pk != en.start_run(wf, base[:-1] + "f").pk
    with pytest.raises(ValidationError):
        en.start_run(wf, base + "x")  # over the limit is refused, not cut
    with pytest.raises(ValidationError):
        en.start_run(wf, "")


def test_scalars_are_bounded_and_nonfinite_numbers_rejected():
    me = _u("AU-13")

    def mk(v):
        return en.create_definition(
            me,
            "w",
            {
                "trigger": {"type": "manual"},
                "steps": [{"type": "set_value", "key": "a", "value": v}],
            },
        )

    for bad in (float("nan"), float("inf"), 2**60, "x" * 201, None, [1]):
        with pytest.raises(ValidationError):
            mk(bad)
    for ok in (True, 0, 1.5, "x" * 200):
        mk(ok)


def test_lifecycle_is_audited_with_ids_and_counts():
    from apps.core.models import AuditLog

    me = _u("AU-14")
    wf = _wf(me, [NOTE])
    run = en.start_run(wf, "k")
    en.execute(run.pk)
    actions = set(AuditLog.objects.filter(target_id=str(wf.pk)).values_list("action", flat=True))
    assert {"automation.create", "automation.enable"} <= actions
    fin = AuditLog.objects.get(action="automation.finish", target_id=str(run.pk))
    assert fin.payload_summary["state"] == "succeeded" and fin.payload_summary["steps"] == 1


def test_runner_executes_a_queued_run_once_and_a_second_pass_does_nothing():
    me = _u("AU-R1")
    wf = _wf(me, [NOTE])
    run = en.start_run(wf, "q-1")
    assert run.state == WfRun.State.QUEUED
    assert en.run_pending()["succeeded"] == 1
    run.refresh_from_db()
    assert run.state == WfRun.State.SUCCEEDED and run.steps.count() == 1
    assert Notification.objects.filter(user=me).count() == 1
    assert en.run_pending() == {"succeeded": 0, "failed": 0, "cancelled": 0, "skipped": 0, "errors": 0, "owner_inactive": 0}
    assert Notification.objects.filter(user=me).count() == 1


def test_runner_resumes_a_dead_workers_run_without_repeating_the_finished_step():
    me = _u("AU-R2")
    wf = _wf(me, [NOTE, {"type": "set_value", "key": "k", "value": 1}, NOTE])
    run = en.start_run(wf, "q-2")
    WfStepResult.objects.create(run=run, step_index=0, state="ok", output={}, idempotency_key=en._key(run, 0))
    notify_before = Notification.objects.filter(user=me).count()
    WfRun.objects.filter(pk=run.pk).update(
        state=WfRun.State.RUNNING, next_step=1, attempt=1,
        lease_until=timezone.now() - dt.timedelta(minutes=1),
    )
    assert en.run_pending()["succeeded"] == 1
    run.refresh_from_db()
    assert run.state == WfRun.State.SUCCEEDED and run.attempt == 2
    assert sorted(run.steps.values_list("step_index", flat=True)) == [0, 1, 2]
    assert Notification.objects.filter(user=me).count() == notify_before + 1  # only step 3 ran


def test_runner_leaves_a_run_with_a_live_lease_and_cancels_runs_of_a_disabled_workflow():
    me = _u("AU-R3")
    wf = _wf(me, [NOTE])
    held = en.start_run(wf, "q-3")
    WfRun.objects.filter(pk=held.pk).update(
        state=WfRun.State.RUNNING, attempt=1, lease_until=timezone.now() + dt.timedelta(minutes=3)
    )
    assert en.run_pending() == {"succeeded": 0, "failed": 0, "cancelled": 0, "skipped": 0, "errors": 0, "owner_inactive": 0}
    held.refresh_from_db()
    assert held.state == WfRun.State.RUNNING and held.attempt == 1
    wf2 = _wf(me, [NOTE])
    waiting = en.start_run(wf2, "q-4")
    en.set_enabled(me, wf2, False)  # disabling already cancels it, so the runner has nothing to take
    assert en.run_pending()["skipped"] == 0 and en.run_pending()["cancelled"] == 0
    waiting.refresh_from_db()
    assert waiting.state == WfRun.State.CANCELLED
    # a queued run that slipped past the disable (switched off between listing and claiming)
    WfRun.objects.filter(pk=waiting.pk).update(state=WfRun.State.QUEUED, finished_at=None)
    out = en.run_pending()
    waiting.refresh_from_db()
    assert out["cancelled"] == 1 and waiting.state == WfRun.State.CANCELLED
    assert not WfStepResult.objects.filter(run=waiting).exists()


def test_runner_respects_the_batch_size():
    me = _u("AU-R4")
    wf = _wf(me, [NOTE])
    for i in range(3):
        en.start_run(wf, f"b-{i}")
    assert en.run_pending(2)["succeeded"] == 2
    assert WfRun.objects.filter(state=WfRun.State.QUEUED).count() == 1


def test_runner_command_runs_one_pass_and_reports_counts():
    from io import StringIO

    from django.core.management import call_command

    me = _u("AU-R5")
    wf = _wf(me, [NOTE])
    en.start_run(wf, "c-1")
    out = StringIO()
    call_command("process_automation_runs", stdout=out)
    assert "1 succeeded, 0 failed, 0 cancelled, 0 not taken, 0 raised, 0 left" in out.getvalue()


def test_runner_isolates_a_run_that_raises_and_still_runs_the_healthy_one(monkeypatch):
    me = _u("AU-R6")
    poison = en.start_run(_wf(me, [NOTE]), "p-1")
    healthy = en.start_run(_wf(me, [NOTE]), "p-2")
    real = en.execute

    def flaky(run_id, **kw):
        if run_id == poison.pk:
            raise RuntimeError("database went away")
        return real(run_id, **kw)

    monkeypatch.setattr(en, "execute", flaky)
    out = en.run_pending()
    poison.refresh_from_db(); healthy.refresh_from_db()
    assert out["errors"] == 1 and out["succeeded"] == 1
    assert healthy.state == WfRun.State.SUCCEEDED
    assert poison.state == WfRun.State.QUEUED and poison.attempt == 0  # untouched, not marked failed


def test_runner_takes_no_run_of_an_owner_who_is_not_active_and_changes_nothing():
    me = _u("AU-R7")
    wf = _wf(me, [NOTE])
    run = en.start_run(wf, "o-1")
    CustomUser.objects.filter(pk=me.pk).update(status="suspended")
    out = en.run_pending()
    run.refresh_from_db()
    assert out["owner_inactive"] == 1 and out["succeeded"] == 0
    assert run.state == WfRun.State.QUEUED and run.attempt == 0 and not run.steps.exists()
    assert not Notification.objects.filter(user=me).exists()
    CustomUser.objects.filter(pk=me.pk).update(status="active")
    assert en.run_pending()["succeeded"] == 1
