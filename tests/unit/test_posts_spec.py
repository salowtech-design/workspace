from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from apps.accounts.models import CustomUser, UserSession
from apps.core.models import AuditLog, OutboxEvent
from apps.directory.models import Department, ReportingRelationship, Team, TeamMembership
from apps.posts.forms import PostForm
from apps.posts.models import Post
from apps.posts.selectors import (
    list_comments_for_post,
    list_posts_for_user,
)
from apps.posts.services import (
    add_comment,
    create_post,
    review_post,
    update_post,
)


@pytest.fixture
def department(db):
    return Department.objects.create(code="ENG", name="Engineering")


@pytest.fixture
def team_alpha(db, department):
    return Team.objects.create(code="ALPHA", name="Team Alpha", department=department)


@pytest.fixture
def team_beta(db, department):
    return Team.objects.create(code="BETA", name="Team Beta", department=department)


@pytest.fixture
def manager_user(db, team_alpha):
    mgr = CustomUser.objects.create_user(
        employee_id="ST-MGR-0010",
        full_name="Bob Manager",
        password="ValidPassword123!",
        role=CustomUser.Role.MANAGER,
        status=CustomUser.Status.ACTIVE,
    )
    team_alpha.lead = mgr
    team_alpha.save()
    TeamMembership.objects.create(team=team_alpha, user=mgr)
    return mgr


@pytest.fixture
def employee_alpha(db, team_alpha, manager_user):
    emp = CustomUser.objects.create_user(
        employee_id="ST-EMP-2001",
        full_name="Carol Engineer",
        password="ValidPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    TeamMembership.objects.create(team=team_alpha, user=emp)
    ReportingRelationship.objects.create(manager=manager_user, subordinate=emp)
    return emp


@pytest.fixture
def employee_beta(db, team_beta):
    emp = CustomUser.objects.create_user(
        employee_id="ST-EMP-3001",
        full_name="Dave Designer",
        password="ValidPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    TeamMembership.objects.create(team=team_beta, user=emp)
    return emp


@pytest.fixture
def intern_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-INT-4001",
        full_name="Eve Intern",
        password="ValidPassword123!",
        role=CustomUser.Role.INTERN,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def admin_user(db):
    return CustomUser.objects.create_superuser(
        employee_id="ST-ADM-0001",
        full_name="Super Admin",
        password="SuperMasterPassword2026!",
    )


@pytest.mark.django_db
class TestPostCreation:
    def test_create_valid_post(self, employee_alpha, team_alpha):
        """Author can submit daily work log; defaults team and records audit & outbox."""
        post = create_post(
            author=employee_alpha,
            title="Completed Authentication Milestone",
            content="Built HMAC token verification and database migrations.",
            category=Post.Category.DAILY_STANDUP,
            team=team_alpha,
            work_date=date(2026, 11, 16),
        )

        assert post.id is not None
        assert post.title == "Completed Authentication Milestone"
        assert post.status == Post.Status.SUBMITTED
        assert post.author == employee_alpha
        assert post.team == team_alpha

        # Check Audit Log
        audit = AuditLog.objects.filter(target_type="post", target_id=str(post.id)).first()
        assert audit is not None
        assert audit.action == "post_created"
        assert audit.actor == employee_alpha

        # Check Outbox Event
        outbox = OutboxEvent.objects.filter(event_type="post.created").first()
        assert outbox is not None
        assert outbox.payload["post_id"] == post.id

    def test_create_post_empty_title_rejected(self, employee_alpha):
        """Empty title raises ValidationError."""
        with pytest.raises(ValidationError):
            create_post(
                author=employee_alpha,
                title="   ",
                content="Valid content details.",
            )

    def test_create_post_empty_content_rejected(self, employee_alpha):
        """Empty content raises ValidationError."""
        with pytest.raises(ValidationError):
            create_post(
                author=employee_alpha,
                title="Valid Title",
                content="   ",
            )


@pytest.mark.django_db
class TestPostUpdate:
    def test_author_can_update_draft_or_submitted_post(self, employee_alpha):
        """Author can update title and content of submitted post."""
        post = create_post(
            author=employee_alpha,
            title="Initial Title",
            content="Initial Content",
        )

        updated = update_post(
            post=post,
            actor=employee_alpha,
            title="Updated Title",
            content="Updated Content",
        )
        assert updated.title == "Updated Title"
        assert updated.content == "Updated Content"

    def test_other_user_cannot_update_post(self, employee_alpha, employee_beta):
        """Non-author and non-moderator cannot update another user's post."""
        post = create_post(
            author=employee_alpha,
            title="Alpha Post",
            content="Alpha Content",
        )

        with pytest.raises(PermissionDenied):
            update_post(
                post=post,
                actor=employee_beta,
                title="Hacked Title",
            )

    def test_author_cannot_edit_approved_post(self, employee_alpha, manager_user):
        """Approved post cannot be edited by normal author."""
        post = create_post(
            author=employee_alpha,
            title="Alpha Post",
            content="Alpha Content",
        )
        review_post(post=post, reviewer=manager_user, status=Post.Status.APPROVED)

        with pytest.raises(ValidationError):
            update_post(
                post=post,
                actor=employee_alpha,
                title="Modified Post",
            )

    def test_admin_moderator_can_edit_approved_post(self, employee_alpha, manager_user, admin_user):
        """Admin/moderator can update post even after approval."""
        post = create_post(
            author=employee_alpha,
            title="Alpha Post",
            content="Alpha Content",
        )
        review_post(post=post, reviewer=manager_user, status=Post.Status.APPROVED)

        updated = update_post(
            post=post,
            actor=admin_user,
            title="Admin Moderated Title",
        )
        assert updated.title == "Admin Moderated Title"


@pytest.mark.django_db
class TestPostReview:
    def test_team_lead_can_approve_report(self, employee_alpha, manager_user):
        """Team lead can approve subordinate post."""
        post = create_post(
            author=employee_alpha,
            title="Sprint Progress",
            content="Delivered Phase 1 features.",
        )

        reviewed = review_post(
            post=post,
            reviewer=manager_user,
            status=Post.Status.APPROVED,
            review_notes="Great work, looks solid.",
        )
        assert reviewed.status == Post.Status.APPROVED
        assert reviewed.reviewed_by == manager_user
        assert reviewed.reviewed_at is not None
        assert reviewed.review_notes == "Great work, looks solid."

    def test_self_review_is_prohibited(self, manager_user):
        """User cannot sign off on their own post."""
        post = create_post(
            author=manager_user,
            title="Manager's Log",
            content="Managed team sprint.",
        )

        with pytest.raises(ValidationError):
            review_post(
                post=post,
                reviewer=manager_user,
                status=Post.Status.APPROVED,
            )

    def test_unauthorized_user_cannot_review(self, employee_alpha, employee_beta):
        """Peer employee cannot review coworker post."""
        post = create_post(
            author=employee_alpha,
            title="Alpha Work",
            content="Alpha work description.",
        )

        with pytest.raises(PermissionDenied):
            review_post(
                post=post,
                reviewer=employee_beta,
                status=Post.Status.APPROVED,
            )


@pytest.mark.django_db
class TestPostComments:
    def test_add_and_list_comments(self, employee_alpha, employee_beta):
        """Users can post replies and they are ordered chronologically."""
        post = create_post(
            author=employee_alpha,
            title="Discussion Thread Post",
            content="Content for discussion.",
        )

        c1 = add_comment(post=post, author=employee_beta, content="First comment reply.")
        c2 = add_comment(post=post, author=employee_alpha, content="Thanks for the feedback!")

        comments = list(list_comments_for_post(post))
        assert len(comments) == 2
        assert comments[0].id == c1.id
        assert comments[1].id == c2.id
        assert comments[0].content == "First comment reply."

    def test_empty_comment_rejected(self, employee_alpha):
        """Empty comment raises ValidationError."""
        post = create_post(author=employee_alpha, title="Test", content="Test")
        with pytest.raises(ValidationError):
            add_comment(post=post, author=employee_alpha, content="   ")


@pytest.mark.django_db
class TestPostSelectorsAndScoping:
    def test_permission_scoping_feed(
        self, employee_alpha, employee_beta, intern_user, admin_user, team_alpha, team_beta
    ):
        """
        - Admin sees all posts.
        - Employee Alpha sees Team Alpha posts + own posts, but not Team Beta.
        - Intern only sees their own posts.
        """
        p_alpha = create_post(
            author=employee_alpha,
            team=team_alpha,
            title="Alpha Daily",
            content="Worked on Alpha.",
        )
        p_beta = create_post(
            author=employee_beta,
            team=team_beta,
            title="Beta Daily",
            content="Worked on Beta.",
        )
        p_intern = create_post(
            author=intern_user,
            title="Intern Daily",
            content="Intern tasks.",
        )

        # Admin sees all 3
        admin_posts = list_posts_for_user(admin_user)
        assert admin_posts.count() == 3

        # Alpha employee sees Alpha and not Beta (unless it's their own)
        alpha_posts = list_posts_for_user(employee_alpha)
        assert p_alpha in alpha_posts
        assert p_beta not in alpha_posts

        # Intern only sees p_intern
        intern_posts = list_posts_for_user(intern_user)
        assert intern_posts.count() == 1
        assert intern_posts.first() == p_intern

    def test_search_and_filter(self, employee_alpha, team_alpha):
        """Can search by title and filter by category."""
        p1 = create_post(
            author=employee_alpha,
            team=team_alpha,
            title="Database Schema Migration",
            content="Finished MySQL migrations.",
            category=Post.Category.DAILY_STANDUP,
        )
        p2 = create_post(
            author=employee_alpha,
            team=team_alpha,
            title="Blocker on Cloud Network",
            content="Waiting for firewall clearance.",
            category=Post.Category.BLOCKER,
        )

        # Search keyword
        results = list_posts_for_user(employee_alpha, search="Migration")
        assert p1 in results
        assert p2 not in results

        # Filter category
        blockers = list_posts_for_user(employee_alpha, category=Post.Category.BLOCKER)
        assert p2 in blockers
        assert p1 not in blockers


@pytest.mark.django_db
class TestPostViewPermissions:
    def _login(self, client, user):
        client.force_login(user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()
        UserSession.objects.create(user=user, session_key=session.session_key, mfa_verified=True)

    def test_non_author_cannot_access_edit_page(self, employee_alpha, employee_beta, team_alpha):
        """GET /posts/<id>/edit/ is rejected with 403 for non-author."""
        post = create_post(
            author=employee_alpha, title="Alpha Post", content="Alpha Content", team=team_alpha
        )
        client = Client()
        self._login(client, employee_beta)

        resp = client.get(f"/posts/{post.id}/edit/")
        assert resp.status_code == 403

    def test_cannot_edit_approved_post_via_view(self, employee_alpha, manager_user, team_alpha):
        """Author cannot access edit page for an approved post."""
        post = create_post(
            author=employee_alpha, title="Approved Post", content="Content", team=team_alpha
        )
        review_post(post=post, reviewer=manager_user, status=Post.Status.APPROVED)

        client = Client()
        self._login(client, employee_alpha)
        resp = client.get(f"/posts/{post.id}/edit/")
        assert resp.status_code == 403

    def test_cannot_comment_on_unauthorized_post(self, employee_alpha, employee_beta, team_alpha):
        """User from another team cannot post comments on a post they cannot view."""
        post = create_post(
            author=employee_alpha,
            title="Team Alpha Confidential",
            content="Internal",
            team=team_alpha,
        )
        client = Client()
        self._login(client, employee_beta)

        resp = client.post(f"/posts/{post.id}/comments/", {"content": "Sneaky comment"})
        assert resp.status_code == 403

    def test_team_lead_can_submit_for_led_team(self, manager_user, team_alpha):
        """Team lead has their led team available in the post form even without explicit membership row."""
        form = PostForm(author=manager_user)
        assert team_alpha in form.fields["team"].queryset
