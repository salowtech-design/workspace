import time

from apps.accounts.totp import (
    generate_recovery_codes,
    generate_totp_code,
    generate_totp_secret,
    hash_recovery_code,
    verify_totp_token,
)


def test_totp_secret_and_token_cycle():
    """Verify standard RFC 6238 TOTP round-trip generation and verification."""
    secret = generate_totp_secret()
    assert len(secret) == 32

    current_code = generate_totp_code(secret)
    assert len(current_code) == 6
    assert current_code.isdigit()

    # Current code must pass verification
    assert verify_totp_token(secret, current_code) is True

    # Bogus code must fail
    assert verify_totp_token(secret, "999999") is False
    assert verify_totp_token(secret, "invalid") is False


def test_totp_drift_window_tolerance():
    """Verify ±30s clock drift window tolerance."""
    secret = generate_totp_secret()
    now_interval = int(time.time()) // 30

    past_code = generate_totp_code(secret, intervals_no=now_interval - 1)
    future_code = generate_totp_code(secret, intervals_no=now_interval + 1)
    too_old_code = generate_totp_code(secret, intervals_no=now_interval - 5)

    assert verify_totp_token(secret, past_code, window=1) is True
    assert verify_totp_token(secret, future_code, window=1) is True
    assert verify_totp_token(secret, too_old_code, window=1) is False


def test_recovery_code_generation_and_hashing():
    """Verify recovery codes are distinct, properly formatted, and securely hashed."""
    codes = generate_recovery_codes(count=8)
    assert len(codes) == 8
    assert len(set(codes)) == 8  # All unique

    for code in codes:
        assert "-" in code
        h1 = hash_recovery_code(code)
        h2 = hash_recovery_code(code.lower().replace("-", ""))
        assert h1 == h2  # Formatting and case normalization
        assert len(h1) == 64  # SHA-256 hex string
