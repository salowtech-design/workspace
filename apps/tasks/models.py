import uuid

from django.conf import settings
from django.db import models

TITLE_MAX = 1024  # same limits as the benchmarked product
NOTES_MAX = 8192


class TaskList(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="task_lists"
    )
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    delete_batch = models.UUIDField(null=True, blank=True)  # one value per delete call: what a restore undoes. Empty on rows deleted before restore existed.

    class Meta:
        indexes = [models.Index(fields=["owner", "deleted_at"])]


class Task(models.Model):
    OPEN, DONE = "open", "completed"
    STATUSES = [(OPEN, "Open"), (DONE, "Completed")]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    task_list = models.ForeignKey(TaskList, on_delete=models.PROTECT, related_name="tasks")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="subtasks"
    )
    title = models.CharField(max_length=TITLE_MAX)
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUSES, default=OPEN)
    due_date = models.DateField(null=True, blank=True)  # a day, not a time (benchmark: date only)
    completed_at = models.DateTimeField(null=True, blank=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    position = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)  # compare-and-set on every edit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    delete_batch = models.UUIDField(null=True, blank=True)  # one value per delete call (see TaskList.delete_batch)

    class Meta:
        indexes = [
            models.Index(fields=["task_list", "deleted_at", "position"]),
            models.Index(fields=["assignee", "deleted_at", "status"]),
        ]


class Project(models.Model):
    """A shared space. Only members can see it. Separate from personal lists."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)


class ProjectMember(models.Model):
    LEAD, MEMBER = "lead", "member"
    ROLES = [(LEAD, "Lead"), (MEMBER, "Member")]

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="members")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="project_memberships"
    )
    role = models.CharField(max_length=8, choices=ROLES, default=MEMBER)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    added_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["person", "removed_at"])]
        constraints = [
            models.UniqueConstraint(fields=["project", "person"], name="uniq_project_person")
        ]


class Column(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="columns")
    name = models.CharField(max_length=60)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class Milestone(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="milestones")
    name = models.CharField(max_length=120)
    due_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)


class ProjectTask(models.Model):
    """A task that belongs to a project. Not a personal-list Task and never shown outside the
    project's members."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="tasks")
    column = models.ForeignKey(Column, on_delete=models.PROTECT, related_name="tasks")
    title = models.CharField(max_length=TITLE_MAX)
    notes = models.TextField(blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    milestone = models.ForeignKey(
        Milestone, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks"
    )
    position = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["project", "column", "deleted_at", "position"])]


COMMENT_MAX = 2000  # build default, not policy


class TaskComment(models.Model):
    """A plain-text note on a project task. Add and read only: editing, deleting and keeping
    comments are unselected policy, so none are built."""

    task = models.ForeignKey(ProjectTask, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["task", "created_at"])]
