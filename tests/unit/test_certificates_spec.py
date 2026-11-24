from datetime import date, timedelta

import defusedxml.ElementTree as ET
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory

from apps.accounts.models import CustomUser, UserSession
from apps.certificates.models import CertificateTemplate, CertificateVerificationLog
from apps.certificates.services import (
    generate_certificate_svg,
    issue_certificate,
    revoke_certificate,
    verify_certificate,
)
from apps.certificates.views import public_verify_view
from apps.core.models import AuditLog, OutboxEvent


@pytest.fixture
def recipient_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-INT-7001",
        full_name="Grace Hopper",
        password="ValidPassword123!",
        role=CustomUser.Role.INTERN,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def other_user(db):
    return CustomUser.objects.create_user(
        employee_id="ST-EMP-7002",
        full_name="Ada Lovelace",
        password="ValidPassword123!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )


@pytest.fixture
def admin_user(db):
    return CustomUser.objects.create_superuser(
        employee_id="ST-ADM-0001",
        full_name="System Admin",
        password="SuperMasterAdminPassword2026!",
    )


@pytest.fixture
def cert_template(db):
    return CertificateTemplate.objects.create(
        code="INTERNSHIP_COMPLETION",
        title="Certificate of Internship Completion",
        description_template="Successfully completed systems engineering internship.",
    )


@pytest.mark.django_db
class TestCertificateIssuance:
    def test_admin_can_issue_certificate(self, recipient_user, admin_user, cert_template):
        """Administrator can issue certificate; serial, hash, audit, and outbox created."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Internship Excellence Award",
            description="Awarded for exemplary contributions to distributed transactional storage.",
            issued_by=admin_user,
            template=cert_template,
            recipient_role="Backend Engineering Intern",
            issue_date=date(2026, 11, 16),
        )

        assert cert.id is not None
        assert cert.serial_number.startswith("ST-CERT-2026-")
        assert len(cert.verification_hash) == 64
        assert cert.recipient == recipient_user
        assert cert.recipient_name == "Grace Hopper"
        assert not cert.is_revoked

        # Check Audit Log
        audit = AuditLog.objects.filter(
            target_type="certificate", target_id=cert.serial_number
        ).first()
        assert audit is not None
        assert audit.action == "certificate_issued"
        assert audit.actor == admin_user

        # Check Outbox Event
        outbox = OutboxEvent.objects.filter(event_type="certificate.issued").first()
        assert outbox is not None
        assert outbox.payload["serial_number"] == cert.serial_number

    def test_non_admin_cannot_issue_certificate(self, recipient_user, other_user):
        """Standard employee cannot issue credentials."""
        with pytest.raises(PermissionDenied):
            issue_certificate(
                recipient=recipient_user,
                title="Unauthorized Certificate",
                description="Hacked credential.",
                issued_by=other_user,
            )

    def test_empty_title_or_description_rejected(self, recipient_user, admin_user):
        """Empty title or description raises ValidationError."""
        with pytest.raises(ValidationError):
            issue_certificate(
                recipient=recipient_user,
                title="   ",
                description="Valid description.",
                issued_by=admin_user,
            )
        with pytest.raises(ValidationError):
            issue_certificate(
                recipient=recipient_user,
                title="Valid Title",
                description="   ",
                issued_by=admin_user,
            )


@pytest.mark.django_db
class TestCertificateRevocation:
    def test_admin_can_revoke_with_reason(self, recipient_user, admin_user):
        """Admin can revoke credential; reason is captured in record and audit log."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Temporary Certification",
            description="Testing revocation workflow.",
            issued_by=admin_user,
        )

        revoked = revoke_certificate(
            certificate=cert,
            revoked_by=admin_user,
            reason="Administrative error during credential issuance.",
        )
        assert revoked.is_revoked is True
        assert revoked.revoked_at is not None
        assert revoked.revoked_by == admin_user
        assert revoked.revocation_reason == "Administrative error during credential issuance."

        # Audit Log
        audit = AuditLog.objects.filter(
            target_type="certificate", target_id=cert.serial_number, action="certificate_revoked"
        ).first()
        assert audit is not None

    def test_cannot_revoke_already_revoked_certificate(self, recipient_user, admin_user):
        """Cannot revoke twice."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Cert",
            description="Desc",
            issued_by=admin_user,
        )
        revoke_certificate(certificate=cert, revoked_by=admin_user, reason="First revocation.")

        with pytest.raises(ValidationError):
            revoke_certificate(certificate=cert, revoked_by=admin_user, reason="Second attempt.")

    def test_empty_revocation_reason_rejected(self, recipient_user, admin_user):
        """Revocation without a reason is rejected."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Cert",
            description="Desc",
            issued_by=admin_user,
        )
        with pytest.raises(ValidationError):
            revoke_certificate(certificate=cert, revoked_by=admin_user, reason="   ")


@pytest.mark.django_db
class TestCertificateVerification:
    def test_verify_by_serial_uuid_and_hash(self, recipient_user, admin_user):
        """Can verify valid certificate by serial, UUID, and verification hash."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Verifiable Certificate",
            description="Valid award.",
            issued_by=admin_user,
        )

        # 1. By serial
        c1, s1 = verify_certificate(cert.serial_number, ip_address="192.168.1.100")
        assert c1.id == cert.id
        assert s1 == "valid"

        # 2. By UUID
        c2, s2 = verify_certificate(str(cert.uuid))
        assert c2.id == cert.id
        assert s2 == "valid"

        # 3. By Hash
        c3, s3 = verify_certificate(cert.verification_hash)
        assert c3.id == cert.id
        assert s3 == "valid"

        # Check Verification Logs were recorded
        assert CertificateVerificationLog.objects.filter(certificate=cert).count() >= 3

    def test_verify_revoked_certificate(self, recipient_user, admin_user):
        """Revoked certificate returns revoked status."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Revoked Cert",
            description="Desc",
            issued_by=admin_user,
        )
        revoke_certificate(certificate=cert, revoked_by=admin_user, reason="Cancelled.")

        c, status = verify_certificate(cert.serial_number)
        assert c.id == cert.id
        assert status == "revoked"

    def test_verify_expired_certificate(self, recipient_user, admin_user):
        """Expired certificate returns expired status."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Expired Cert",
            description="Desc",
            issued_by=admin_user,
            expiry_date=date.today() - timedelta(days=1),
        )

        c, status = verify_certificate(cert.serial_number)
        assert c.id == cert.id
        assert status == "expired"

    def test_verify_not_found(self):
        """Non-existent identifier returns not_found."""
        c, status = verify_certificate("ST-CERT-9999-DOESNOTEXIST")
        assert c is None
        assert status == "not_found"

    def test_tampered_hash_detected(self, recipient_user, admin_user):
        """Tampered cryptographic hash fails verification."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Legit Cert",
            description="Desc",
            issued_by=admin_user,
        )
        # Manually tamper with the hash in the database
        cert.verification_hash = "0" * 64
        cert.save()

        c, status = verify_certificate(cert.serial_number)
        assert c.id == cert.id
        assert status == "tampered"

    def test_tampered_title_or_recipient_detected(self, recipient_user, admin_user):
        """Modifying title or recipient name invalidates HMAC-SHA256 hash."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Legit Internship Award",
            description="Original description",
            issued_by=admin_user,
        )
        # Verify initially authentic
        c, status = verify_certificate(cert.serial_number)
        assert status == "valid"

        # Tamper title
        cert.title = "Forged Executive Award"
        cert.save()
        c2, status2 = verify_certificate(cert.serial_number)
        assert status2 == "tampered"

        # Restore title, tamper recipient_name
        cert.title = "Legit Internship Award"
        cert.recipient_name = "Mallory Hacker"
        cert.save()
        c3, status3 = verify_certificate(cert.serial_number)
        assert status3 == "tampered"


@pytest.mark.django_db
class TestCertificateSVG:
    def test_generate_svg_structure_and_valid_xml(self, recipient_user, admin_user):
        """Generated SVG contains proper XML, brand tokens, and parses cleanly as XML."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="SVG Test Certificate",
            description="Valid SVG rendering validation.",
            issued_by=admin_user,
        )

        svg = generate_certificate_svg(cert)
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "Grace Hopper" in svg
        assert cert.serial_number in svg
        assert "#AD8B73" in svg
        assert "#FFFBE9" in svg
        assert "REVOKED" not in svg

        # Deep Verification: Must parse as well-formed XML without error
        root = ET.fromstring(svg)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"

    def test_svg_handles_special_xml_characters(self, admin_user):
        """SVG generation cleanly XML-escapes characters like &, <, >, " without crashing parser."""
        tricky_user = CustomUser.objects.create_user(
            employee_id="ST-INT-7999",
            full_name="Alice & Bob <Engineers>",
            password="ValidPassword123!",
            role=CustomUser.Role.INTERN,
            status=CustomUser.Status.ACTIVE,
        )
        cert = issue_certificate(
            recipient=tricky_user,
            title='Excellence in "Full-Stack" & Systems <2026>',
            description="Handled tricky payloads: A & B > C < D \"E\" 'F'",
            issued_by=admin_user,
            recipient_role="R&D <Lead>",
        )

        svg = generate_certificate_svg(cert)
        # Must parse cleanly with standard XML parser
        root = ET.fromstring(svg)
        assert root is not None
        assert "&amp;" in svg
        assert "&lt;" in svg
        assert "&gt;" in svg

    def test_revoked_svg_contains_watermark(self, recipient_user, admin_user):
        """Revoked certificate SVG has REVOKED watermark overlay and remains valid XML."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Revoked SVG Cert",
            description="Desc",
            issued_by=admin_user,
        )
        revoke_certificate(certificate=cert, revoked_by=admin_user, reason="Revoked.")

        svg = generate_certificate_svg(cert)
        assert "REVOKED" in svg
        root = ET.fromstring(svg)
        assert root is not None


@pytest.mark.django_db
class TestPublicVerificationEndpoint:
    def test_public_verify_view_anonymous_access(self, recipient_user, admin_user):
        """Public verification endpoint is accessible without login and sets noindex."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Public Verification Test",
            description="Testing public verification.",
            issued_by=admin_user,
        )

        factory = RequestFactory()
        request = factory.get(f"/verify/?id={cert.serial_number}")
        # Note: Anonymous request (no session/user set)
        response = public_verify_view(request)

        assert response.status_code == 200
        assert response.headers.get("X-Robots-Tag") == "noindex, nofollow"
        content = response.content.decode("utf-8")
        assert "VERIFIED AUTHENTIC" in content
        assert "Grace Hopper" in content
        assert cert.serial_number in content

    def test_public_verify_revoked_view(self, recipient_user, admin_user):
        """Public verification displays revoked banner for revoked certificate."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Public Revoked Test",
            description="Testing revoked view.",
            issued_by=admin_user,
        )
        revoke_certificate(certificate=cert, revoked_by=admin_user, reason="Academic misconduct.")

        factory = RequestFactory()
        request = factory.get(f"/verify/?id={cert.serial_number}")
        response = public_verify_view(request)

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "CREDENTIAL REVOKED" in content
        assert "Academic misconduct." in content

    def test_public_verify_tampered_view(self, recipient_user, admin_user):
        """Public verification displays prominent tamper warning for altered records."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Tampered Verification Test",
            description="Testing tamper view.",
            issued_by=admin_user,
        )
        cert.verification_hash = "f" * 64
        cert.save()

        factory = RequestFactory()
        request = factory.get(f"/verify/?id={cert.serial_number}")
        response = public_verify_view(request)

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "INTEGRITY VIOLATION / CRYPTOGRAPHIC TAMPERING DETECTED" in content
        assert "Cryptographic Verification Failed" in content


@pytest.mark.django_db
class TestCertificateSVGViewPermissions:
    def test_anonymous_redirected_to_login(self, recipient_user, admin_user):
        """Unauthenticated request to certificate SVG is redirected to login."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Protected SVG",
            description="Protected",
            issued_by=admin_user,
        )
        client = Client()
        resp = client.get(f"/certificates/{cert.uuid}/svg/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("Location")

    def test_unauthorized_user_denied_access(self, recipient_user, other_user, admin_user):
        """Non-recipient and non-admin employee cannot access raw SVG asset."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="Protected SVG",
            description="Protected",
            issued_by=admin_user,
        )
        client = Client()
        client.force_login(other_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()
        UserSession.objects.create(
            user=other_user, session_key=session.session_key, mfa_verified=True
        )

        resp = client.get(f"/certificates/{cert.uuid}/svg/")
        assert resp.status_code == 403

    def test_recipient_can_download_svg(self, recipient_user, admin_user):
        """Certificate recipient can access their own raw SVG asset."""
        cert = issue_certificate(
            recipient=recipient_user,
            title="My Own SVG",
            description="Protected",
            issued_by=admin_user,
        )
        client = Client()
        client.force_login(recipient_user)
        session = client.session
        session["_salow_mfa_verified"] = True
        session.save()
        UserSession.objects.create(
            user=recipient_user, session_key=session.session_key, mfa_verified=True
        )

        resp = client.get(f"/certificates/{cert.uuid}/svg/")
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type") == "image/svg+xml"
        assert f'filename="{cert.serial_number}.svg"' in resp.headers.get("Content-Disposition")


class TestCertificateRevokeNeedsSudo:
    def _call(self, admin_user, cert, sudo):
        from django.contrib.messages.storage.fallback import FallbackStorage

        from apps.accounts.auth import stepup
        from apps.certificates.views import certificate_revoke_view

        rf = RequestFactory().post("/x/", {"reason": "Issued in error."})
        rf.user = admin_user
        rf.session = {}
        rf._messages = FallbackStorage(rf)
        if sudo:
            rf.session[stepup.SUDO_GRANTED_TIMESTAMP_KEY] = __import__("time").time()
        stepup._USER_SUDO_CACHE.pop(admin_user.id, None)
        return certificate_revoke_view(rf, cert.uuid)

    def _cert(self, recipient_user, admin_user):
        return issue_certificate(
            recipient=recipient_user,
            title="Sudo Cert",
            description="Revoke needs a fresh password re-entry.",
            issued_by=admin_user,
        )

    def test_revoke_without_sudo_is_redirected_and_changes_nothing(
        self, recipient_user, admin_user
    ):
        cert = self._cert(recipient_user, admin_user)
        resp = self._call(admin_user, cert, sudo=False)
        assert resp.status_code == 302 and "/security/sudo/" in resp.url
        cert.refresh_from_db()
        assert cert.is_revoked is False

    def test_revoke_with_sudo_and_reason_revokes(self, recipient_user, admin_user):
        cert = self._cert(recipient_user, admin_user)
        self._call(admin_user, cert, sudo=True)
        cert.refresh_from_db()
        assert cert.is_revoked is True and cert.revoked_by == admin_user


@pytest.mark.django_db
class TestCertificateLinkedReissue:
    def _revoked(self, recipient_user, admin_user):
        cert = issue_certificate(
            recipient=recipient_user, title="Original", description="Citation", issued_by=admin_user
        )
        return revoke_certificate(certificate=cert, revoked_by=admin_user, reason="Wrong name")

    def test_reissue_links_to_the_revoked_certificate_once(self, recipient_user, admin_user):
        old = self._revoked(recipient_user, admin_user)
        new = issue_certificate(
            recipient=recipient_user,
            title="Original",
            description="Citation",
            issued_by=admin_user,
            supersedes=old,
        )
        assert new.supersedes_id == old.id and old.reissued_as.id == new.id
        assert new.serial_number != old.serial_number and not new.is_revoked
        with pytest.raises(ValidationError):
            issue_certificate(
                recipient=recipient_user,
                title="Again",
                description="Citation",
                issued_by=admin_user,
                supersedes=old,
            )

    def test_cannot_supersede_a_live_certificate_or_another_recipients(
        self, recipient_user, admin_user
    ):
        live = issue_certificate(
            recipient=recipient_user, title="Live", description="Citation", issued_by=admin_user
        )
        with pytest.raises(ValidationError):
            issue_certificate(
                recipient=recipient_user,
                title="X",
                description="Citation",
                issued_by=admin_user,
                supersedes=live,
            )
        old = self._revoked(recipient_user, admin_user)
        other = CustomUser.objects.create_user(
            employee_id="ST-INT-7002",
            full_name="Other",
            password="ValidPassword123!",
            role=CustomUser.Role.INTERN,
            status=CustomUser.Status.ACTIVE,
        )
        with pytest.raises(ValidationError):
            issue_certificate(
                recipient=other,
                title="X",
                description="Citation",
                issued_by=admin_user,
                supersedes=old,
            )

    def _rf(self, method, path, user, data=None):
        from django.contrib.messages.storage.fallback import FallbackStorage

        rf = getattr(RequestFactory(), method)(path, data or {})
        rf.user = user
        rf.session = {}
        rf._messages = FallbackStorage(rf)
        return rf

    def test_issue_page_reissue_flow_links_and_view_shows_replacement(
        self, recipient_user, admin_user
    ):
        from apps.certificates.models import Certificate
        from apps.certificates.views import certificate_issue_view, certificate_view

        old = self._revoked(recipient_user, admin_user)
        page = certificate_issue_view(
            self._rf("get", f"/certificates/issue/?reissue={old.uuid}", admin_user)
        )
        assert page.status_code == 200 and b'name="supersedes"' in page.content
        resp = certificate_issue_view(
            self._rf(
                "post",
                "/certificates/issue/",
                admin_user,
                {
                    "supersedes": str(old.uuid),
                    "recipient": recipient_user.id,
                    "title": "Original",
                    "recipient_role": "Intern",
                    "description": "Citation",
                    "issue_date": "2026-11-24",
                },
            )
        )
        assert resp.status_code == 302
        new = Certificate.objects.get(supersedes=old)
        old_page = certificate_view(self._rf("get", "/x/", admin_user), old.uuid)
        assert new.serial_number.encode() in old_page.content
        before = Certificate.objects.count()
        bad = certificate_issue_view(
            self._rf(
                "post",
                "/certificates/issue/",
                admin_user,
                {"supersedes": str(new.uuid), "recipient": recipient_user.id, "title": "T"},
            )
        )
        assert bad.status_code == 302 and Certificate.objects.count() == before
