"""Salow Forms services. Locking: the form row first, then re-read. Audience and response-visibility rights are load-bearing
choices the owner has not confirmed; the working defaults are in policy.py and docs/FORMS_DESIGN.md. Nothing notifies anyone."""

import datetime
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.functions import Upper
from django.conf import settings
from django.http import Http404

from apps.core.foundation.audit import audit
from apps.core.foundation.permissions import can
from apps.forms.models import (ANSWER_MAX, CHOICE_MAX, DESC_MAX, KINDS, QTEXT_MAX, TITLE_MAX, Answer, Audience, Form,
                               Question, Response)  # fmt: skip

MAX_QUESTIONS, MAX_CHOICES, MAX_AUDIENCE = 50, 20, 200  # build defaults


def _text(value, limit, label, newlines=False, required=True):
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text.")
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").strip()
    if required and not value:
        raise ValidationError(f"{label} must not be empty.")
    if len(value) > limit:
        raise ValidationError(f"{label} is longer than {limit} characters.")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") and not (newlines and c == "\n") for c in value):
        raise ValidationError(f"{label} contains characters that are not allowed.")
    return value


def _load(public_id, actor, lock=False):
    qs = Form.objects.select_for_update() if lock else Form.objects
    form = qs.filter(public_id=public_id).first()
    if form is None or not (
        form.owner_id == actor.pk
        or form.audience.filter(person=actor).exists()
        or form.responses.filter(person=actor).exists()
    ):
        raise Http404
    return form


def _owned(public_id, actor, lock=True):
    form = _load(public_id, actor, lock)
    if form.owner_id != actor.pk:
        raise Http404
    return form


def create_form(actor, title, description=""):
    form = Form.objects.create(owner=actor, title=_text(title, TITLE_MAX, "Title"),
                               description=_text(description or "", DESC_MAX, "Description", True, False))  # fmt: skip
    audit(actor, "forms.create", "form", form.pk)
    return form


def _draft(form):
    if form.status != Form.DRAFT:
        raise ValidationError("Only a draft form can be changed.")


def update_details(actor, public_id, title, description):
    with transaction.atomic():
        form = _owned(public_id, actor)
        _draft(form)
        form.title = _text(title, TITLE_MAX, "Title")
        form.description = _text(description or "", DESC_MAX, "Description", True, False)
        form.version += 1
        form.save()
        audit(actor, "forms.update", "form", form.pk)
    return form


def add_question(actor, public_id, kind, text, required=False, choices=()):
    if kind not in KINDS:
        raise ValidationError("Choose a question type from the list.")
    text = _text(text, QTEXT_MAX, "Question")
    clean = []
    if kind in ("single_choice", "multi_choice"):
        clean = [_text(c, CHOICE_MAX, "Choice") for c in choices if str(c).strip()]
        if not 2 <= len(clean) <= MAX_CHOICES or len(set(clean)) != len(clean):
            raise ValidationError(f"Give between 2 and {MAX_CHOICES} different choices.")
    elif any(str(c).strip() for c in choices):
        raise ValidationError("Only choice questions take choices.")
    with transaction.atomic():
        form = _owned(public_id, actor)
        _draft(form)
        count = form.questions.count()
        if count >= MAX_QUESTIONS:
            raise ValidationError(f"A form has at most {MAX_QUESTIONS} questions.")
        Question.objects.create(form=form, position=count + 1, kind=kind, text=text, required=bool(required), choices=clean)
        form.version += 1
        form.save()
        audit(actor, "forms.question_add", "form", form.pk)
    return form


def _renumber(form, ordered):
    # two passes so the unique (form, position) constraint is never violated midway
    for i, q in enumerate(ordered, start=1):
        Question.objects.filter(pk=q.pk).update(position=1000 + i)
    for i, q in enumerate(ordered, start=1):
        Question.objects.filter(pk=q.pk).update(position=i)


def change_question(actor, public_id, question_id, action):
    if action not in ("remove", "up", "down"):
        raise ValidationError("Unknown change.")
    with transaction.atomic():
        form = _owned(public_id, actor)
        _draft(form)
        qs = list(form.questions.order_by("position"))
        idx = next((i for i, q in enumerate(qs) if str(q.pk) == str(question_id)), None)
        if idx is None:
            raise Http404
        if action == "remove":
            qs.pop(idx).delete()  # draft only: no response can exist yet
        else:
            j = idx - 1 if action == "up" else idx + 1
            if 0 <= j < len(qs):
                qs[idx], qs[j] = qs[j], qs[idx]
        _renumber(form, qs)
        form.version += 1
        form.save()
        audit(actor, "forms.question_" + action, "form", form.pk)
    return form


def set_audience(actor, public_id, employee_ids):
    ids = list(dict.fromkeys(i.strip().upper() for i in employee_ids if i.strip()))
    if len(ids) > MAX_AUDIENCE:
        raise ValidationError(f"At most {MAX_AUDIENCE} people.")
    users = list(get_user_model().objects.annotate(eid=Upper("employee_id")).filter(eid__in=ids, is_active=True, status="active"))
    if len(users) != len(ids):
        raise ValidationError("Some people could not be added. Check the employee IDs.")  # same text for every cause
    with transaction.atomic():
        form = _owned(public_id, actor)
        _draft(form)
        form.audience.all().delete()
        Audience.objects.bulk_create([Audience(form=form, person=u) for u in users])
        form.version += 1
        form.save()
        audit(actor, "forms.audience", "form", form.pk, people=len(users))
    return form


def _collection_on():
    """Real respondent collection stays off until the owner scopes the audience and identity privacy. Not a policy choice made here."""
    return bool(getattr(settings, "FORMS_COLLECTION_APPROVED", False))


def publish(actor, public_id):
    if not _collection_on():
        raise ValidationError("Publishing is switched off until the owner approves who may answer and who sees answers. Use Preview.")
    with transaction.atomic():
        form = _owned(public_id, actor)
        _draft(form)
        if not form.questions.exists() or not form.audience.exists():
            raise ValidationError("Add at least one question and one person before publishing.")
        form.status = Form.OPEN
        form.version += 1
        form.save()
        audit(actor, "forms.publish", "form", form.pk)
    return form


def close(actor, public_id):
    with transaction.atomic():
        form = _owned(public_id, actor)
        if form.status == Form.OPEN:
            form.status = Form.CLOSED
            form.version += 1
            form.save()
            audit(actor, "forms.close", "form", form.pk)
    return form


def clean_answers(questions, posted):
    """posted: {question_pk(str): value or list}. Returns {question_pk: stored text}. Raises ValidationError with every problem."""
    problems, out = [], {}
    for q in questions:
        label = f"Question {q.position}"
        raw = posted.get(str(q.pk))
        empty = raw in (None, "", []) or (isinstance(raw, list) and not any(str(x).strip() for x in raw))
        if empty:
            if q.required:
                problems.append(f"{label} needs an answer.")
            continue
        try:
            if q.kind in ("short_text", "long_text"):
                if isinstance(raw, list):
                    raise ValidationError("Give one answer.")
                out[str(q.pk)] = _text(raw, 300 if q.kind == "short_text" else ANSWER_MAX, label, q.kind == "long_text")
            elif q.kind == "single_choice":
                if raw not in q.choices:
                    raise ValidationError("Pick one of the choices.")
                out[str(q.pk)] = raw
            elif q.kind == "multi_choice":
                vals = raw if isinstance(raw, list) else [raw]
                if not vals or any(v not in q.choices for v in vals) or len(set(vals)) != len(vals):
                    raise ValidationError("Pick from the choices, each at most once.")
                out[str(q.pk)] = json.dumps([c for c in q.choices if c in vals])
            elif q.kind == "number":
                s = str(raw).strip()
                if len(s) > 40 or not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", s):
                    raise ValidationError("Enter a plain number.")
                d = Decimal(s)
                out[str(q.pk)] = format(d.normalize() if d != 0 else Decimal(0), "f")
            elif q.kind == "date":
                out[str(q.pk)] = datetime.date.fromisoformat(str(raw).strip()).isoformat()
            elif q.kind == "scale_1_5":
                if str(raw).strip() not in ("1", "2", "3", "4", "5"):
                    raise ValidationError("Pick 1 to 5.")
                out[str(q.pk)] = str(raw).strip()
        except (ValidationError, InvalidOperation, ValueError, TypeError) as exc:
            msg = " ".join(exc.messages) if isinstance(exc, ValidationError) else "That answer is not valid."
            problems.append(f"{label}: {msg}")
    extra = set(posted) - {str(q.pk) for q in questions}
    if extra:
        problems.append("The form has changed. Reload and try again.")
    if problems:
        raise ValidationError(problems)
    return out


def preview_check(actor, public_id, posted):
    """Owner-only dry run of the fill page: validates the answers exactly like a real submit and stores nothing."""
    form = _owned(public_id, actor, lock=False)
    return clean_answers(list(form.questions.order_by("position")), posted)


def submit_response(actor, public_id, posted):
    if not _collection_on():
        raise Http404
    with transaction.atomic():
        form = _load(public_id, actor, lock=True)
        if form.owner_id == actor.pk and not form.audience.filter(person=actor).exists():
            raise Http404  # the owner previews; they do not submit unless they put themselves on the audience
        if not can(actor, "fill", "forms.form", form):
            if form.responses.filter(person=actor).exists():
                raise ValidationError("You have already answered this form.")
            raise Http404
        if form.status != Form.OPEN:
            raise ValidationError("This form is not open.")
        if form.responses.filter(person=actor).exists():
            raise ValidationError("You have already answered this form.")
        questions = list(form.questions.order_by("position"))
        answers = clean_answers(questions, posted)
        try:
            with transaction.atomic():
                resp = Response.objects.create(form=form, person=actor, form_version=form.version)
        except IntegrityError:
            raise ValidationError("You have already answered this form.") from None
        Answer.objects.bulk_create([Answer(response=resp, question_id=int(qid), value=v) for qid, v in answers.items()])
        audit(actor, "forms.submit", "form", form.pk)
    return resp


def get_form_for(actor, public_id):
    return _load(public_id, actor)


def my_response(actor, form):
    resp = form.responses.filter(person=actor).first()
    if resp is None:
        return None
    vals = {a.question_id: a.value for a in resp.answers.all()}
    return [(q, vals.get(q.pk)) for q in form.questions.order_by("position")]


def results(actor, public_id):
    form = _owned(public_id, actor, lock=False)
    questions = list(form.questions.order_by("position"))
    rows = []
    for r in form.responses.select_related("person").order_by("submitted_at", "pk"):
        vals = {a.question_id: a.value for a in r.answers.all()}
        rows.append((r, [vals.get(q.pk, "") for q in questions]))
    counts = []
    for q in questions:
        if q.kind in ("single_choice", "multi_choice", "scale_1_5"):
            options = q.choices if q.kind != "scale_1_5" else ["1", "2", "3", "4", "5"]
            tally = {o: 0 for o in options}
            for _r, vals in rows:
                v = vals[questions.index(q)]
                for o in (json.loads(v) if (q.kind == "multi_choice" and v) else ([v] if v else [])):
                    if o in tally:
                        tally[o] += 1
            counts.append((q, list(tally.items())))
    return form, questions, rows, counts


def csv_safe(cell):
    """A spreadsheet runs a cell that starts with = + - @ tab or CR as a formula. Prefix those with an apostrophe."""
    cell = "" if cell is None else str(cell)
    return "'" + cell if cell[:1] in ("=", "+", "-", "@", "\t", "\r") else cell


def csv_text(actor, public_id):
    import csv
    import io

    form, questions, rows, _c = results(actor, public_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Employee ID", "Submitted"] + [csv_safe(q.text) for q in questions])
    for r, vals in rows:
        w.writerow([csv_safe(r.person.employee_id), r.submitted_at.isoformat()] + [csv_safe(v) for v in vals])
    audit(actor, "forms.export", "form", form.pk, rows=len(rows))
    return buf.getvalue()
