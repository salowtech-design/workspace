from typing import Optional

from django.db.models import Q, QuerySet

from apps.accounts.models import CustomUser
from apps.accounts.permissions import PERM_POST_VIEW_ALL, PERM_POST_VIEW_TEAM, has_permission
from apps.posts.models import Post, PostComment, PostTemplate


def get_post_by_id(post_id: int) -> Optional[Post]:
    """Retrieve a single post by primary key with related author and team."""
    return (
        Post.objects.select_related("author", "team", "reviewed_by")
        .prefetch_related("comments__author")
        .filter(id=post_id)
        .first()
    )


def _apply_post_filters(
    qs: QuerySet[Post],
    *,
    team_id: Optional[int],
    category: Optional[str],
    author_id: Optional[int],
    status: Optional[str],
    start_date,
    end_date,
    search: Optional[str],
) -> QuerySet[Post]:
    """Apply secondary filters and keyword search to a scoped queryset."""
    if team_id:
        qs = qs.filter(team_id=team_id)
    if category:
        qs = qs.filter(category=category)
    if author_id:
        qs = qs.filter(author_id=author_id)
    if status:
        qs = qs.filter(status=status)
    if start_date:
        qs = qs.filter(work_date__gte=start_date)
    if end_date:
        qs = qs.filter(work_date__lte=end_date)
    if search:
        search_term = search.strip()
        qs = qs.filter(
            Q(title__icontains=search_term)
            | Q(content__icontains=search_term)
            | Q(author__employee_id__icontains=search_term)
            | Q(author__full_name__icontains=search_term)
        )
    return qs


def list_posts_for_user(
    user: CustomUser,
    *,
    team_id: Optional[int] = None,
    category: Optional[str] = None,
    author_id: Optional[int] = None,
    search: Optional[str] = None,
    start_date=None,
    end_date=None,
    status: Optional[str] = None,
) -> QuerySet[Post]:
    """
    Fetch posts visible to the given user based on role and team boundaries.
    Applies optional filtering for search, team, category, status, and dates.
    """
    qs = Post.objects.select_related("author", "team", "reviewed_by").all()

    # Permission scoping
    if has_permission(user, PERM_POST_VIEW_ALL):
        pass
    elif has_permission(user, PERM_POST_VIEW_TEAM):
        user_team_ids = set(user.team_memberships.values_list("team_id", flat=True))
        if hasattr(user, "led_team") and user.led_team:
            user_team_ids.add(user.led_team.id)
        qs = qs.filter(Q(author=user) | Q(team_id__in=user_team_ids))
    else:
        qs = qs.filter(author=user)

    qs = _apply_post_filters(
        qs,
        team_id=team_id,
        category=category,
        author_id=author_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        search=search,
    )
    return qs.order_by("-work_date", "-created_at")


def list_comments_for_post(post: Post) -> QuerySet[PostComment]:
    """Fetch comments for a post ordered chronologically."""
    return post.comments.select_related("author").order_by("created_at")


def list_active_templates() -> QuerySet[PostTemplate]:
    """Return all active post templates for selection."""
    return PostTemplate.objects.filter(is_active=True).order_by("name")
