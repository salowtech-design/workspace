"""Pages for a person's own workflows. Everything here is scoped to the signed-in owner.

Not built and not implied by these pages: event triggers firing (the engine accepts them, but
nothing delivers events to it), a background runner, sharing a workflow, deleting one, or any
step that touches another person or leaves the app."""

import re
import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.automation import engine
from apps.automation.models import WfDefinition, WfRun
from apps.core.foundation.throttle import throttled

MAX_WORKFLOWS = 20  # per person
FORM_ROWS = 10  # step rows shown on the form (the engine allows up to 20)
RUNS_SHOWN = 20
RUN_LIMIT = 10  # manual runs per person per minute
KINDS = (
    ("notify_owner", "Notify me"),
    ("set_value", "Set a value"),
    ("stop_unless_equals", "Stop unless a value equals"),
)


def _active(request):
    if getattr(request.user, "status", "active") != "active":
        raise Http404


def _mine(request, public_id):
    return get_object_or_404(WfDefinition, public_id=public_id, owner=request.user)


def _steps_from_post(post):
    steps = []
    for i in range(FORM_ROWS):
        kind = (post.get(f"type{i}") or "").strip()
        if not kind:
            continue
        title, key = (post.get(f"title{i}") or "").strip(), (post.get(f"key{i}") or "").strip()
        value, equals = post.get(f"value{i}") or "", post.get(f"equals{i}") or ""
        if kind == "notify_owner":
            steps.append({"type": kind, "title": title})
        elif kind == "set_value":
            steps.append({"type": kind, "key": key, "value": value})
        elif kind == "stop_unless_equals":
            steps.append({"type": kind, "key": key, "equals": equals})
        else:
            steps.append({"type": kind})  # the engine refuses an unknown type with a message
    return steps


def _rows(steps):
    rows = []
    for i in range(FORM_ROWS):
        s = steps[i] if i < len(steps) else {}
        rows.append(
            {
                "i": i,
                "type": s.get("type", ""),
                "title": s.get("title", ""),
                "key": s.get("key", ""),
                "value": s.get("value", "") if isinstance(s.get("value", ""), str) else s["value"],
                "equals": s.get("equals", ""),
            }
        )
    return rows


FORM_KEYS = {
    "notify_owner": {"type", "title"},
    "set_value": {"type", "key", "value"},
    "stop_unless_equals": {"type", "key", "equals"},
}


def _round_trips(definition):
    """True only if this page can show every step and save it back without changing or losing any."""
    steps = definition.get("steps", [])
    if definition.get("trigger") != {"type": "manual"} or len(steps) > FORM_ROWS:
        return False
    for st in steps:
        if not isinstance(st, dict) or st.get("type") not in FORM_KEYS:
            return False
        if set(st) != FORM_KEYS[st["type"]] or any(not isinstance(v, str) for v in st.values()):
            return False
    return True


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


def _describe(step):
    k = step.get("type")
    if k == "notify_owner":
        return f"Notify me: {step.get('title', '')}"
    if k == "set_value":
        return f"Set {step.get('key', '')} to {step.get('value', '')!r}"
    if k == "stop_unless_equals":
        return f"Stop unless {step.get('key', '')} equals {step.get('equals', '')!r}"
    return "Unknown step"


@login_required
@never_cache
def home(request):
    _active(request)
    wfs = WfDefinition.objects.filter(owner=request.user).order_by("-id")[:MAX_WORKFLOWS]
    return render(request, "automation/home.html", {"wfs": wfs, "max": MAX_WORKFLOWS})


@login_required
@never_cache
def new(request):
    _active(request)
    ctx = {"rows": _rows([]), "kinds": KINDS, "name": "", "mode": "new"}
    if request.method == "POST":
        steps = _steps_from_post(request.POST)
        ctx.update(name=request.POST.get("name", ""), rows=_rows(steps))
        try:
            with transaction.atomic():
                # lock the person's own row so two creates cannot both pass the cap
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                if WfDefinition.objects.filter(owner=request.user).count() >= MAX_WORKFLOWS:
                    raise ValidationError(f"You can keep up to {MAX_WORKFLOWS} workflows.")
                wf = engine.create_definition(
                    request.user,
                    request.POST.get("name"),
                    {"trigger": {"type": "manual"}, "steps": steps},
                )
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return render(request, "automation/form.html", ctx, status=400)
        messages.success(request, "Saved. It is switched off until you turn it on.")
        return redirect("automation:detail", public_id=wf.public_id)
    return render(request, "automation/form.html", ctx)


@login_required
@never_cache
def edit(request, public_id):
    _active(request)
    wf = _mine(request, public_id)
    steps = wf.definition.get("steps", [])
    ctx = {
        "rows": _rows(steps),
        "kinds": KINDS,
        "name": wf.name,
        "mode": "edit",
        "wf": wf,
        "version": wf.version,
    }
    if not _round_trips(wf.definition):
        messages.error(
            request,
            "This workflow has steps or settings this page cannot show and save back without changes, so it cannot be edited here.",
        )
        return redirect("automation:detail", public_id=wf.public_id)
    if request.method == "POST":
        steps = _steps_from_post(request.POST)
        ctx["rows"] = _rows(steps)
        try:
            engine.update_definition(
                request.user,
                wf,
                {"trigger": wf.definition["trigger"], "steps": steps},
                expected_version=_int(request.POST.get("version")),
            )
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return render(request, "automation/form.html", ctx, status=400)
        messages.success(request, "Saved as a new version. Runs already started keep the old one.")
        return redirect("automation:detail", public_id=wf.public_id)
    return render(request, "automation/form.html", ctx)


@login_required
@never_cache
def detail(request, public_id):
    _active(request)
    wf = _mine(request, public_id)
    runs = list(wf.runs.order_by("-id")[:RUNS_SHOWN])
    return render(
        request,
        "automation/detail.html",
        {
            "wf": wf,
            "trigger": wf.definition.get("trigger", {}),
            "steps": [_describe(s) for s in wf.definition.get("steps", [])],
            "runs": runs,
            "run_token": uuid.uuid4().hex,
        },
    )


@login_required
@require_POST
def toggle(request, public_id):
    _active(request)
    wf = _mine(request, public_id)
    want = request.POST.get("enabled") == "1"
    engine.set_enabled(request.user, wf, want)
    messages.success(
        request,
        "Switched on." if want else "Switched off. Any queued or running runs were cancelled.",
    )
    return redirect("automation:detail", public_id=wf.public_id)


@login_required
@require_POST
def run_now(request, public_id):
    """A person-started run of their own workflow. It runs here, in this request."""
    _active(request)
    wf = _mine(request, public_id)
    if throttled(request.user, "automation_run", RUN_LIMIT):
        messages.error(request, "Too many runs in a minute. Wait and try again.")
        return redirect("automation:detail", public_id=wf.public_id)
    if wf.definition.get("trigger", {}).get("type") != "manual":
        messages.error(
            request,
            "This workflow starts from an event, which is not connected yet, so it cannot be run here.",
        )
        return redirect("automation:detail", public_id=wf.public_id)
    token = request.POST.get("token", "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        messages.error(request, "That page was out of date. Try again.")
        return redirect("automation:detail", public_id=wf.public_id)
    # the page's own token is the trigger key, so a double click or a resent form is one run
    run = engine.start_run(wf, token)
    if run is None:
        messages.error(request, "Switch the workflow on before running it.")
        return redirect("automation:detail", public_id=wf.public_id)
    engine.execute(run.pk)
    run = WfRun.objects.get(pk=run.pk)
    if run.state == WfRun.State.SUCCEEDED:
        messages.success(request, "Run finished: succeeded.")
    elif run.state == WfRun.State.FAILED:
        messages.error(request, "Run failed. The error is in the run list below.")
    else:
        messages.error(request, f"The run did not finish here. It is {run.state}.")
    return redirect("automation:detail", public_id=wf.public_id)
