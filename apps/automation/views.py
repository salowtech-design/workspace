"""Pages for a person's own workflows. Everything here is scoped to the signed-in owner.

Not built and not implied by these pages: event triggers firing (the engine accepts them, but
nothing delivers events to it), a background runner, sharing a workflow, deleting one, or any
step that touches another person or leaves the app."""

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
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
        if WfDefinition.objects.filter(owner=request.user).count() >= MAX_WORKFLOWS:
            messages.error(request, f"You can keep up to {MAX_WORKFLOWS} workflows.")
            return render(request, "automation/form.html", ctx, status=400)
        steps = _steps_from_post(request.POST)
        ctx.update(name=request.POST.get("name", ""), rows=_rows(steps))
        try:
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
    ctx = {"rows": _rows(steps), "kinds": KINDS, "name": wf.name, "mode": "edit", "wf": wf}
    if wf.definition.get("trigger", {}).get("type") != "manual" or len(steps) > FORM_ROWS:
        messages.error(request, "This workflow has a shape this page cannot edit safely.")
        return redirect("automation:detail", public_id=wf.public_id)
    if request.method == "POST":
        steps = _steps_from_post(request.POST)
        ctx["rows"] = _rows(steps)
        try:
            engine.update_definition(
                request.user, wf, {"trigger": wf.definition["trigger"], "steps": steps}
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
    run = engine.start_run(wf, uuid.uuid4().hex)
    if run is None:
        messages.error(request, "Switch the workflow on before running it.")
        return redirect("automation:detail", public_id=wf.public_id)
    state = engine.execute(run.pk)
    messages.success(request, f"Run finished: {state or WfRun.objects.get(pk=run.pk).state}.")
    return redirect("automation:detail", public_id=wf.public_id)
