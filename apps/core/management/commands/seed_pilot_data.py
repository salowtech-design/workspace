import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import CustomUser, TOTPCredential
from apps.attendance.models import WorkSession
from apps.certificates.models import Certificate, CertificateTemplate
from apps.certificates.services import issue_certificate, revoke_certificate
from apps.directory.models import Department, ReportingRelationship, Team, TeamMembership
from apps.posts.models import Post, PostTemplate
from apps.posts.services import add_comment, create_post, review_post


class Command(BaseCommand):
    help = "Seeds comprehensive, realistic pilot company dataset for Salow Workspace."

    def add_arguments(self, parser):
        parser.add_argument(
            "--default-password",
            type=str,
            default=os.getenv("PILOT_DEFAULT_PASSWORD", "SalowPilot2026!Secure"),
            help="Default initial password for seeded pilot accounts.",
        )

    def _seed_org(self, default_pwd):
        self.stdout.write("Seeding pilot organizational hierarchy...")

        dept_eng, _ = Department.objects.get_or_create(
            code="ENG", defaults={"name": "Engineering & Technology"}
        )
        dept_prd, _ = Department.objects.get_or_create(
            code="PRD", defaults={"name": "Product Design & UX"}
        )
        Department.objects.get_or_create(code="OPS", defaults={"name": "Operations & SRE"})
        Department.objects.get_or_create(code="PPL", defaults={"name": "People Operations & HR"})

        admin, _ = CustomUser.objects.get_or_create(
            employee_id="ST-ADM-0001",
            defaults={
                "full_name": "Chief Information Officer",
                "email": "cio@salowtech.internal",
                "role": CustomUser.Role.SUPER_ADMIN,
                "status": CustomUser.Status.ACTIVE,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if not admin.has_usable_password():
            admin.set_password(default_pwd)
            admin.save()
        TOTPCredential.objects.get_or_create(
            user=admin,
            defaults={
                "encrypted_secret": b"pilot_totp_key_32bytes_sample12",
                "confirmed_at": timezone.now(),
            },
        )

        manager, _ = CustomUser.objects.get_or_create(
            employee_id="ST-MGR-1001",
            defaults={
                "full_name": "Priya Sharma",
                "email": "priya.sharma@salowtech.internal",
                "role": CustomUser.Role.MANAGER,
                "status": CustomUser.Status.ACTIVE,
            },
        )
        if not manager.has_usable_password():
            manager.set_password(default_pwd)
            manager.save()

        eng1, _ = CustomUser.objects.get_or_create(
            employee_id="ST-EMP-2001",
            defaults={
                "full_name": "Arjun Patel",
                "email": "arjun.patel@salowtech.internal",
                "role": CustomUser.Role.EMPLOYEE,
                "status": CustomUser.Status.ACTIVE,
            },
        )
        if not eng1.has_usable_password():
            eng1.set_password(default_pwd)
            eng1.save()

        eng2, _ = CustomUser.objects.get_or_create(
            employee_id="ST-EMP-2002",
            defaults={
                "full_name": "Neha Reddy",
                "email": "neha.reddy@salowtech.internal",
                "role": CustomUser.Role.EMPLOYEE,
                "status": CustomUser.Status.ACTIVE,
            },
        )
        if not eng2.has_usable_password():
            eng2.set_password(default_pwd)
            eng2.save()

        intern, _ = CustomUser.objects.get_or_create(
            employee_id="ST-INT-3001",
            defaults={
                "full_name": "Kavya Iyer",
                "email": "kavya.iyer@salowtech.internal",
                "role": CustomUser.Role.INTERN,
                "status": CustomUser.Status.ACTIVE,
            },
        )
        if not intern.has_usable_password():
            intern.set_password(default_pwd)
            intern.save()

        team_core, _ = Team.objects.get_or_create(
            code="CORE",
            defaults={
                "name": "Core Monolith & Data Engine",
                "department": dept_eng,
                "lead": manager,
            },
        )
        team_front, _ = Team.objects.get_or_create(
            code="FRONT",
            defaults={"name": "Web Platform & UI Systems", "department": dept_prd},
        )

        TeamMembership.objects.get_or_create(team=team_core, user=manager)
        TeamMembership.objects.get_or_create(team=team_core, user=eng1)
        TeamMembership.objects.get_or_create(team=team_core, user=intern)
        TeamMembership.objects.get_or_create(team=team_front, user=eng2)

        ReportingRelationship.objects.get_or_create(manager=manager, subordinate=eng1)
        ReportingRelationship.objects.get_or_create(manager=manager, subordinate=intern)

        return admin, manager, eng1, eng2, intern, team_core, team_front

    def _seed_attendance(self, eng1):
        self.stdout.write("Seeding attendance records...")
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        now = timezone.now()

        WorkSession.objects.get_or_create(
            user=eng1,
            work_date=yesterday,
            defaults={
                "check_in_at": now - timedelta(days=1, hours=8),
                "check_out_at": now - timedelta(days=1),
                "verification_method": "dev_stub",
                "state": WorkSession.State.COMPLETED,
            },
        )
        WorkSession.objects.get_or_create(
            user=eng1,
            work_date=today,
            defaults={
                "check_in_at": now - timedelta(hours=3),
                "verification_method": "dev_stub",
                "state": WorkSession.State.OPEN,
            },
        )

    def _seed_posts(self, eng1, eng2, manager, team_core, team_front):
        self.stdout.write("Seeding post templates & work logs...")
        PostTemplate.objects.get_or_create(
            name="Daily Standup 3-Point",
            defaults={
                "category": Post.Category.DAILY_STANDUP,
                "template_text": "1. What did I achieve yesterday?\n2. What will I accomplish today?\n3. Any impediments or blockers?",
            },
        )

        today = timezone.localdate()
        post1 = Post.objects.filter(
            author=eng1, title="Distributed Transaction Verification"
        ).first()
        if not post1:
            post1 = create_post(
                author=eng1,
                team=team_core,
                title="Distributed Transaction Verification",
                content="1. Validated TiKV transactional rollback behavior.\n2. Implemented Crockford Base32 lock counter isolation.\n3. All test assertions green.",
                category=Post.Category.DAILY_STANDUP,
                work_date=today,
            )
            review_post(
                post=post1,
                reviewer=manager,
                status=Post.Status.APPROVED,
                review_notes="Excellent progress on database isolation.",
            )
            add_comment(
                post=post1,
                author=manager,
                content="Great work, Arjun! Let's ensure the pen-testing suite runs cleanly against it.",
            )

        post2 = Post.objects.filter(
            author=eng2, title="Responsive Design System Token Audit"
        ).first()
        if not post2:
            post2 = create_post(
                author=eng2,
                team=team_front,
                title="Responsive Design System Token Audit",
                content="Audit of #AD8B73 sandstone and #FFFBE9 cream color contrast compliance across mobile viewports.",
                category=Post.Category.MILESTONE,
                work_date=today,
            )
            add_comment(
                post=post2,
                author=eng1,
                content="Contrast ratios look well above 4.5:1 for WCAG AA compliance.",
            )

    def _seed_certificates(self, admin, intern, eng1, eng2):
        self.stdout.write("Seeding certificate templates & credentials...")
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)

        cert_tpl, _ = CertificateTemplate.objects.get_or_create(
            code="INTERNSHIP_COMPLETION",
            defaults={
                "title": "Certificate of Internship Completion",
                "description_template": "For successful completion of the Systems Engineering Internship Program.",
            },
        )
        cert_tpl2, _ = CertificateTemplate.objects.get_or_create(
            code="ENGINEERING_EXCELLENCE",
            defaults={
                "title": "Engineering Excellence Recognition",
                "description_template": "In recognition of outstanding technical contribution and architectural design.",
            },
        )

        if not Certificate.objects.filter(recipient=intern).exists():
            issue_certificate(
                recipient=intern,
                title="Certificate of Internship Completion",
                description="Successfully completed the 6-month intensive backend systems engineering internship with distinction.",
                issued_by=admin,
                template=cert_tpl,
                recipient_role="Systems Engineering Intern",
                issue_date=today,
            )

        if not Certificate.objects.filter(recipient=eng1).exists():
            issue_certificate(
                recipient=eng1,
                title="Engineering Excellence Recognition",
                description="Awarded for exemplary design and security hardening of the Salow Workspace platform.",
                issued_by=admin,
                template=cert_tpl2,
                recipient_role="Senior Software Engineer",
                issue_date=today,
            )

        if not Certificate.objects.filter(recipient=eng2, is_revoked=True).exists():
            c_revoked = issue_certificate(
                recipient=eng2,
                title="Provisional Access Credential",
                description="Initial test credential superseded by permanent role authorization.",
                issued_by=admin,
                issue_date=yesterday,
            )
            revoke_certificate(
                certificate=c_revoked,
                revoked_by=admin,
                reason="Administrative rollover to permanent credentials.",
            )

    def handle(self, *args, **options):
        default_pwd = options["default_password"]

        with transaction.atomic():
            admin, manager, eng1, eng2, intern, team_core, team_front = self._seed_org(default_pwd)
            self._seed_attendance(eng1)
            self._seed_posts(eng1, eng2, manager, team_core, team_front)
            self._seed_certificates(admin, intern, eng1, eng2)

        self.stdout.write(
            self.style.SUCCESS(
                "Successfully seeded pilot dataset: 4 departments, 2 teams, 5 users, "
                "attendance sessions, work logs with reviews/comments, and cryptographic certificates."
            )
        )
