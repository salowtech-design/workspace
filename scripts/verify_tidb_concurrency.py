"""
Independent Forensic TiDB Concurrency Verification Script.
Authoritative Target: TiDB Cloud (AWS ap-southeast-1) MySQL-compatible distributed SQL.

Verifies:
1. Multi-threaded atomic TOTP code verification and replay defense (select_for_update).
2. Multi-threaded atomic recovery code redemption.
3. Multi-threaded atomic activation code redemption.
4. Pessimistic transaction serialization on TiKV.
"""

import concurrent.futures
import os
import sys
import time
from pathlib import Path

# Setup environment & Django settings
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")
os.environ["USE_TIDB"] = "true"

try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django

django.setup()

import pyotp
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.auth.activation import issue_activation_code, verify_and_consume_code
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring, verify_totp_code
from apps.accounts.auth.recovery_codes import generate_ten_recovery_codes, redeem_recovery_code
from apps.accounts.models import ActivationCode, CustomUser, RecoveryCode, TOTPCredential


def run_tidb_concurrency_audit():
    print("=" * 70)
    print("SALOW WORKSPACE - TiDB CLOUD CONCURRENCY & REPLAY FORENSIC AUDIT")
    print("=" * 70)

    # 1. Connection and Engine Info
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@version, @@version_comment, DATABASE();")
        row = cursor.fetchone()
        version, comment, db = row
        print(f"[*] Connected Database: {db}")
        print(f"[*] Engine Version: {version} ({comment})")

        cursor.execute("SHOW VARIABLES LIKE 'tidb_txn_mode';")
        txn_mode = cursor.fetchone()
        print(f"[*] TiDB Txn Mode: {txn_mode[1] if txn_mode else 'default'}")

        cursor.execute("SHOW STATUS LIKE 'Ssl_cipher';")
        ssl_cipher = cursor.fetchone()
        print(f"[*] TLS Cipher: {ssl_cipher[1] if ssl_cipher else 'None'}")

    kr = get_auth_key_ring()
    test_emp_id = "TIDB-CONC-TEST-01"

    # Setup or retrieve test user
    with transaction.atomic():
        user, created = CustomUser.objects.get_or_create(
            employee_id=test_emp_id,
            defaults={
                "full_name": "TiDB Concurrency Test Subject",
                "email": "tidb-concurrency@salowtech.internal",
                "role": CustomUser.Role.EMPLOYEE,
                "status": CustomUser.Status.ACTIVE,
            },
        )
        if not user.password:
            user.set_password("TiDB-Pessimistic-Test-2026!")
            user.save()

    print(f"[*] Target User: {user.employee_id} (ID: {user.id})")

    # -------------------------------------------------------------
    # TEST 1: Concurrent TOTP Replay Prevention
    # -------------------------------------------------------------
    print("\n--- [TEST 1] Concurrent TOTP Replay Prevention (5 Threads) ---")
    raw_secret = pyotp.random_base32(length=32)
    ciphertext, nonce, key_id = encrypt_secret(raw_secret, kr)

    with transaction.atomic():
        TOTPCredential.objects.filter(user=user).delete()
        TOTPCredential.objects.create(
            user=user,
            encrypted_secret=ciphertext,
            nonce=nonce,
            key_id=key_id,
            confirmed_at=timezone.now(),
            last_used_step=0,
        )

    totp = pyotp.TOTP(raw_secret, interval=30, digits=6)
    valid_code = totp.now()

    def attempt_verify(worker_id):
        connection.close()  # Force fresh connection per thread
        t0 = time.perf_counter()
        try:
            u = CustomUser.objects.get(id=user.id)
            verify_totp_code(u, valid_code, key_ring=kr)
            elapsed = time.perf_counter() - t0
            return (True, worker_id, "VERIFIED", elapsed)
        except AuthException as ae:
            elapsed = time.perf_counter() - t0
            return (False, worker_id, f"AUTH_ERR: {ae.code}", elapsed)
        except Exception as ex:
            elapsed = time.perf_counter() - t0
            return (False, worker_id, f"EXC: {type(ex).__name__} - {str(ex)[:40]}", elapsed)
        finally:
            connection.close()

    results_totp = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_verify, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results_totp.append(f.result())

    success_totp = [r for r in results_totp if r[0]]
    fail_totp = [r for r in results_totp if not r[0]]
    print(f"[*] TOTP Results: {len(success_totp)} Successes, {len(fail_totp)} Rejections")
    for ok, wid, msg, el in results_totp:
        status_tag = "[PASS-OK]" if ok else "[REJECTED]"
        print(f"    Worker {wid}: {status_tag} {msg} ({el * 1000:.1f}ms)")

    assert len(success_totp) == 1, f"FAIL: Expected exactly 1 TOTP success, got {len(success_totp)}"
    assert len(fail_totp) == 4, f"FAIL: Expected 4 rejections, got {len(fail_totp)}"
    print(
        "[PASS] Test 1: Exactly 1 concurrent TOTP submission accepted, 4 replayed requests rejected."
    )

    # -------------------------------------------------------------
    # TEST 2: Concurrent Recovery Code Redemption
    # -------------------------------------------------------------
    print("\n--- [TEST 2] Concurrent Recovery Code Redemption (5 Threads) ---")
    with transaction.atomic():
        RecoveryCode.objects.filter(user=user).delete()
    raw_codes = generate_ten_recovery_codes(user)
    target_code = raw_codes[0]

    def attempt_redeem(worker_id):
        connection.close()
        t0 = time.perf_counter()
        try:
            ok = redeem_recovery_code(user, target_code)
            elapsed = time.perf_counter() - t0
            return (ok, worker_id, "REDEEMED" if ok else "REJECTED", elapsed)
        except Exception as ex:
            elapsed = time.perf_counter() - t0
            return (False, worker_id, f"EXC: {type(ex).__name__} - {str(ex)[:40]}", elapsed)
        finally:
            connection.close()

    results_rc = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_redeem, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results_rc.append(f.result())

    success_rc = [r for r in results_rc if r[0]]
    fail_rc = [r for r in results_rc if not r[0]]
    print(f"[*] Recovery Code Results: {len(success_rc)} Redeemed, {len(fail_rc)} Rejected")
    for ok, wid, msg, el in results_rc:
        status_tag = "[REDEEMED]" if ok else "[BLOCKED]"
        print(f"    Worker {wid}: {status_tag} {msg} ({el * 1000:.1f}ms)")

    assert len(success_rc) == 1, f"FAIL: Expected exactly 1 redemption, got {len(success_rc)}"
    assert len(fail_rc) == 4, f"FAIL: Expected 4 rejections, got {len(fail_rc)}"
    print("[PASS] Test 2: Exactly 1 concurrent recovery code redeemed, 4 rejected.")

    # -------------------------------------------------------------
    # TEST 3: Concurrent Activation Code Redemption
    # -------------------------------------------------------------
    print("\n--- [TEST 3] Concurrent Activation Code Redemption (5 Threads) ---")
    with transaction.atomic():
        ActivationCode.objects.filter(user=user).delete()
    code_record, raw_activation = issue_activation_code(
        user=user, purpose=ActivationCode.Purpose.ACTIVATION
    )

    def attempt_activate(worker_id):
        connection.close()
        t0 = time.perf_counter()
        try:
            consumed_code, consumed_user = verify_and_consume_code(
                employee_id=user.employee_id,
                raw_code=raw_activation,
                purpose=ActivationCode.Purpose.ACTIVATION,
            )
            elapsed = time.perf_counter() - t0
            ok = bool(consumed_user)
            return (ok, worker_id, "ACTIVATED" if ok else "FAILED", elapsed)
        except Exception as ex:
            elapsed = time.perf_counter() - t0
            return (False, worker_id, f"EXC: {type(ex).__name__} - {str(ex)[:40]}", elapsed)
        finally:
            connection.close()

    results_act = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_activate, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results_act.append(f.result())

    success_act = [r for r in results_act if r[0]]
    fail_act = [r for r in results_act if not r[0]]
    print(f"[*] Activation Code Results: {len(success_act)} Consumed, {len(fail_act)} Rejected")
    for ok, wid, msg, el in results_act:
        status_tag = "[CONSUMED]" if ok else "[BLOCKED]"
        print(f"    Worker {wid}: {status_tag} {msg} ({el * 1000:.1f}ms)")

    assert len(success_act) == 1, f"FAIL: Expected exactly 1 consumption, got {len(success_act)}"
    assert len(fail_act) == 4, f"FAIL: Expected 4 rejections, got {len(fail_act)}"
    print("[PASS] Test 3: Exactly 1 concurrent activation token consumed, 4 rejected.")

    # -------------------------------------------------------------
    # TEST 4: Concurrent Rate-Limit Updates (TiDB Sliding Window)
    # -------------------------------------------------------------
    print("\n--- [TEST 4] Concurrent Rate-Limit Updates on TiDB (5 Threads, 10 Inserts) ---")
    from apps.core.models import RateLimitRecord

    test_rate_key = f"tidb_test_rate_{int(time.time())}"
    now_ts = time.time()

    def record_rate_hit(worker_id):
        connection.close()
        t0 = time.perf_counter()
        try:
            RateLimitRecord.objects.create(key=test_rate_key, timestamp=now_ts)
            elapsed = time.perf_counter() - t0
            return (True, worker_id, elapsed)
        except Exception:
            elapsed = time.perf_counter() - t0
            return (False, worker_id, elapsed)
        finally:
            connection.close()

    results_rate = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(record_rate_hit, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results_rate.append(f.result())

    rate_successes = sum(1 for ok, _, _ in results_rate if ok)
    db_count = RateLimitRecord.objects.filter(key=test_rate_key).count()
    print(
        f"[*] Rate-Limit Concurrent Hits: {rate_successes}/10 successful, DB persisted count: {db_count}"
    )
    assert rate_successes == 10, f"Expected 10 successes, got {rate_successes}"
    assert db_count == 10, f"Expected 10 DB records, got {db_count}"
    # Cleanup test records
    RateLimitRecord.objects.filter(key=test_rate_key).delete()
    print(
        "[PASS] Test 4: Distributed rate-limit records inserted and counted atomically under concurrent load."
    )

    # -------------------------------------------------------------
    # TEST 5: Concurrent Session Revocation on TiDB
    # -------------------------------------------------------------
    print("\n--- [TEST 5] Concurrent Session Revocation & Verification (5 Threads) ---")
    from apps.accounts.auth.sessions import revoke_user_sessions
    from apps.accounts.models import UserSession

    # Seed 5 user sessions
    with transaction.atomic():
        UserSession.objects.filter(user=user).delete()
        for i in range(5):
            UserSession.objects.create(
                user=user,
                session_key=f"tidb_sess_conc_{i}_{int(time.time())}",
                mfa_verified=True,
                ip_address="127.0.0.1",
            )

    def attempt_revoke_or_read(worker_id):
        connection.close()
        t0 = time.perf_counter()
        try:
            if worker_id == 0:
                count = revoke_user_sessions(user)
                elapsed = time.perf_counter() - t0
                return ("REVOKED", worker_id, count, elapsed)
            else:
                remaining = UserSession.objects.filter(user=user).count()
                elapsed = time.perf_counter() - t0
                return ("READ", worker_id, remaining, elapsed)
        except Exception as ex:
            elapsed = time.perf_counter() - t0
            return ("ERROR", worker_id, str(ex)[:30], elapsed)
        finally:
            connection.close()

    results_sess = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_revoke_or_read, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results_sess.append(f.result())

    for op, wid, val, el in results_sess:
        print(f"    Worker {wid} [{op}]: {val} ({el * 1000:.1f}ms)")

    # Ensure all sessions are completely cleared
    final_count = UserSession.objects.filter(user=user).count()
    assert final_count == 0, f"Expected 0 active sessions after revocation, found {final_count}"
    print(
        "[PASS] Test 5: Immediate multi-device session revocation purged all active sessions across connections."
    )

    # -------------------------------------------------------------
    # TEST 6: Concurrent MFA Replacement Token Consumption
    # -------------------------------------------------------------
    print("\n--- [TEST 6] Concurrent MFA Replacement Token Consumption (5 Threads) ---")
    with transaction.atomic():
        ActivationCode.objects.filter(
            user=user, purpose=ActivationCode.Purpose.MFA_REPLACEMENT
        ).delete()
    mfa_rec, raw_mfa_code = issue_activation_code(
        user=user, purpose=ActivationCode.Purpose.MFA_REPLACEMENT
    )

    def attempt_consume_mfa(worker_id):
        connection.close()
        t0 = time.perf_counter()
        try:
            consumed_code, consumed_user = verify_and_consume_code(
                employee_id=user.employee_id,
                raw_code=raw_mfa_code,
                purpose=ActivationCode.Purpose.MFA_REPLACEMENT,
            )
            elapsed = time.perf_counter() - t0
            ok = bool(consumed_user)
            return (ok, worker_id, "CONSUMED" if ok else "REJECTED", elapsed)
        except Exception as ex:
            elapsed = time.perf_counter() - t0
            return (False, worker_id, f"EXC: {type(ex).__name__}", elapsed)
        finally:
            connection.close()

    results_mfa = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_consume_mfa, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results_mfa.append(f.result())

    success_mfa = [r for r in results_mfa if r[0]]
    fail_mfa = [r for r in results_mfa if not r[0]]
    print(f"[*] MFA Replacement Results: {len(success_mfa)} Consumed, {len(fail_mfa)} Rejected")
    for ok, wid, msg, el in results_mfa:
        status_tag = "[CONSUMED]" if ok else "[BLOCKED]"
        print(f"    Worker {wid}: {status_tag} {msg} ({el * 1000:.1f}ms)")

    assert len(success_mfa) == 1, (
        f"FAIL: Expected exactly 1 MFA replacement consumption, got {len(success_mfa)}"
    )
    assert len(fail_mfa) == 4, f"FAIL: Expected 4 rejections, got {len(fail_mfa)}"
    print("[PASS] Test 6: Exactly 1 concurrent MFA replacement token consumed, 4 rejected.")

    # -------------------------------------------------------------
    # TEST 7: Outbox Event Atomic Processing
    # -------------------------------------------------------------
    print("\n--- [TEST 7] Outbox Event Atomic Claiming and Status Transitions (5 Threads) ---")
    from apps.core.models import OutboxEvent

    test_event_id = f"evt_tidb_{int(time.time() * 1000)}"
    test_event_type = f"audit.concurrency.test.{int(time.time())}"
    with transaction.atomic():
        event = OutboxEvent.objects.create(
            event_id=test_event_id,
            event_type=test_event_type,
            payload={"msg": "concurrency test on TiDB"},
            status=OutboxEvent.Status.PENDING,
        )

    def attempt_claim_event(worker_id):
        connection.close()
        t0 = time.perf_counter()
        try:
            with transaction.atomic():
                # Attempt to lock and claim the pending event
                ev = (
                    OutboxEvent.objects.select_for_update()
                    .filter(event_id=test_event_id, status=OutboxEvent.Status.PENDING)
                    .first()
                )
                if ev:
                    ev.status = OutboxEvent.Status.PROCESSED
                    ev.processed_at = timezone.now()
                    ev.save(update_fields=["status", "processed_at"])
                    elapsed = time.perf_counter() - t0
                    return (True, worker_id, "CLAIMED_AND_PROCESSED", elapsed)
                else:
                    elapsed = time.perf_counter() - t0
                    return (False, worker_id, "ALREADY_CLAIMED", elapsed)
        except Exception as ex:
            elapsed = time.perf_counter() - t0
            return (False, worker_id, f"EXC: {type(ex).__name__} - {str(ex)[:30]}", elapsed)
        finally:
            connection.close()

    results_outbox = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_claim_event, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results_outbox.append(f.result())

    success_outbox = [r for r in results_outbox if r[0]]
    fail_outbox = [r for r in results_outbox if not r[0]]
    print(f"[*] Outbox Claim Results: {len(success_outbox)} Claimed, {len(fail_outbox)} Bypassed")
    for ok, wid, msg, el in results_outbox:
        status_tag = "[CLAIMED]" if ok else "[BYPASSED]"
        print(f"    Worker {wid}: {status_tag} {msg} ({el * 1000:.1f}ms)")

    assert len(success_outbox) == 1, (
        f"FAIL: Expected exactly 1 outbox claim, got {len(success_outbox)}"
    )
    assert len(fail_outbox) == 4, f"FAIL: Expected 4 bypasses, got {len(fail_outbox)}"
    # Cleanup event
    OutboxEvent.objects.filter(event_id=test_event_id).delete()
    print(
        "[PASS] Test 7: Exactly 1 concurrent worker claimed the pending outbox event with pessimistic locking."
    )

    print("\n" + "=" * 70)
    print("ALL 7 TIDB CLOUD CONCURRENCY & REPLAY AUDIT TESTS PASSED [PASS]")
    print("=" * 70)


if __name__ == "__main__":
    run_tidb_concurrency_audit()
