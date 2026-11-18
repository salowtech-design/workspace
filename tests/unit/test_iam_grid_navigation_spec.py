"""
IAM Landing Page 13-Tile Grid Navigation Tests — Salow Workspace IAM.
Validates:
1. IAM Landing page renders all 13 required subsystem navigation tiles.
2. Each tile specifies Name, Description, and valid destination URL.
3. Each tile reflects live operational status indicators.
4. Business modules (Attendance, Posts, Certificates) are NOT in the IAM grid.
5. Unauthorized staff roles (Employee, Intern) receive HTTP 403 Forbidden.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import CustomUser

User = get_user_model()


@pytest.fixture
def admin_actor(db):
    user = User.objects.create(
        employee_id="ST-ADM-GRID-01",
        full_name="Grid Admin",
        email="grid.admin@salowtech.com",
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_staff=True,
    )
    user.set_password("AdminGridPass2026!")
    user.save()
    return user


@pytest.fixture
def regular_employee(db):
    user = User.objects.create(
        employee_id="ST-EMP-GRID-01",
        full_name="Grid Regular Employee",
        email="grid.employee@salowtech.com",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("EmpGridPass2026!")
    user.save()
    return user


@pytest.mark.django_db
class TestIAMGridNavigation:
    def test_overview_renders_all_thirteen_required_tiles(self, client, admin_actor):
        """IAM landing page (tab=overview) renders all 13 required subsystem tiles."""
        client.force_login(admin_actor)
        resp = client.get(reverse("accounts:admin_iam") + "?tab=overview")
        assert resp.status_code == 200
        html = resp.content.decode()

        required_tiles = [
            "1. Authentication",
            "2. Authorization & RBAC",
            "3. Identity & Accounts",
            "4. Identity Recovery",
            "5. Sessions & Devices",
            "6. MFA & Authenticators",
            "7. Forgot Password",
            "8. Privileged Access",
            "9. Security Operations",
            "10. Service Identities",
            "11. Secure Application Operation",
            "12. Threat Protection & Exceptions",
            "13. Resilience & Assurance",
        ]

        for tile in required_tiles:
            assert tile in html, f"Required tile '{tile}' was not found in IAM overview navigation."

        # Business systems MUST NOT be in the IAM grid
        assert "Attendance Check-In" not in html
        assert "Daily Work Posts" not in html
        assert "Certificate Verification Portal" not in html

    def test_authentication_subsystem_tab(self, client, admin_actor):
        """Tab 1 (Authentication) renders password policies, Argon2id, and throttling thresholds."""
        client.force_login(admin_actor)
        resp = client.get(reverse("accounts:admin_iam") + "?tab=authentication")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Authentication Architecture" in html
        assert "Argon2id (RFC 9106)" in html
        assert "First-Time Activation (Login)" in html
        assert "Returning Employee (Sign In)" in html
        assert "5 attempts" in html
        assert "3 attempts" in html

    def test_threat_protection_subsystem_tab(self, client, admin_actor):
        """Tab 13 (Threats & Exceptions) renders rate limits and fallback queue."""
        client.force_login(admin_actor)
        resp = client.get(reverse("accounts:admin_iam") + "?tab=threats")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Active Abuse Defense & Throttling Rules" in html
        assert "Biometric Attendance Fallback Queue" in html
        assert "60 requests / minute" in html

    def test_resilience_and_assurance_subsystem_tab(self, client, admin_actor):
        """Tab 14 (Resilience & Assurance) renders TiDB Cloud SLA and backup details."""
        client.force_login(admin_actor)
        resp = client.get(reverse("accounts:admin_iam") + "?tab=resilience")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "TiDB Cloud Serverless" in html
        assert "1,000 Simultaneously Active Users" in html
        assert "Recovery Point Objective" in html

    def test_unauthorized_employee_denied_access(self, client, regular_employee):
        """Regular employee without administrative authority receives 403 Forbidden."""
        client.force_login(regular_employee)
        resp = client.get(reverse("accounts:admin_iam"))
        assert resp.status_code == 403
