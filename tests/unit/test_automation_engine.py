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
