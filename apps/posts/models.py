from django.conf import settings
from django.db import models
from django.utils import timezone


class PostTemplate(models.Model):
    """Reusable template for daily logs, standups, or reports."""

    name = models.CharField(max_length=64, unique=True)
    category = models.CharField(max_length=32, db_index=True)
    template_text = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Post Template"
        verbose_name_plural = "Post Templates"

    def __str__(self):
        return f"{self.name} ({self.category})"


class Post(models.Model):
    """
    Work log entry, daily standup, milestone or communication update.
    Supports team association, manager sign-off, and audit trail.
    """

    class Category(models.TextChoices):
        DAILY_STANDUP = "daily_standup", "Daily Standup"
        BLOCKER = "blocker", "Blocker / Impediment"
        MILESTONE = "milestone", "Milestone Update"
        GENERAL = "general", "General Communication"
        WEEKLY_SUMMARY = "weekly_summary", "Weekly Summary"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved / Signed Off"
        FLAGGED = "flagged", "Flagged for Revision"

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="posts",
        db_index=True,
    )
    team = models.ForeignKey(
        "directory.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
        db_index=True,
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        default=Category.DAILY_STANDUP,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )
    work_date = models.DateField(default=timezone.now, db_index=True)

    # Manager / Reviewer sign-off
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_posts",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "-created_at"]
        verbose_name = "Work Log / Post"
        verbose_name_plural = "Work Logs / Posts"

    def __str__(self):
        return f"[{self.work_date}] {self.author.employee_id} - {self.title}"


class PostComment(models.Model):
    """Discussion thread attached to a work log or post."""

    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="comments",
        db_index=True,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="post_comments",
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Post Comment"
        verbose_name_plural = "Post Comments"

    def __str__(self):
        return f"Comment by {self.author.employee_id} on Post {self.post_id}"
