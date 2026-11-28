from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.core.foundation.throttle import throttled
from apps.forms import services as fs
from apps.forms.models import KINDS, Form

WRITE_LIMIT = 30


def _limited(request):
    if throttled(request.user, "forms-write", WRITE_LIMIT):
        messages.error(request, "Too many changes in a minute. Wait and try again.")
        return True
    return False


def _errors(request, exc):
    for m in exc.messages:
        messages.error(request, m)


def _posted(request, questions):
    out = {}
    for q in questions:
        key = f"q{q.pk}"
        out[str(q.pk)] = request.POST.getlist(key) if q.kind == "multi_choice" else request.POST.get(key, "")
    return out


@login_required
@require_http_methods(["GET", "POST"])
def home(request):
    if request.method == "POST":
        if _limited(request):
            return redirect("forms:home")
        try:
            f = fs.create_form(request.user, request.POST.get("title", ""), request.POST.get("description", ""))
            return redirect("forms:editor", pid=f.public_id)
        except ValidationError as exc:
            _errors(request, exc)
    return render(request, "forms/home.html", {"mine": Form.objects.filter(owner=request.user).order_by("-updated_at")[:100]})


@login_required
@require_http_methods(["GET", "POST"])
def editor(request, pid):
    if request.method == "POST":
        act = request.POST.get("do", "")
        try:
            if _limited(request):
                return redirect("forms:editor", pid=pid)
            if act == "details":
                fs.update_details(request.user, pid, request.POST.get("title", ""), request.POST.get("description", ""))
            elif act == "add":
                fs.add_question(request.user, pid, request.POST.get("kind", ""), request.POST.get("text", ""),
                                request.POST.get("required") == "on", request.POST.get("choices", "").split("\n"))  # fmt: skip
            elif act in ("remove", "up", "down"):
                fs.change_question(request.user, pid, request.POST.get("question", ""), act)
            elif act == "audience":
                fs.set_audience(request.user, pid, request.POST.get("people", "").replace(",", " ").split())
            elif act == "publish":
                fs.publish(request.user, pid)
            elif act == "close":
                fs.close(request.user, pid)
        except ValidationError as exc:
            _errors(request, exc)
        return redirect("forms:editor", pid=pid)
    form = fs.get_form_for(request.user, pid)
    if form.owner_id != request.user.pk:
        return redirect("forms:fill", pid=pid)
    return render(request, "forms/editor.html", {
        "f": form, "questions": form.questions.order_by("position"), "kinds": KINDS,
        "people": ", ".join(a.person.employee_id for a in form.audience.select_related("person")),
        "collection_on": fs._collection_on(),
    })  # fmt: skip


@login_required
@require_http_methods(["GET", "POST"])
def preview(request, pid):
    form = fs._owned(pid, request.user, lock=False)
    questions = list(form.questions.order_by("position"))
    note = ""
    if request.method == "POST":
        try:
            fs.preview_check(request.user, pid, _posted(request, questions))
            note = "These answers would be accepted. Nothing was saved."
        except ValidationError as exc:
            _errors(request, exc)
    return render(request, "forms/fill.html", {"f": form, "questions": questions, "preview": True, "note": note})


@login_required
@require_http_methods(["GET", "POST"])
def fill(request, pid):
    form = fs.get_form_for(request.user, pid)
    questions = list(form.questions.order_by("position"))
    mine = fs.my_response(request.user, form)
    if request.method == "POST" and mine is None:
        if _limited(request):
            return redirect("forms:fill", pid=pid)
        try:
            fs.submit_response(request.user, pid, _posted(request, questions))
            messages.success(request, "Your answers were saved.")
            return redirect("forms:fill", pid=pid)
        except ValidationError as exc:
            _errors(request, exc)
    return render(request, "forms/fill.html", {"f": form, "questions": questions, "mine": mine, "preview": False})


@login_required
def results_view(request, pid):
    form, questions, rows, counts = fs.results(request.user, pid)
    return render(request, "forms/results.html", {"f": form, "questions": questions, "rows": rows, "counts": counts})


@login_required
def results_csv(request, pid):
    text = fs.csv_text(request.user, pid)
    resp = HttpResponse(text, content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="form-results.csv"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp
