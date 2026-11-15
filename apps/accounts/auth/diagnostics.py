"""
Authentication Diagnostics & auth_doctor Self-Test Engine.
Executes an 8-point automated sanity verification across crypto, database, and auth subsystems.
"""

import time
from typing import NamedTuple

import pyotp
from django.contrib.auth.hashers import check_password, make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring, render_svg_qr
from apps.accounts.models import LoginAttempt


class DiagnosticCheckResult(NamedTuple):
    name: str
    passed: bool
    details: str


def run_auth_doctor_checks() -> list[DiagnosticCheckResult]:  # noqa: C901
    """Execute complete 8-point system diagnostics."""
    results: list[DiagnosticCheckResult] = []

    # 1. Database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        results.append(
            DiagnosticCheckResult("Database Connection", True, f"Connected to {connection.vendor}")
        )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("Database Connection", False, str(e)))

    # 2. Migrations applied
    try:
        executor = MigrationExecutor(connection)
        unapplied = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not unapplied:
            results.append(
                DiagnosticCheckResult("Database Migrations", True, "All migrations applied")
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "Database Migrations", False, f"{len(unapplied)} unapplied migrations detected"
                )
            )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("Database Migrations", False, str(e)))

    # 3. Session table & storage
    try:
        from django.contrib.sessions.models import Session

        Session.objects.exists()
        results.append(
            DiagnosticCheckResult("Session Storage", True, "Server-side session table verified")
        )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("Session Storage", False, str(e)))

    # 4. AES-256-GCM key ring
    try:
        kr = get_auth_key_ring()
        test_secret = pyotp.random_base32()
        ciphertext, nonce, key_id = encrypt_secret(test_secret, kr)
        from apps.accounts.auth.mfa_totp import decrypt_secret

        decrypted = decrypt_secret(ciphertext, nonce, key_id, kr)
        if decrypted == test_secret:
            results.append(
                DiagnosticCheckResult("AES-256-GCM Key Ring", True, f"Key '{key_id}' operational")
            )
        else:
            results.append(
                DiagnosticCheckResult("AES-256-GCM Key Ring", False, "Decryption mismatch")
            )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("AES-256-GCM Key Ring", False, str(e)))

    # 5. Server clock check
    try:
        system_time = time.time()
        # Verify step calculation
        step = int(system_time) // 30
        results.append(
            DiagnosticCheckResult(
                "Server Clock", True, f"System UTC timestamp: {int(system_time)} (Step {step})"
            )
        )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("Server Clock", False, str(e)))

    # 6. Password hashing engine (Argon2id)
    try:
        test_raw = "TestingArgon2idHashingPassword2026!"
        hashed = make_password(test_raw)
        verified = check_password(test_raw, hashed)
        hasher_name = hashed.split("$")[0]
        if verified:
            results.append(
                DiagnosticCheckResult("Password Hashing", True, f"Active hasher: {hasher_name}")
            )
        else:
            results.append(
                DiagnosticCheckResult("Password Hashing", False, "Hash verification failed")
            )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("Password Hashing", False, str(e)))

    # 7. TOTP & SVG QR Rendering
    try:
        sample_secret = "JBSWY3DPEHPK3PXP"  # noqa: S105
        sample_uri = "otpauth://totp/Salowtech:TEST?secret=" + sample_secret
        svg = render_svg_qr(sample_uri)
        totp = pyotp.TOTP(sample_secret)
        code = totp.now()
        is_totp_valid = totp.verify(code)

        if "<svg" in svg and is_totp_valid:
            results.append(
                DiagnosticCheckResult(
                    "TOTP & SVG QR Generator", True, "Round trip and SVG rendering OK"
                )
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "TOTP & SVG QR Generator", False, "TOTP or SVG verification failed"
                )
            )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("TOTP & SVG QR Generator", False, str(e)))

    # 8. Lockout counters in database
    try:
        test_key = "diag:test_key"
        LoginAttempt.objects.filter(key=test_key).delete()
        attempt = LoginAttempt.objects.create(key=test_key, counter=1)
        attempt.refresh_from_db()
        LoginAttempt.objects.filter(key=test_key).delete()
        results.append(
            DiagnosticCheckResult(
                "Lockout Database Counters", True, "DB-backed counters operational"
            )
        )
    except Exception as e:  # noqa: BLE001
        results.append(DiagnosticCheckResult("Lockout Database Counters", False, str(e)))

    return results
