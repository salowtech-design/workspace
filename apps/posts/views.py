from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import CustomUser
from apps.accounts.permissions import (
    PERM_POST_CREATE,
    PERM_POST_MODERATE,
    has_permission,
)
from apps.directory.models import Team
from apps.posts.forms import PostCommentForm, PostForm, PostReviewForm
from apps.posts.models import Post
from apps.posts.selectors import (
    get_post_by_id,
    list_active_templates,
    list_comments_for_post,
    list_posts_for_user,
)
from apps.posts.services import add_comment, create_post, review_post, update_post


@login_required
def post_feed_view(request):
    """Feed listing work logs and team posts with filters."""
    category = request.GET.get("category", "")
    team_id_raw = request.GET.get("team", "")
    search = request.GET.get("q", "")
    status = request.GET.get("status", "")

    team_id = int(team_id_raw) if team_id_raw.isdigit() else None

    posts = list_posts_for_user(
        request.user,
        category=category or None,
        team_id=team_id,
        search=search or None,
        status=status or None,
    )

    teams = (
        Team.objects.all()
        if request.user.role in ("super_admin", "admin")
        else (
            Team.objects.filter(Q(memberships__user=request.user) | Q(lead=request.user)).distinct()
        )
    )

    context = {
        "posts": posts,
        "categories": Post.Category.choices,
        "statuses": Post.Status.choices,
        "selected_category": category,
        "selected_team": team_id,
        "selected_status": status,
        "search_query": search,
        "teams": teams,
    }

    if request.headers.get("HX-Request"):
        return render(request, "posts/partials/post_list.html", context)

    return render(request, "posts/feed.html", context)


@login_required
def post_detail_view(request, post_id: int):
    """View a single post with its comment thread and manager review controls."""
    post = get_post_by_id(post_id)
    if not post:
        messages.error(request, "Post not found.")
        return redirect("posts:feed")

    # Access check: can this user see this post?
    visible_posts = list_posts_for_user(request.user)
    if not visible_posts.filter(id=post.id).exists():
        raise PermissionDenied("You do not have permission to view this post.")

    comments = list_comments_for_post(post)
    comment_form = PostCommentForm()

    # Can this user review the post?
    can_review = False
    if request.user.id != post.author_id:
        if request.user.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
            can_review = True
        elif request.user.role == CustomUser.Role.MANAGER:
            is_lead = post.team and post.team.lead_id == request.user.id
            is_mgr = (
                hasattr(post.author, "manager_relationship")
                and post.author.manager_relationship.manager_id == request.user.id
            )
            can_review = is_lead or is_mgr

    review_form = PostReviewForm(initial={"status": post.status}) if can_review else None

    context = {
        "post": post,
        "comments": comments,
        "comment_form": comment_form,
        "can_review": can_review,
        "review_form": review_form,
        "can_edit": post.author_id == request.user.id and post.status != Post.Status.APPROVED,
    }
    return render(request, "posts/detail.html", context)


@login_required
def post_create_view(request):
    """Create a new work log or standup entry."""
    if not has_permission(request.user, PERM_POST_CREATE):
        raise PermissionDenied("You do not have permission to create posts.")

    templates = list_active_templates()

    if request.method == "POST":
        form = PostForm(request.POST, author=request.user)
        if form.is_valid():
            try:
                post = create_post(
                    author=request.user,
                    title=form.cleaned_data["title"],
                    content=form.cleaned_data["content"],
                    category=form.cleaned_data["category"],
                    team=form.cleaned_data.get("team"),
                    work_date=form.cleaned_data.get("work_date"),
                )
                messages.success(request, f"Work log '{post.title}' submitted successfully.")
                return redirect("posts:detail", post_id=post.id)
            except ValidationError as err:
                messages.error(request, str(err))
    else:
        initial = {}
        template_id = request.GET.get("template")
        if template_id and template_id.isdigit():
            tpl = templates.filter(id=int(template_id)).first()
            if tpl:
                initial["category"] = tpl.category
                initial["content"] = tpl.template_text
        form = PostForm(initial=initial, author=request.user)

    context = {
        "form": form,
        "templates": templates,
    }
    return render(request, "posts/form.html", context)


@login_required
def post_edit_view(request, post_id: int):
    """Edit an existing post."""
    post = get_object_or_404(Post, id=post_id)
    is_moderator = has_permission(request.user, PERM_POST_MODERATE)

    if post.author_id != request.user.id and not is_moderator:
        raise PermissionDenied("You do not have permission to edit this post.")
    if post.status == Post.Status.APPROVED and not is_moderator:
        raise PermissionDenied("Approved posts cannot be edited.")

    if request.method == "POST":
        form = PostForm(request.POST, instance=post, author=request.user)
        if form.is_valid():
            try:
                update_post(
                    post=post,
                    actor=request.user,
                    title=form.cleaned_data["title"],
                    content=form.cleaned_data["content"],
                    category=form.cleaned_data["category"],
                )
                messages.success(request, "Post updated successfully.")
                return redirect("posts:detail", post_id=post.id)
            except (ValidationError, PermissionDenied) as err:
                messages.error(request, str(err))
    else:
        form = PostForm(instance=post, author=request.user)

    return render(request, "posts/form.html", {"form": form, "post": post, "is_edit": True})


@login_required
@require_POST
def post_review_view(request, post_id: int):
    """Manager sign-off or feedback submission."""
    post = get_object_or_404(Post, id=post_id)
    form = PostReviewForm(request.POST)
    if form.is_valid():
        try:
            review_post(
                post=post,
                reviewer=request.user,
                status=form.cleaned_data["status"],
                review_notes=form.cleaned_data.get("review_notes", ""),
            )
            messages.success(request, f"Post status updated to {post.get_status_display()}.")
        except ValidationError as err:
            messages.error(request, str(err))
    else:
        messages.error(request, "Invalid review form data.")

    return redirect("posts:detail", post_id=post.id)


@login_required
@require_POST
def post_comment_view(request, post_id: int):
    """Post comment submission handler."""
    post = get_object_or_404(Post, id=post_id)

    # Scoping check: user must be permitted to view the post to comment
    visible_posts = list_posts_for_user(request.user)
    if not visible_posts.filter(id=post.id).exists():
        raise PermissionDenied("You do not have permission to comment on this post.")

    form = PostCommentForm(request.POST)
    if form.is_valid():
        try:
            add_comment(
                post=post,
                author=request.user,
                content=form.cleaned_data["content"],
            )
            messages.success(request, "Comment posted.")
        except ValidationError as err:
            messages.error(request, str(err))

    if request.headers.get("HX-Request"):
        comments = list_comments_for_post(post)
        return render(request, "posts/partials/comment_list.html", {"comments": comments})

    return redirect("posts:detail", post_id=post.id)
