import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import Client

from apps.accounts.models import CustomUser
from apps.core.models import AuditLog, Notification
from apps.posts.models import PostComment
from apps.posts.services import add_comment, create_post
from tests.unit.test_posts_spec import department, employee_alpha, manager_user, team_alpha, team_beta  # noqa: F401


@pytest.fixture
def outsider(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-2999", full_name="Dan Outsider", password="ValidPassword123!",
        role=CustomUser.Role.EMPLOYEE, status=CustomUser.Status.ACTIVE,
    )  # fmt: skip


@pytest.mark.django_db
def test_service_refuses_a_commenter_who_cannot_see_the_post_and_writes_nothing(employee_alpha, outsider):
    post = create_post(author=employee_alpha, title="Alpha log", content="Alpha body")
    before_audit = AuditLog.objects.count()
    with pytest.raises(PermissionDenied):
        add_comment(post=post, author=outsider, content="should not land")
    assert not PostComment.objects.filter(post=post).exists()
    assert AuditLog.objects.count() == before_audit
    assert not Notification.objects.filter(kind="posts.commented").exists()
    # the HTTP view gives the same person the same 403 it always did
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(outsider)
    assert c.post(f"/posts/{post.pk}/comments/", {"content": "nope"}).status_code == 403
    assert not PostComment.objects.filter(post=post).exists()


@pytest.mark.django_db
def test_service_still_allows_the_author_and_a_person_who_can_see_the_post(employee_alpha, manager_user):
    post = create_post(author=employee_alpha, title="Alpha log", content="Alpha body")
    add_comment(post=post, author=employee_alpha, content="mine")
    add_comment(post=post, author=manager_user, content="team lead can see it")
    assert PostComment.objects.filter(post=post).count() == 2


@pytest.mark.django_db
def test_the_pilot_seed_command_still_runs():
    call_command("seed_pilot_data", "--default-password", "ValidPassword123!")
    assert PostComment.objects.count() == 2
    assert sorted(PostComment.objects.values_list("author__employee_id", flat=True)) == ["ST-ADM-0001", "ST-MGR-1001"]


@pytest.mark.django_db
def test_a_member_of_another_team_is_refused_by_the_service_and_the_view(employee_alpha, team_beta):
    # the case the old test_add_and_list_comments covered by accident: a team_beta member on a team_alpha post
    beta = CustomUser.objects.create_user(
        employee_id="ST-EMP-2002", full_name="Beta Member", password="ValidPassword123!",
        role=CustomUser.Role.EMPLOYEE, status=CustomUser.Status.ACTIVE,
    )  # fmt: skip
    from apps.directory.models import TeamMembership

    TeamMembership.objects.create(team=team_beta, user=beta)
    post = create_post(author=employee_alpha, title="Alpha log", content="Alpha body")
    with pytest.raises(PermissionDenied):
        add_comment(post=post, author=beta, content="cross-team")
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(beta)
    assert c.post(f"/posts/{post.pk}/comments/", {"content": "cross-team"}).status_code == 403
    assert not PostComment.objects.filter(post=post).exists()
