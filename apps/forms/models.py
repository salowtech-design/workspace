"""Salow Forms, internal first (see docs/FORMS_DESIGN.md). Typed answers only: no file questions, no file bytes.
No delete path exists and none should be built until retention is decided: every foreign key is PROTECT.
Who may fill a form and who sees responses are load-bearing choices still awaiting the owner; services.py documents the
working defaults used until then. Choices below are Django-level declarations (checked by full_clean, not by the database)."""

import uuid

from django.conf import settings
from django.db import models

TITLE_MAX, DESC_MAX, QTEXT_MAX, CHOICE_MAX, ANSWER_MAX = 200, 2000, 300, 100, 2000
KINDS = ("short_text", "long_text", "single_choice", "multi_choice", "number", "date", "scale_1_5")


class Form(models.Model):
    DRAFT, OPEN, CLOSED = "draft", "open", "closed"
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_forms")
    title = models.CharField(max_length=TITLE_MAX)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=6, default=DRAFT, choices=[(x, x) for x in (DRAFT, OPEN, CLOSED)])
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Question(models.Model):
    form = models.ForeignKey(Form, on_delete=models.PROTECT, related_name="questions")
    position = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=14, choices=[(k, k) for k in KINDS])
    text = models.CharField(max_length=QTEXT_MAX)
    required = models.BooleanField(default=False)
    choices = models.JSONField(default=list)

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["form", "position"], name="forms_question_position_unique")]


class Audience(models.Model):
    form = models.ForeignKey(Form, on_delete=models.PROTECT, related_name="audience")
    person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["form", "person"], name="forms_audience_unique")]


class Response(models.Model):
    form = models.ForeignKey(Form, on_delete=models.PROTECT, related_name="responses")
    person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    form_version = models.PositiveIntegerField()
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["form", "person"], name="forms_response_one_per_person")]


class Answer(models.Model):
    response = models.ForeignKey(Response, on_delete=models.PROTECT, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="+")
    value = models.TextField()  # text; multi_choice is a JSON list

    class Meta:
        constraints = [models.UniqueConstraint(fields=["response", "question"], name="forms_answer_unique")]
