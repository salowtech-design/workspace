import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import CustomUser, TOTPCredential, UserSession
from apps.certificates.services import issue_certificate
from apps.core.middleware import RateLimitMiddleware
from apps.directory.models import Department, Team, TeamMembership
from apps.posts.services import add_comment, create_post


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Ensure clean rate limiter state before each security test."""
    RateLimitMiddleware.reset()
    yield
    RateLimitMiddleware.reset()


@pytest.fixture
def department(db):
    return Department.objects.create(code="SEC", name="Security Ops")


@pytest.fixture
def team(db, department):
    return Team.objects.create(code="SECTM", name="Sec Team", department=department)


@pytest.fixture
def test_employee(db, team):
    user = CustomUser.objects.create_user(
        employee_id="ST-EMP-8001",
        full_name="Pen Tester Alice",
        password="ValidStrongPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    TeamMembership.objects.create(team=team, user=user)
    return user


@pytest.fixture
def test_admin(db):
    admin = CustomUser.objects.create_superuser(
        employee_id="ST-ADM-0001",
        full_name="Admin Security",
        password="SuperMasterAdminPassword2026!",
    )
    TOTPCredential.objects.create(
        user=admin,
        encrypted_secret=b"x" * 32,
        confirmed_at=timezone.now(),
    )
    return admin


def _create_authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session["_salow_mfa_verified"] = True
    session.save()
    UserSession.objects.create(
        user=user,
        session_key=session.session_key,
        mfa_verified=True,
    )
    return client


@pytest.fixture
def authenticated_employee_client(test_employee):
    return _create_authenticated_client(test_employee)


@pytest.fixture
def authenticated_admin_client(test_admin):
    return _create_authenticated_client(test_admin)


@pytest.mark.django_db
class TestSQLInjectionResistance:
    SQLI_PAYLOADS = [
        "' OR '1'='1",
        "'; DROP TABLE apps_accounts_customuser; --",
        "' UNION SELECT null, null, null, null, null --",
        "1' OR '1' = '1' /*",
        "admin'--",
        "1; WAITFOR DELAY '0:0:5'--",
    ]

    def test_search_posts_sqli_payloads_neutralized(self, authenticated_employee_client):
        """Search query parameters must not execute arbitrary SQL or crash the database."""
        for payload in self.SQLI_PAYLOADS:
            response = authenticated_employee_client.get(f"/posts/?q={payload}")
            # Must return 200 OK without 500 error or SQL crash
            assert response.status_code == 200

    def test_public_verify_sqli_payloads_neutralized(self):
        """Public verification endpoint must safely handle SQL injection probes."""
        client = Client()
        for payload in self.SQLI_PAYLOADS:
            response = client.get(f"/verify/?id={payload}")
            assert response.status_code == 200
            assert "No Credential Found" in response.content.decode("utf-8")

    def test_auth_login_sqli_neutralized(self):
        """Login form employee_id input safely neutralizes SQL injection attempts."""
        client = Client()
        for payload in self.SQLI_PAYLOADS:
            response = client.post("/auth/login/", {"employee_id": payload, "password": "password"})
            # Must handle gracefully with form error or 200, never 500
            assert response.status_code in (200, 302)


@pytest.mark.django_db
class TestXSSMitigation:
    XSS_PAYLOADS = [
        "<script>alert('xss-1')</script>",
        "<img src=x onerror=alert('xss-2')>",
        "<svg/onload=alert('xss-3')>",
        "javascript:alert('xss-4')",
        "\"><script>alert('xss-5')</script>",
    ]

    def test_post_content_and_title_xss_escaped(
        self, authenticated_employee_client, test_employee, team
    ):
        """Malicious HTML and script tags in posts are HTML-escaped by template engine."""
        for payload in self.XSS_PAYLOADS:
            post = create_post(
                author=test_employee,
                title=f"Title {payload}",
                content=f"Content {payload}",
                team=team,
            )

            # View feed
            feed_resp = authenticated_employee_client.get("/posts/")
            content_str = feed_resp.content.decode("utf-8")
            assert "<script>" not in content_str
            assert "<svg/onload" not in content_str

            # View detail
            detail_resp = authenticated_employee_client.get(f"/posts/{post.id}/")
            detail_str = detail_resp.content.decode("utf-8")
            assert "<script>" not in detail_str
            assert "<svg/onload" not in detail_str

    def test_post_comment_xss_escaped(self, authenticated_employee_client, test_employee, team):
        """Comments with XSS vectors are HTML-escaped."""
        post = create_post(
            author=test_employee,
            title="Clean Post",
            content="Clean Content",
            team=team,
        )
        add_comment(post=post, author=test_employee, content="<script>alert('hack')</script>")

        resp = authenticated_employee_client.get(f"/posts/{post.id}/")
        rendered = resp.content.decode("utf-8")
        assert "<script>alert('hack')</script>" not in rendered
        assert (
            "&lt;script&gt;alert(&#x27;hack&#x27;)&lt;/script&gt;" in rendered
            or "&lt;script&gt;" in rendered
        )


@pytest.mark.django_db
class TestCSRFProtection:
    def test_post_mutations_require_csrf_token(self, test_employee):
        """CSRF-enforced client rejects state-changing requests lacking valid token."""
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(test_employee)
        session = csrf_client.session
        session["_salow_mfa_verified"] = True
        session.save()
        UserSession.objects.create(
            user=test_employee,
            session_key=session.session_key,
            mfa_verified=True,
        )

        # Attempt POST without CSRF token
        response = csrf_client.post("/posts/create/", {"title": "Test", "content": "Body"})
        assert response.status_code == 403


@pytest.mark.django_db
class TestRateLimitingDefense:
    def test_rate_limiter_blocks_excessive_requests(self):
        """RateLimitMiddleware blocks rapid brute-force bursts with HTTP 429."""
        client = Client()
        endpoint = "/verify/?id=ST-TEST-PROBE"

        # Fire 60 requests (allowed threshold)
        for _ in range(RateLimitMiddleware.SENSITIVE_LIMIT):
            resp = client.get(endpoint)
            assert resp.status_code == 200

        # 61st request must trigger 429 Too Many Requests
        blocked_resp = client.get(endpoint)
        assert blocked_resp.status_code == 429
        assert blocked_resp.headers.get("Retry-After") == "60"
        assert "Too Many Requests" in blocked_resp.content.decode("utf-8")


@pytest.mark.django_db
class TestSecurityHeaders:
    def test_mandatory_security_headers_present(self):
        """Every response must deliver strict defense-in-depth security headers."""
        client = Client()
        response = client.get("/health")

        assert response.status_code == 200
        assert "Content-Security-Policy" in response.headers
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "Permissions-Policy" in response.headers


@pytest.mark.django_db
class TestPrivilegeBoundaryAndDataExposure:
    def test_unauthenticated_user_cannot_access_internal_routes(self):
        """Unauthenticated requests to internal routes are redirected to login."""
        client = Client()
        protected_routes = ["/posts/", "/certificates/", "/attendance/", "/directory/"]
        for route in protected_routes:
            resp = client.get(route)
            assert resp.status_code == 302
            assert "/auth/login" in resp.headers.get("Location")

    def test_employee_cannot_issue_or_revoke_certificates(
        self, authenticated_employee_client, test_employee, test_admin
    ):
        """Standard employee cannot execute administrative certificate actions."""
        # Issue attempt
        resp_issue = authenticated_employee_client.post(
            "/certificates/issue/",
            {"recipient": test_employee.id, "title": "Self Award", "description": "Hacked"},
        )
        assert resp_issue.status_code == 403

        # Create valid cert via admin service
        cert = issue_certificate(
            recipient=test_employee,
            title="Real Cert",
            description="Real Desc",
            issued_by=test_admin,
        )

        # Revoke attempt by employee
        resp_revoke = authenticated_employee_client.post(
            f"/certificates/{cert.uuid}/revoke/",
            {"reason": "Malicious revocation attempt"},
        )
        assert resp_revoke.status_code == 403

    def test_public_verify_does_not_leak_sensitive_credentials(self, test_employee, test_admin):
        """Public verification portal never exposes passwords, secrets, or internal keys."""
        cert = issue_certificate(
            recipient=test_employee,
            title="Public Verification Security Audit",
            description="Testing information leakage.",
            issued_by=test_admin,
        )

        client = Client()
        resp = client.get(f"/verify/?id={cert.serial_number}")
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")

        # Must never leak sensitive keys or internal hashes
        assert "argon2" not in content.lower()
        assert "password" not in content.lower()
        assert "totp" not in content.lower()
        assert "embedding" not in content.lower()
        assert "secret" not in content.lower()

    def test_public_verify_tampered_fails_closed(self, test_employee, test_admin):
        """Tampered certificates fail verification and render tamper security warnings."""
        cert = issue_certificate(
            recipient=test_employee,
            title="Integrity Pen Test Award",
            description="Testing tamper detection.",
            issued_by=test_admin,
        )
        # Corrupt the verification hash
        cert.verification_hash = "deadbeef" * 8
        cert.save()

        client = Client()
        resp = client.get(f"/verify/?id={cert.serial_number}")
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "INTEGRITY VIOLATION / CRYPTOGRAPHIC TAMPERING DETECTED" in content

    def test_rate_limiter_evicts_stale_keys_preventing_memory_leak(self):
        """RateLimitMiddleware evicts stale entries under high key volume."""
        RateLimitMiddleware.reset()
        client = Client()

        # Artificially populate storage past the cleanup threshold with expired timestamps
        cutoff = timezone.now().timestamp() - 100
        for i in range(505):
            RateLimitMiddleware._storage[f"10.0.{i // 256}.{i % 256}:general"] = [cutoff]

        # Issue a new request to trigger eviction
        client.get("/health")

        # Must have evicted the stale keys down below 500
        assert len(RateLimitMiddleware._storage) <= 500
