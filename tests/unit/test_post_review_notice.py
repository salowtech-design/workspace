from unittest import mock

import pytest
from django.test import Client

from apps.core.models import Notification
from apps.posts.models import Post
from apps.posts.services import create_post, review_post
from tests.unit.test_posts_spec import department, employee_alpha, manager_user, team_alpha  # noqa: F401


def _post(author):
    return create_post(author=author, title="Secret quarterly figures", content="Body text")


@pytest.mark.django_db
def test_author_gets_one_notice_per_review_with_no_post_content_and_can_open_it(employee_alpha, manager_user):
    post = _post(employee_alpha)
    review_post(post=post, reviewer=manager_user, status=Post.Status.FLAGGED, review_notes="private note XYZ")
    ns = list(Notification.objects.filter(kind="posts.reviewed"))
    assert len(ns) == 1 and ns[0].user_id == employee_alpha.pk
    assert ns[0].title == "Bob Manager flagged your post"
    assert "Secret" not in ns[0].title and "XYZ" not in ns[0].title and "XYZ" not in ns[0].link
    assert ns[0].link == f"/posts/{post.pk}/"
    assert not Notification.objects.filter(user=manager_user).exists()  # never the reviewer
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(employee_alpha)
    assert c.get(ns[0].link).status_code == 200
    review_post(post=post, reviewer=manager_user, status=Post.Status.APPROVED)
    assert sorted(Notification.objects.filter(kind="posts.reviewed").values_list("title", flat=True)) == [
        "Bob Manager approved your post", "Bob Manager flagged your post"]


@pytest.mark.django_db
def test_a_failed_notice_leaves_the_review_saved(employee_alpha, manager_user):
    post = _post(employee_alpha)
    with mock.patch("apps.posts.services.notify", side_effect=RuntimeError("boom")):
        review_post(post=post, reviewer=manager_user, status=Post.Status.APPROVED)
    post.refresh_from_db()
    assert post.status == Post.Status.APPROVED and post.reviewed_by_id == manager_user.pk
    assert not Notification.objects.filter(kind="posts.reviewed").exists()
