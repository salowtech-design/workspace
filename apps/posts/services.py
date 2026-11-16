import uuid
from datetime import date
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.accounts.permissions import (
    PERM_POST_CREATE,
    PERM_POST_MODERATE,
    has_permission,
)
from apps.core.models import OutboxEvent
from apps.core.services import record_audit_event
from apps.directory.models import Team
from apps.posts.models import Post, PostComment


def create_post(
    *,
    author: CustomUser,
    title: str,
    content: str,
    category: str = Post.Category.DAILY_STANDUP,
    team: Optional[Team] = None,
    work_date: Optional[date] = None,
    status: str = Post.Status.SUBMITTED,
) -> Post:
    """Create a new work log or communication post."""
    if not has_permission(author, PERM_POST_CREATE):
        raise PermissionDenied("User does not have permission to create posts.")

    if not title or not title.strip():
        raise ValidationError("Title cannot be empty.")
    if not content or not content.strip():
        raise ValidationError("Content cannot be empty.")

    if not team:
        # Default to first team membership of the author
        first_membership = author.team_memberships.select_related("team").first()
        if first_membership:
            team = first_membership.team

    if not work_date:
        work_date = (
            timezone.localdate() if timezone.is_aware(timezone.now()) else timezone.now().date()
        )

    with transaction.atomic():
        post = Post.objects.create(
            author=author,
            team=team,
            title=title.strip(),
            content=content.strip(),
            category=category,
            status=status,
            work_date=work_date,
        )

        record_audit_event(
            actor=author,
            action="post_created",
            target_type="post",
            target_id=str(post.id),
            payload_summary={
                "title": post.title,
                "category": post.category,
                "team_id": post.team_id,
                "work_date": str(post.work_date),
            },
        )

        OutboxEvent.objects.create(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            event_type="post.created",
            payload={"post_id": post.id, "author_id": author.id, "category": post.category},
        )

    return post


def update_post(
    *,
    post: Post,
    actor: CustomUser,
    title: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
) -> Post:
    """Update post title, content, or category. Only author or moderator allowed."""
    is_moderator = has_permission(actor, PERM_POST_MODERATE)
    if post.author_id != actor.id and not is_moderator:
        raise PermissionDenied("You do not have permission to edit this post.")

    if post.status == Post.Status.APPROVED and not is_moderator:
        raise ValidationError("Approved posts cannot be edited by the author.")

    update_fields = ["updated_at"]
    if title is not None:
        if not title.strip():
            raise ValidationError("Title cannot be empty.")
        post.title = title.strip()
        update_fields.append("title")

    if content is not None:
        if not content.strip():
            raise ValidationError("Content cannot be empty.")
        post.content = content.strip()
        update_fields.append("content")

    if category is not None:
        post.category = category
        update_fields.append("category")

    with transaction.atomic():
        post.save(update_fields=update_fields)
        record_audit_event(
            actor=actor,
            action="post_updated",
            target_type="post",
            target_id=str(post.id),
            payload_summary={"updated_fields": update_fields},
        )

    return post


def review_post(
    *,
    post: Post,
    reviewer: CustomUser,
    status: str,
    review_notes: str = "",
) -> Post:
    """
    Manager or Administrator review sign-off.
    Enforces no self-review.
    """
    if post.author_id == reviewer.id:
        raise ValidationError("Self-review prohibited: you cannot approve or review your own post.")

    is_admin = reviewer.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)
    is_manager = reviewer.role == CustomUser.Role.MANAGER

    can_review = is_admin
    if is_manager:
        # Check if reviewer is lead of post's team or direct manager of author
        is_team_lead = post.team and post.team.lead_id == reviewer.id
        is_direct_mgr = (
            hasattr(post.author, "manager_relationship")
            and post.author.manager_relationship.manager_id == reviewer.id
        )
        if is_team_lead or is_direct_mgr:
            can_review = True

    if not can_review:
        raise PermissionDenied("You do not have permission to review this post.")

    if status not in (Post.Status.APPROVED, Post.Status.FLAGGED):
        raise ValidationError(f"Invalid review status: '{status}'.")

    with transaction.atomic():
        post.status = status
        post.reviewed_by = reviewer
        post.reviewed_at = timezone.now()
        post.review_notes = review_notes.strip()
        post.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"]
        )

        record_audit_event(
            actor=reviewer,
            action=f"post_{status}",
            target_type="post",
            target_id=str(post.id),
            payload_summary={
                "status": status,
                "reviewer_id": reviewer.id,
                "notes": review_notes.strip()[:200],
            },
        )

        OutboxEvent.objects.create(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            event_type=f"post.{status}",
            payload={"post_id": post.id, "reviewer_id": reviewer.id, "status": status},
        )

    return post


def add_comment(
    *,
    post: Post,
    author: CustomUser,
    content: str,
) -> PostComment:
    """Add a discussion comment to a post."""
    if not author.is_authenticated or not author.is_active:
        raise PermissionDenied("Authentication required to add comments.")

    if not content or not content.strip():
        raise ValidationError("Comment content cannot be empty.")

    with transaction.atomic():
        comment = PostComment.objects.create(
            post=post,
            author=author,
            content=content.strip(),
        )

        record_audit_event(
            actor=author,
            action="post_comment_created",
            target_type="post_comment",
            target_id=str(comment.id),
            payload_summary={"post_id": post.id},
        )

    return comment
