from unittest import mock

import pytest
from django.test import Client

from apps.core.models import Notification
from apps.posts.models import Post, PostComment
from apps.posts.services import add_comment, create_post
from tests.unit.test_posts_spec import department, employee_alpha, manager_user, team_alpha  # noqa: F401


def _post(author):
    return create_post(author=author, title="Secret quarterly figures", content="Body text")


@pytest.mark.django_db
def test_author_gets_one_notice_per_comment_by_someone_else_and_own_comment_none(employee_alpha, manager_user):
    post = _post(employee_alpha)
    add_comment(post=post, author=employee_alpha, content="my own follow-up")
    assert not Notification.objects.filter(kind="posts.commented").exists()
    add_comment(post=post, author=manager_user, content="private remark QQQ")
    add_comment(post=post, author=manager_user, content="second remark")
    ns = list(Notification.objects.filter(kind="posts.commented"))
    assert len(ns) == 2 and {n.user_id for n in ns} == {employee_alpha.pk}  # one per comment, to the author only
    assert ns[0].title == "Bob Manager commented on your post"
    assert all("QQQ" not in n.title and "Secret" not in n.title and "QQQ" not in n.link for n in ns)
    assert ns[0].link == f"/posts/{post.pk}/"
    assert not Notification.objects.filter(user=manager_user).exists()
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(employee_alpha)
    assert c.get(ns[0].link).status_code == 200


@pytest.mark.django_db
def test_comment_through_the_real_view_notifies_and_a_failed_notice_keeps_the_comment(employee_alpha, manager_user):
    post = _post(employee_alpha)
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(manager_user)
    assert c.post(f"/posts/{post.pk}/comments/", {"content": "via the form"}).status_code == 302
    assert Notification.objects.filter(kind="posts.commented", user=employee_alpha).count() == 1
    with mock.patch("apps.posts.services.notify", side_effect=RuntimeError("boom")):
        add_comment(post=post, author=manager_user, content="notice will fail")
    assert PostComment.objects.filter(post=post).count() == 2
    assert Notification.objects.filter(kind="posts.commented").count() == 1
