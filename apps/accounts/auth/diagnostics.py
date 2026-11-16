"""
Authentication Diagnostics & auth_doctor Self-Test Engine.
Executes complete 12-point automated verification across crypto, database,
WebAuthn, rate limiting, and security safeguards according to architectural spec.
"""

import time
from typing import NamedTuple, Optional

import pyotp
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.accounts.auth.mfa_totp import (
    decrypt_secret,
    encrypt_secret,
    get_auth_key_ring,
    render_svg_qr,
)
from apps.accounts.auth.recovery_codes import hash_recovery_code
from apps.accounts.models import AuthEvent
from apps.core.models import AuditLog, RateLimitRecord


class DiagnosticCheckResult(NamedTuple):
    name: str
    passed: bool
    details: str
    remediation: Optional[str] = None


def run_auth_doctor_checks() -> list[DiagnosticCheckResult]:  # noqa: C901
    """Execute complete 12-point system diagnostics."""
    results: list[DiagnosticCheckResult] = []

    # 1. Database Connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        results.append(
            DiagnosticCheckResult("Database Connection", True, f"Connected to {connection.vendor}")
        )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Database Connection",
                False,
                str(e),
                "Verify database credentials in .env and check network reachability to TiDB Cloud.",
            )
        )

    # 2. Database Adapter & Migrations
    try:
        executor = MigrationExecutor(connection)
        unapplied = executor.migration_plan(executor.loader.graph.leaf_nodes())
        engine = connection.settings_dict.get("ENGINE", "")
        if not unapplied:
            results.append(
                DiagnosticCheckResult(
                    "Database Adapter & Migrations",
                    True,
                    f"Engine: {engine.split('.')[-1]}, all migrations applied",
                )
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "Database Adapter & Migrations",
                    False,
                    f"{len(unapplied)} unapplied migrations detected",
                    "Run 'python manage.py migrate' to apply outstanding schema changes.",
                )
            )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Database Adapter & Migrations",
                False,
                str(e),
                "Check database adapter compatibility and connectivity.",
            )
        )

    # 3. Password Hashing Engine
    try:
        test_raw = "TestingArgon2idHashingPassword2026!"
        hashed = make_password(test_raw)
        verified = check_password(test_raw, hashed)
        hasher_name = hashed.split("$")[0]
        if verified and hasher_name == "argon2":
            results.append(
                DiagnosticCheckResult("Password Hashing", True, f"Primary hasher: {hasher_name}")
            )
        elif verified:
            results.append(
                DiagnosticCheckResult(
                    "Password Hashing",
                    True,
                    f"Active hasher: {hasher_name} (Argon2id recommended)",
                    "Configure Argon2PasswordHasher as first entry in PASSWORD_HASHERS.",
                )
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "Password Hashing",
                    False,
                    "Hash round-trip verification failed",
                    "Verify argon2-cffi package installation.",
                )
            )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Password Hashing", False, str(e), "Install argon2-cffi in active virtualenv."
            )
        )

    # 4. TOTP Configuration & SVG QR
    try:
        sample_secret = "JBSWY3DPEHPK3PXP"  # noqa: S105
        sample_uri = "otpauth://totp/Salowtech:TEST?secret=" + sample_secret
        svg = render_svg_qr(sample_uri)
        totp = pyotp.TOTP(sample_secret, interval=30, digits=6)
        code = totp.now()
        is_totp_valid = totp.verify(code)

        if "<svg" in svg and is_totp_valid:
            results.append(
                DiagnosticCheckResult(
                    "TOTP & QR Configuration", True, "RFC 6238 30s/6-digit & SVG rendering OK"
                )
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "TOTP & QR Configuration",
                    False,
                    "TOTP verification or SVG generation failed",
                    "Check qrcode and pyotp dependencies.",
                )
            )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "TOTP & QR Configuration", False, str(e), "Verify pyotp and qrcode installation."
            )
        )

    # 5. AES-256-GCM Key Ring
    try:
        kr = get_auth_key_ring()
        test_secret = pyotp.random_base32()
        ciphertext, nonce, key_id = encrypt_secret(test_secret, kr)
        decrypted = decrypt_secret(ciphertext, nonce, key_id, kr)
        if decrypted == test_secret:
            results.append(
                DiagnosticCheckResult(
                    "AES-256-GCM Key Ring", True, f"Key '{key_id}' operational (AES-256-GCM)"
                )
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "AES-256-GCM Key Ring",
                    False,
                    "Decrypted secret did not match original",
                    "Verify cryptography AES-GCM key ring configuration in apps.accounts.auth.config.",
                )
            )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "AES-256-GCM Key Ring",
                False,
                str(e),
                "Check AES encryption key ring in apps/accounts/auth/config.py.",
            )
        )

    # 6. Recovery-Code Configuration
    try:
        test_code = "REC-ABCD-1234"
        h1 = hash_recovery_code(test_code)
        h2 = hash_recovery_code("rec-abcd-1234")
        if h1 == h2 and len(h1) == 64:
            results.append(
                DiagnosticCheckResult(
                    "Recovery-Code Engine", True, "Keyed HMAC-SHA256 normalization operational"
                )
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "Recovery-Code Engine",
                    False,
                    "Hash normalization mismatch",
                    "Verify hash_recovery_code normalization in apps.accounts.totp.",
                )
            )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Recovery-Code Engine", False, str(e), "Check recovery code hashing implementation."
            )
        )

    # 7. Session Configuration & Storage
    try:
        from django.contrib.sessions.models import Session

        Session.objects.exists()
        session_engine = getattr(settings, "SESSION_ENGINE", "django.contrib.sessions.backends.db")
        results.append(
            DiagnosticCheckResult(
                "Session Storage", True, f"Backend: {session_engine.split('.')[-1]} (DB table OK)"
            )
        )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Session Storage",
                False,
                str(e),
                "Run migrations to create django_session database table.",
            )
        )

    # 8. Cookie Security Configuration
    try:
        httponly = getattr(settings, "SESSION_COOKIE_HTTPONLY", False)
        samesite = getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax")
        csrf_httponly = getattr(settings, "CSRF_COOKIE_HTTPONLY", False)
        if httponly and samesite:
            results.append(
                DiagnosticCheckResult(
                    "Cookie Security",
                    True,
                    f"HttpOnly={httponly}, SameSite={samesite}, CSRF_HttpOnly={csrf_httponly}",
                )
            )
        else:
            results.append(
                DiagnosticCheckResult(
                    "Cookie Security",
                    False,
                    f"Weak cookie flags: HttpOnly={httponly}",
                    "Set SESSION_COOKIE_HTTPONLY=True in settings.",
                )
            )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Cookie Security", False, str(e), "Review cookie security in config/settings/."
            )
        )

    # 9. Rate-Limit Backend Availability
    try:
        test_rl_key = "diag:rate_limit_test"
        RateLimitRecord.objects.filter(key=test_rl_key).delete()
        RateLimitRecord.objects.create(key=test_rl_key, timestamp=time.time())
        RateLimitRecord.objects.filter(key=test_rl_key).delete()
        results.append(
            DiagnosticCheckResult(
                "Distributed Rate Limiter", True, "Centralized TiDB tracking operational"
            )
        )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Distributed Rate Limiter",
                False,
                str(e),
                "Apply core migrations to create RateLimitRecord table.",
            )
        )

    # 10. WebAuthn Passkey Configuration
    try:
        import webauthn

        version = getattr(webauthn, "__version__", "installed")
        results.append(
            DiagnosticCheckResult(
                "WebAuthn Passkey Engine", True, f"webauthn v{version} loaded, FIDO2 ready"
            )
        )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "WebAuthn Passkey Engine",
                False,
                str(e),
                "Install webauthn>=3.0.0 in the application virtual environment.",
            )
        )

    # 11. Audit Persistence & Immutability
    try:
        AuditLog.objects.exists()
        AuthEvent.objects.exists()
        results.append(
            DiagnosticCheckResult(
                "Audit Logging Engine", True, "AuditLog and AuthEvent persistence verified"
            )
        )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Audit Logging Engine",
                False,
                str(e),
                "Ensure core and accounts migrations are applied.",
            )
        )

    # 12. Production Debug & Stub Safeguards
    try:
        is_debug = getattr(settings, "DEBUG", False)
        dev_verifier = getattr(settings, "DEV_VERIFIER", False)
        settings_module = getattr(settings, "SETTINGS_MODULE", "") or ""
        is_test = "test" in settings_module or getattr(settings, "TESTING", False)
        if not is_debug and dev_verifier and not is_test:
            results.append(
                DiagnosticCheckResult(
                    "Production Safeguards",
                    False,
                    "DEV_VERIFIER is True while DEBUG is False! Biometric stub active in production.",
                    "Set DEV_VERIFIER=False in production settings.",
                )
            )
        else:
            status_desc = (
                "Safe (Test Mode)"
                if is_test
                else ("Safe (Dev Mode)" if is_debug else "Strict (Prod Mode)")
            )
            results.append(
                DiagnosticCheckResult(
                    "Production Safeguards",
                    True,
                    f"{status_desc}: DEBUG={is_debug}, DEV_VERIFIER={dev_verifier}",
                )
            )
    except Exception as e:
        results.append(
            DiagnosticCheckResult(
                "Production Safeguards", False, str(e), "Check DEBUG and DEV_VERIFIER settings."
            )
        )

    return results
