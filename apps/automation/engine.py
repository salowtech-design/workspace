"""Workflow definitions and a lease-based runner.

Scope of this slice (nothing beyond it is claimed):
- Steps are a closed list of in-app step types. There is no arbitrary code, no outbound
  call, and no step that reads or writes another person's data.
- A run belongs to the definition owner, and "notify_owner" reaches only that owner.
- Delivery is at-least-once: a step is recorded and its effect applied in one transaction,
  and the step's idempotency key is unique, so a repeated step does not repeat its effect.
"""

import datetime
import hashlib
import math

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.foundation.audit import audit
from apps.core.foundation.notify import notify

from .models import WfDefinition, WfRun, WfStepResult

MAX_STEPS = 20
MAX_TEXT = 200
MAX_KEY = 64
LEASE = datetime.timedelta(minutes=5)
TRIGGERS = {"manual", "event"}


def _scalar(i, v):
    if isinstance(v, bool):
        return
    if isinstance(v, int):
        if abs(v) > 2**53:
            raise ValidationError(f"Step {i + 1} number is out of range.")
        return
    if isinstance(v, float):
        if not math.isfinite(v) or abs(v) > 1e15:
            raise ValidationError(f"Step {i + 1} number is not usable.")
        return
    if isinstance(v, str) and len(v) <= MAX_TEXT:
        return
    raise ValidationError(f"Step {i + 1} needs a plain value up to {MAX_TEXT} characters.")


def _check_step(i, step):
    if not isinstance(step, dict) or set(step) - {"type", "title", "key", "value", "equals"}:
        raise ValidationError(f"Step {i + 1} has an unsupported shape.")
    kind = step.get("type")
    if kind == "notify_owner":
        title = step.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValidationError(f"Step {i + 1} needs a title up to 200 characters.")
    elif kind == "set_value":
        if not isinstance(step.get("key"), str) or not step["key"].strip():
            raise ValidationError(f"Step {i + 1} needs a key.")
        _scalar(i, step.get("value"))
        if len(step["key"]) > MAX_KEY:
            raise ValidationError(f"Step {i + 1} key is too long.")
    elif kind == "stop_unless_equals":
        if not isinstance(step.get("key"), str) or "equals" not in step:
            raise ValidationError(f"Step {i + 1} needs a key and a value to compare.")
        if len(step["key"]) > MAX_KEY:
            raise ValidationError(f"Step {i + 1} key is too long.")
        _scalar(i, step["equals"])
    else:
        raise ValidationError(f"Step {i + 1} has an unknown type.")


def validate_definition(d):
    if not isinstance(d, dict) or set(d) - {"trigger", "steps"}:
        raise ValidationError("A definition has a trigger and steps only.")
    trig = d.get("trigger")
    if isinstance(trig, dict) and set(trig) - {"type", "event_type"}:
        raise ValidationError("Trigger has a type and an event type only.")
    if not isinstance(trig, dict) or trig.get("type") not in TRIGGERS:
        raise ValidationError("Trigger must be manual or event.")
    if trig["type"] == "event" and not (
        isinstance(trig.get("event_type"), str) and trig["event_type"].strip()
    ):
        raise ValidationError("An event trigger needs an event type.")
    if trig["type"] == "event" and len(trig["event_type"]) > 128:
        raise ValidationError("Event type is too long.")
    steps = d.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValidationError(f"A workflow has 1 to {MAX_STEPS} steps.")
    for i, s in enumerate(steps):
        _check_step(i, s)


def create_definition(owner, name, definition):
    validate_definition(definition)
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("Name is required, up to 120 characters.")
    wf = WfDefinition.objects.create(owner=owner, name=name, definition=definition)
    audit(owner, "automation.create", "wf_definition", wf.pk, version=1)
    return wf


def update_definition(owner, wf, definition, *, expected_version=None):
    """Owner edits create a new version; runs already started keep the version they began on.

    Pass expected_version to refuse the edit if the workflow changed since it was read."""
    if wf.owner_id != owner.id:
        raise PermissionError("only the owner edits a workflow")
    validate_definition(definition)
    with transaction.atomic():
        wf = WfDefinition.objects.select_for_update().get(pk=wf.pk)
        if expected_version is not None and wf.version != expected_version:
            raise ValidationError(
                "This workflow changed since you opened it. Reload and try again."
            )
        wf.definition, wf.version = definition, wf.version + 1
        wf.save(update_fields=["definition", "version"])
        audit(owner, "automation.edit", "wf_definition", wf.pk, version=wf.version)
    return wf


def set_enabled(owner, wf, enabled):
    if wf.owner_id != owner.id:
        raise PermissionError("only the owner enables a workflow")
    with transaction.atomic():
        wf = WfDefinition.objects.select_for_update().get(pk=wf.pk)
        wf.enabled = bool(enabled)
        wf.save(update_fields=["enabled"])
        cancelled = 0
        if not wf.enabled:  # queued and running runs both stop; a running one at its next step
            cancelled = wf.runs.filter(state__in=[WfRun.State.QUEUED, WfRun.State.RUNNING]).update(
                state=WfRun.State.CANCELLED, finished_at=timezone.now(), lease_until=None
            )
        audit(owner, "automation.enable" if wf.enabled else "automation.disable",
              "wf_definition", wf.pk, cancelled_runs=cancelled)  # fmt: skip
    return wf


def start_run(wf, trigger_key):
    """One run per (definition, trigger). A repeat delivery returns the existing run."""
    trigger_key = str(trigger_key)
    if not trigger_key or len(trigger_key) > 64:
        raise ValidationError("A trigger key is 1 to 64 characters.")
    with transaction.atomic():
        wf = WfDefinition.objects.select_for_update().get(pk=wf.pk)
        if not wf.enabled:
            return None
        existing = WfRun.objects.filter(definition=wf, trigger_key=trigger_key).first()
        if existing:
            return existing
        try:
            with transaction.atomic():
                return WfRun.objects.create(
                    definition=wf,
                    version=wf.version,
                    definition_snapshot=wf.definition,
                    trigger_key=trigger_key,
                )
        except IntegrityError:
            return WfRun.objects.filter(definition=wf, trigger_key=trigger_key).first()


def _claim(run_id, now):
    """Returns (run, attempt) or None. The attempt number fences out any earlier worker."""
    with transaction.atomic():
        wf_id = WfRun.objects.values_list("definition_id", flat=True).get(pk=run_id)
        wf = WfDefinition.objects.select_for_update().get(pk=wf_id)  # lock order: definition, run
        run = WfRun.objects.select_for_update().get(pk=run_id)
        if run.state in (WfRun.State.SUCCEEDED, WfRun.State.FAILED, WfRun.State.CANCELLED):
            return None
        if run.state == WfRun.State.RUNNING and run.lease_until and run.lease_until > now:
            return None
        if not wf.enabled:
            run.state, run.finished_at = WfRun.State.CANCELLED, now
            run.save(update_fields=["state", "finished_at"])
            return None
        run.state, run.lease_until, run.attempt = (
            WfRun.State.RUNNING, now + LEASE, run.attempt + 1,
        )  # fmt: skip
        run.save(update_fields=["state", "lease_until", "attempt"])
        audit(None, "automation.claim", "wf_run", run.pk, attempt=run.attempt)
        return run, run.attempt


def _key(run, index):
    return hashlib.sha1(f"{run.pk}:{index}".encode(), usedforsecurity=False).hexdigest()


class Fenced(Exception):
    """This worker no longer owns the run (a newer attempt, a disable, or an expired lease)."""


def _own(run_id, attempt, now):
    """Lock definition then run, and confirm this attempt still owns a live, enabled run."""
    wf_id = WfRun.objects.values_list("definition_id", flat=True).get(pk=run_id)
    wf = WfDefinition.objects.select_for_update().get(pk=wf_id)
    run = WfRun.objects.select_for_update().get(pk=run_id)
    if (
        run.state != WfRun.State.RUNNING
        or run.attempt != attempt
        or not wf.enabled
        or not run.lease_until
        or run.lease_until <= now
    ):
        raise Fenced()
    return run


def _apply(run, step, ctx):
    kind = step["type"]
    if kind == "notify_owner":
        notify(run.definition.owner, "automation", step["title"])
        return "ok", {}, False
    if kind == "set_value":
        return "ok", {step["key"]: step["value"]}, False
    if ctx.get(step["key"]) == step["equals"]:
        return "ok", {}, False
    return "skipped", {}, True


def _finish(run_id, attempt, state, error=""):
    """Final state only if this attempt still owns the run."""
    with transaction.atomic():
        try:
            run = _own(run_id, attempt, timezone.now())
        except Fenced:
            return False
        run.state, run.error, run.finished_at, run.lease_until = (
            state, error[:500], timezone.now(), None,
        )  # fmt: skip
        run.save(update_fields=["state", "error", "finished_at", "lease_until"])
        audit(None, "automation.finish", "wf_run", run.pk, state=state, attempt=attempt,
              steps=run.steps.count())  # fmt: skip
        return True


def execute(run_id, *, now=None):
    """Claim a run and carry out its remaining steps. Returns the final state or None.

    Every step, and the final state, re-checks ownership under the definition and run locks, so a
    worker whose lease expired or whose run was disabled cannot apply an effect or write state.
    """
    now = now or timezone.now()
    claimed = _claim(run_id, now)
    if claimed is None:
        return None
    run, attempt = claimed
    steps = run.definition_snapshot["steps"]
    ctx = {}
    for r in run.steps.order_by("step_index"):
        ctx.update(r.output or {})
    if run.steps.filter(state="skipped").exists():  # a stop was recorded before a crash
        return "succeeded" if _finish(run_id, attempt, "succeeded") else None
    try:
        for i in range(run.next_step, len(steps)):
            stop = False
            with transaction.atomic():
                cur = _own(run_id, attempt, now_fn())
                if cur.next_step > i:  # another attempt already did this step
                    ctx.update(
                        {k: v for r in cur.steps.filter(step_index=i) for k, v in r.output.items()}
                    )
                    continue
                state, out, stop = _apply(cur, steps[i], ctx)
                WfStepResult.objects.create(
                    run=cur, step_index=i, state=state, output=out, idempotency_key=_key(cur, i)
                )
                ctx.update(out)
                WfRun.objects.filter(pk=cur.pk, attempt=attempt).update(
                    next_step=i + 1, lease_until=now_fn() + LEASE
                )
            if stop:
                break
    except Fenced:
        return None
    except Exception as exc:  # noqa: BLE001
        return "failed" if _finish(run_id, attempt, "failed", str(exc)) else None
    return "succeeded" if _finish(run_id, attempt, "succeeded") else None


def now_fn():
    return timezone.now()


def run_pending(limit=50, *, now=None):
    """The background runner's one pass: carry out runs that are waiting, and runs whose worker died.

    A run is picked up when it is queued, or running with a lease that has run out. A run another
    worker is still holding is left alone. execute() claims each one with the fencing attempt, so two
    runners (or a runner and a person's own press) never apply the same step twice. Returns a dict of
    how many runs ended in each state, plus "skipped" for runs that were not ours to take.
    """
    now = now or timezone.now()
    ids = list(
        WfRun.objects.filter(
            Q(state=WfRun.State.QUEUED)
            | Q(state=WfRun.State.RUNNING, lease_until__isnull=True)
            | Q(state=WfRun.State.RUNNING, lease_until__lte=now)
        )
        .order_by("id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    done = {"succeeded": 0, "failed": 0, "cancelled": 0, "skipped": 0}
    for run_id in ids:
        state = execute(run_id, now=now)
        if state in ("succeeded", "failed"):
            done[state] += 1
        elif WfRun.objects.filter(pk=run_id, state=WfRun.State.CANCELLED).exists():
            done["cancelled"] += 1
        else:
            done["skipped"] += 1
    return done
