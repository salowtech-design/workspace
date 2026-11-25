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

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.foundation.notify import notify

from .models import WfDefinition, WfRun, WfStepResult

MAX_STEPS = 20
LEASE = datetime.timedelta(minutes=5)
TRIGGERS = {"manual", "event"}


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
        if not isinstance(step.get("value"), (str, int, float, bool)):
            raise ValidationError(f"Step {i + 1} needs a plain value.")
    elif kind == "stop_unless_equals":
        if not isinstance(step.get("key"), str) or "equals" not in step:
            raise ValidationError(f"Step {i + 1} needs a key and a value to compare.")
    else:
        raise ValidationError(f"Step {i + 1} has an unknown type.")


def validate_definition(d):
    if not isinstance(d, dict) or set(d) - {"trigger", "steps"}:
        raise ValidationError("A definition has a trigger and steps only.")
    trig = d.get("trigger")
    if not isinstance(trig, dict) or trig.get("type") not in TRIGGERS:
        raise ValidationError("Trigger must be manual or event.")
    if trig["type"] == "event" and not (
        isinstance(trig.get("event_type"), str) and trig["event_type"].strip()
    ):
        raise ValidationError("An event trigger needs an event type.")
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
    return WfDefinition.objects.create(owner=owner, name=name, definition=definition)


def update_definition(owner, wf, definition):
    """Owner edits create a new version; runs already started keep the version they began on."""
    if wf.owner_id != owner.id:
        raise PermissionError("only the owner edits a workflow")
    validate_definition(definition)
    with transaction.atomic():
        wf = WfDefinition.objects.select_for_update().get(pk=wf.pk)
        wf.definition, wf.version = definition, wf.version + 1
        wf.save(update_fields=["definition", "version"])
    return wf


def set_enabled(owner, wf, enabled):
    if wf.owner_id != owner.id:
        raise PermissionError("only the owner enables a workflow")
    with transaction.atomic():
        wf = WfDefinition.objects.select_for_update().get(pk=wf.pk)
        wf.enabled = bool(enabled)
        wf.save(update_fields=["enabled"])
        if not wf.enabled:
            wf.runs.filter(state=WfRun.State.QUEUED).update(
                state=WfRun.State.CANCELLED, finished_at=timezone.now()
            )
    return wf


def start_run(wf, trigger_key):
    """One run per (definition, trigger). A repeat delivery returns the existing run."""
    trigger_key = str(trigger_key)[:64]
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
    with transaction.atomic():
        run = WfRun.objects.select_for_update().get(pk=run_id)
        if run.state in (WfRun.State.SUCCEEDED, WfRun.State.FAILED, WfRun.State.CANCELLED):
            return None
        if run.state == WfRun.State.RUNNING and run.lease_until and run.lease_until > now:
            return None
        if not run.definition.enabled:
            run.state, run.finished_at = WfRun.State.CANCELLED, now
            run.save(update_fields=["state", "finished_at"])
            return None
        run.state, run.lease_until = WfRun.State.RUNNING, now + LEASE
        run.save(update_fields=["state", "lease_until"])
        return run


def _key(run, index):
    return hashlib.sha1(f"{run.pk}:{index}".encode(), usedforsecurity=False).hexdigest()


def _apply(run, index, step, ctx):
    """Returns (state, output, stop). Effects happen inside the caller's transaction."""
    kind = step["type"]
    if kind == "notify_owner":
        notify(run.definition.owner, "automation", step["title"])
        return "ok", {}, False
    if kind == "set_value":
        return "ok", {step["key"]: step["value"]}, False
    if ctx.get(step["key"]) == step["equals"]:
        return "ok", {}, False
    return "skipped", {}, True


def execute(run_id, *, now=None):
    """Claim a run and carry out its remaining steps. Returns the final state or None."""
    now = now or timezone.now()
    run = _claim(run_id, now)
    if run is None:
        return None
    steps = run.definition_snapshot["steps"]
    ctx = {}
    for r in run.steps.order_by("step_index"):
        ctx.update(r.output or {})
    if run.steps.filter(state="skipped").exists():  # a stop was recorded before a crash
        WfRun.objects.filter(pk=run.pk).update(
            state=WfRun.State.SUCCEEDED, finished_at=timezone.now()
        )
        return WfRun.State.SUCCEEDED
    try:
        for i in range(run.next_step, len(steps)):
            with transaction.atomic():
                key = _key(run, i)
                if WfStepResult.objects.filter(idempotency_key=key).exists():
                    stop = False
                else:
                    state, out, stop = _apply(run, i, steps[i], ctx)
                    WfStepResult.objects.create(
                        run=run, step_index=i, state=state, output=out, idempotency_key=key
                    )
                    ctx.update(out)
                WfRun.objects.filter(pk=run.pk).update(next_step=i + 1)
            if stop:
                break
    except Exception as exc:  # noqa: BLE001
        WfRun.objects.filter(pk=run.pk).update(
            state=WfRun.State.FAILED, error=str(exc)[:500], finished_at=timezone.now()
        )
        return WfRun.State.FAILED
    WfRun.objects.filter(pk=run.pk).update(state=WfRun.State.SUCCEEDED, finished_at=timezone.now())
    return WfRun.State.SUCCEEDED
