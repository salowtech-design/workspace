"""
Comprehensive Concurrency, Replay Defense, and Abuse Protection Tests:
1. Concurrent TOTP Replay Defense (multi-threaded verification of identical code)
2. Concurrent Recovery Code Redemption (multi-threaded atomic consumption)
3. Concurrent Activation / Reset Code Redemption (atomic single-use)
4. Untrusted Proxy Header & IP Spoofing Defense
5. Step-Up / Sudo Re-Authentication Lifecycle
"""

import concurrent.futures

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.auth.activation import (
    issue_activation_code,
    verify_and_consume_code,
)
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import (
    encrypt_secret,
    get_auth_key_ring,
    verify_totp_code,
)
from apps.accounts.auth.recovery_codes import (
    generate_ten_recovery_codes,
    redeem_recovery_code,
)
from apps.accounts.auth.stepup import (
    grant_sudo,
    is_sudo_active,
    require_sudo,
)
from apps.accounts.models import (
    ActivationCode,
    CustomUser,
    RecoveryCode,
    TOTPCredential,
)
from apps.core.middleware import RateLimitMiddleware

User = get_user_model()


@pytest.fixture
def target_employee(db):
    user = CustomUser.objects.create_user(
        employee_id="ST-CONC-001",
        full_name="Concurrency Target",
        password="ValidPassword2026!",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    return user


@pytest.mark.django_db(transaction=True)
class TestConcurrentReplayProtections:
    def test_concurrent_totp_replay_prevention(self, target_employee):
        """
        Critical Concurrency Test:
        Simulate 5 concurrent requests submitting the EXACT SAME valid TOTP code
        at the same instant across independent execution threads.
        Requirement: Exactly 1 request succeeds; 4 are rejected by atomic replay protection.
        """
        kr = get_auth_key_ring()
        raw_secret = pyotp.random_base32(length=32)
        ciphertext, nonce, key_id = encrypt_secret(raw_secret, kr)

        # Create active, confirmed TOTP credential
        TOTPCredential.objects.create(
            user=target_employee,
            encrypted_secret=ciphertext,
            nonce=nonce,
            key_id=key_id,
            confirmed_at=timezone.now(),
            last_used_step=0,
        )

        totp = pyotp.TOTP(raw_secret, interval=30, digits=6)
        valid_code = totp.now()
        # Release main thread connection lock before thread pool executes
        connection.close()

        def attempt_verify(worker_id):
            connection.close()
            for attempt in range(15):
                try:
                    # Reload fresh user instance in thread
                    u = CustomUser.objects.get(id=target_employee.id)
                    verify_totp_code(u, valid_code, key_ring=kr)
                    return True, worker_id, None
                except AuthException as e:
                    return False, worker_id, str(e.code)
                except Exception as e:
                    if "locked" in str(e).lower() and attempt < 14:
                        import time
                        time.sleep(0.04 * (attempt + 1))
                        connection.close()
                        continue
                    return False, worker_id, str(e)
                finally:
                    connection.close()

        num_workers = 5
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(attempt_verify, i) for i in range(num_workers)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        success_count = sum(1 for ok, _, _ in results if ok)
        rejection_count = sum(1 for ok, _, _ in results if not ok)

        # Exactly ONE verification must succeed
        assert success_count == 1, f"Expected 1 success, got {success_count}. Results: {results}"
        assert rejection_count == num_workers - 1, (
            f"Expected {num_workers - 1} rejections, got {rejection_count}."
        )

        # Verify database last_used_step was updated
        cred = TOTPCredential.objects.get(user=target_employee)
        assert cred.last_used_step > 0

    def test_concurrent_recovery_code_redemption(self, target_employee):
        """
        Critical Concurrency Test:
        Simulate 5 concurrent requests attempting to redeem the EXACT SAME
        one-time recovery code simultaneously.
        Requirement: Exactly 1 succeeds; all 4 concurrent requests fail.
        """
        raw_codes = generate_ten_recovery_codes(target_employee)
        chosen_code = raw_codes[0]
        # Release main thread connection lock before thread pool executes
        connection.close()

        def attempt_redeem(worker_id):
            connection.close()
            for attempt in range(15):
                try:
                    u = CustomUser.objects.get(employee_id=target_employee.employee_id)
                    success = redeem_recovery_code(u, chosen_code)
                    return success, worker_id
                except AuthException:
                    return False, worker_id
                except Exception as e:
                    if "locked" in str(e).lower() and attempt < 14:
                        import time
                        time.sleep(0.04 * (attempt + 1))
                        connection.close()
                        continue
                    return False, worker_id
                finally:
                    connection.close()

        num_workers = 5
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(attempt_redeem, i) for i in range(num_workers)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        success_count = sum(1 for ok, _ in results if ok)
        assert success_count == 1, f"Expected 1 redemption, got {success_count}."

        # Confirm code is marked used in DB (used_at is set)
        code_record = RecoveryCode.objects.filter(
            user=target_employee, used_at__isnull=False
        ).count()
        assert code_record == 1

    def test_concurrent_activation_code_redemption(self, target_employee):
        """
        Critical Concurrency Test:
        Simulate 5 concurrent threads attempting to consume the exact same activation token.
        Requirement: Exactly 1 consumes the token; 4 fail.
        """
        record, raw_code = issue_activation_code(
            user=target_employee, purpose=ActivationCode.Purpose.ACTIVATION
        )
        # Release main thread connection lock before thread pool executes
        connection.close()

        def attempt_consume(worker_id):
            connection.close()
            try:
                consumed_code, consumed_user = verify_and_consume_code(
                    employee_id=target_employee.employee_id,
                    raw_code=raw_code,
                    purpose=ActivationCode.Purpose.ACTIVATION,
                )
                return bool(consumed_user), worker_id
            except Exception:
                return False, worker_id
            finally:
                connection.close()

        num_workers = 5
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(attempt_consume, i) for i in range(num_workers)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        connection.close()
        success_count = sum(1 for ok, _ in results if ok)
        assert success_count == 1, f"Expected 1 activation consumption, got {success_count}."


        record.refresh_from_db()
        assert record.consumed_at is not None
        assert record.is_live is None


@pytest.mark.django_db
class TestProxyHeaderValidation:
    def test_spoofed_x_forwarded_for_defense(self):
        """RateLimitMiddleware extracts the outermost client IP without trusting spoofed hops when untrusted."""
        rf = RequestFactory()

        middleware = RateLimitMiddleware(lambda r: None)
        req = rf.get("/auth/login/", HTTP_X_FORWARDED_FOR="198.51.100.1, 203.0.113.195, 10.0.0.1", REMOTE_ADDR="127.0.0.1")
        ip = middleware._get_client_ip(req)
        # Must extract the leftmost client IP when arriving via trusted proxy
        assert ip == "198.51.100.1"

        # Missing or untrusted X-Forwarded-For falls back to REMOTE_ADDR
        req2 = rf.get("/auth/login/", HTTP_X_FORWARDED_FOR="10.99.99.99", REMOTE_ADDR="192.0.2.45")
        ip2 = middleware._get_client_ip(req2)
        assert ip2 == "192.0.2.45"


@pytest.mark.django_db
class TestStepUpSudoLifecycle:
    def test_sudo_mode_grant_and_timeout(self, target_employee):
        """Verify sudo authorization duration and view decorator enforcement."""
        rf = RequestFactory()
        req = rf.get("/auth/security/")
        req.user = target_employee
        req.session = {}

        # Sudo not active initially
        assert is_sudo_active(req) is False

        # Grant sudo
        grant_sudo(req, target_employee)
        assert is_sudo_active(req) is True

        # Sudo protected view allows execution when active
        @require_sudo
        def sensitive_view(request):
            return "Sensitive Action Performed"

        res = sensitive_view(req)
        assert res == "Sensitive Action Performed"

        # Simulate timeout (> 10 minutes)
        req.session["_salow_sudo_granted_at"] = 0
        assert is_sudo_active(req) is False

        with pytest.raises(PermissionDenied):
            sensitive_view(req)
