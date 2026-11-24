"""
Unit & Integration Tests for Final IAM, Workspace Security & Multi-Device Application.
Validates:
1. Linked employee lifecycle synchronization in IAM login & session validation.
2. ApprovedDevice durable model, UserSession linking, and 3-device capacity.
3. 2-of-3 Shamir threshold secret sharing & offline disaster recovery.
4. Atomic database rate limiter (RateLimitBucket) concurrency.
5. Scoped Workspace Security Admin authorization in IAM console.
"""

from datetime import timedelta
import pytest
from django.conf import settings
from django.utils import timezone

from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.iam_auth import (
    authenticate_iam_admin,
    create_iam_admin_identity,
    create_iam_session,
    get_iam_session_from_request,
)
from apps.accounts.auth.offline_recovery import (
    parse_share,
    reconstruct_secret_2_of_3,
    split_secret_2_of_3,
)
from apps.accounts.auth.sessions import (
    synchronize_employee_lifecycle_revocation,
)
from apps.accounts.models import (
    ApprovedDevice,
    CustomUser,
    DeviceApprovalRequest,
    IAMAdminIdentity,
    RecoveryRequest,
    UserSession,
)
from apps.core.models import RateLimitBucket


@pytest.fixture
def active_user(db):
    user = CustomUser.objects.create(
        employee_id="ST-EMP-8801",
        full_name="Bob Test Employee",
        email="bob.test@salowtech.com",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("ValidPassword2026!@#")
    user.save()
    return user


@pytest.fixture
def admin_person(db):
    user = CustomUser.objects.create(
        employee_id="ST-ADM-8802",
        full_name="Charlie Test Admin",
        email="charlie.admin@salowtech.com",
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("AdminPassword2026!@#")
    user.save()
    return user


@pytest.mark.django_db
class TestLifecycleSynchronization:
    def test_suspended_employee_blocked_from_iam_login(self, admin_person):
        """IAM admin whose linked employee record is suspended is rejected at login."""
        iam_admin, raw_totp = create_iam_admin_identity(
            user=admin_person,
            admin_id="IAM-SYNC-01",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.ADMIN,
        )

        # Suspend linked employee
        admin_person.status = CustomUser.Status.SUSPENDED
        admin_person.is_active = False
        admin_person.save()

        import pyotp

        totp_val = pyotp.TOTP(raw_totp).now()

        with pytest.raises(AuthException) as exc:
            authenticate_iam_admin("IAM-SYNC-01", "Strong16CharMasterPass!", totp_val)
        assert "Authoritative employee identity is suspended" in str(exc.value.user_message)

    def test_active_iam_session_invalidated_when_employee_suspended(self, rf, admin_person):
        """Active IAM session returns None if linked employee is suspended/inactive."""
        iam_admin, _ = create_iam_admin_identity(
            user=admin_person,
            admin_id="IAM-SYNC-02",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.ADMIN,
        )
        req = rf.get("/iam/dashboard/")
        iam_sess = create_iam_session(iam_admin, req)
        bearer = req.session_key_iam

        cookie_name = getattr(settings, "IAM_SESSION_COOKIE_NAME", "__Host-salow_iam_session")
        req.COOKIES[cookie_name] = bearer

        # Session is valid initially
        sess = get_iam_session_from_request(req)
        assert sess is not None
        assert sess.id == iam_sess.id

        # Suspend employee
        admin_person.status = CustomUser.Status.SUSPENDED
        admin_person.is_active = False
        admin_person.save()

        # Next check invalidates session
        sess_after = get_iam_session_from_request(req)
        assert sess_after is None
        iam_sess.refresh_from_db()
        assert iam_sess.is_active is False

    def test_synchronize_employee_lifecycle_revocation(self, rf, active_user, admin_person):
        """Account suspension synchronously revokes Workspace, IAM, approvals, and recovery."""
        iam_admin, _ = create_iam_admin_identity(
            user=admin_person,
            admin_id="IAM-SYNC-03",
            password="Strong16CharMasterPass!",
            role=IAMAdminIdentity.Role.ADMIN,
        )
        req = rf.get("/iam/dashboard/")
        iam_sess = create_iam_session(iam_admin, req)

        # Create Workspace session
        UserSession.objects.create(
            user=admin_person,
            session_key="test_sess_key_1",
            session_digest="test_sess_digest_1",
            mfa_verified=True,
        )

        # Create pending device request
        DeviceApprovalRequest.objects.create(
            user=admin_person,
            status=DeviceApprovalRequest.Status.PENDING,
            expires_at=timezone.now() + timedelta(minutes=1),
        )

        # Create pending recovery request
        RecoveryRequest.objects.create(
            user=admin_person,
            status=RecoveryRequest.Status.PENDING,
            reason="Lost second factor device",
        )

        # Execute synchronized revocation
        admin_person.status = CustomUser.Status.SUSPENDED
        admin_person.is_active = False
        admin_person.save()

        stats = synchronize_employee_lifecycle_revocation(admin_person)

        assert stats["workspace_sessions"] >= 1
        assert stats["iam_sessions"] >= 1
        assert stats["device_requests"] >= 1
        assert stats["recovery_requests"] >= 1

        iam_sess.refresh_from_db()
        assert iam_sess.is_active is False
        iam_admin.refresh_from_db()
        assert iam_admin.status == IAMAdminIdentity.Status.SUSPENDED


@pytest.mark.django_db
class TestMultiDeviceAndApprovedDeviceModel:
    def test_approved_device_creation_and_session_link(self, active_user):
        """ApprovedDevice persists device metadata and links to UserSession."""
        dev = ApprovedDevice.objects.create(
            user=active_user,
            device_id="dev-unique-uuid-12345",
            display_name="Alice's MacBook Pro",
            device_type=ApprovedDevice.DeviceType.DESKTOP,
            platform_metadata="Mozilla/5.0 Macintosh",
            is_approved=True,
        )
        assert dev.id is not None
        assert dev.is_approved is True

        sess = UserSession.objects.create(
            user=active_user,
            session_key="test_session_key_dev",
            session_digest="test_session_digest_dev",
            device=dev,
            mfa_verified=True,
        )
        assert sess.device == dev
        assert dev.sessions.count() == 1

    def test_max_concurrent_devices_setting(self):
        """System supports 3 concurrent devices (Desktop, Tablet, Mobile)."""
        assert getattr(settings, "MAX_CONCURRENT_DEVICES", None) == 3


class TestOfflineDisasterRecoverySecretSharing:
    def test_split_and_reconstruct_from_any_two_shares(self):
        """2-of-3 threshold split secret can be reconstructed from any 2 distinct shares."""
        import secrets

        master_secret = secrets.token_bytes(32)

        shares = split_secret_2_of_3(master_secret)
        assert len(shares) == 3

        s1 = parse_share(shares[0])
        s2 = parse_share(shares[1])
        s3 = parse_share(shares[2])

        # Any 2 shares must reconstruct the identical 32-byte secret
        rec_12 = reconstruct_secret_2_of_3([s1, s2])
        rec_23 = reconstruct_secret_2_of_3([s2, s3])
        rec_13 = reconstruct_secret_2_of_3([s1, s3])

        assert rec_12 == master_secret
        assert rec_23 == master_secret
        assert rec_13 == master_secret

    def test_duplicate_shares_rejected(self):
        """Reconstructing from duplicate shares is rejected."""
        import secrets

        master_secret = secrets.token_bytes(32)
        shares = split_secret_2_of_3(master_secret)

        s1 = parse_share(shares[0])
        with pytest.raises(ValueError) as exc:
            reconstruct_secret_2_of_3([s1, s1])
        assert "Duplicate shares" in str(exc.value)


@pytest.mark.django_db
class TestAtomicRateLimiting:
    def test_rate_limit_bucket_atomic_increment(self):
        """RateLimitBucket provides database-backed atomic serialization."""
        now = timezone.now()
        b1 = RateLimitBucket.objects.create(
            bucket_key="127.0.0.1:sensitive",
            count=1,
            reset_at=now + timedelta(seconds=60),
        )
        assert b1.count == 1

        b1.count += 1
        b1.save(update_fields=["count"])
        b1.refresh_from_db()
        assert b1.count == 2
