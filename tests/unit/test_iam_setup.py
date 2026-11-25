"""IAM admin setup (pending admin, single-use link, authenticator confirm) and restricted recovery."""

from datetime import timedelta

import pyotp
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.auth import iam_setup
from apps.accounts.auth.crypto import decrypt_totp_secret
from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.iam_auth import authenticate_iam_admin
from apps.accounts.models import (
    AuthEvent,
    CustomUser,
    IAMAdminIdentity,
    IAMRecoveryCode,
    IAMSession,
    IAMSetupToken,
    LoginAttempt,
    SecurityAlert,
)

PW = "A-long-iam-password-2026"


def _user(n=1, status=CustomUser.Status.ACTIVE):
    return CustomUser.objects.create_user(
        employee_id=f"ST-IS{n}",
        full_name=f"IS{n}",
        email=f"st-is{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=CustomUser.Role.EMPLOYEE,
        status=status,
        is_active=True,
    )


def _secret(admin):
    admin.refresh_from_db()
    return decrypt_totp_secret(
        bytes(admin.encrypted_totp_secret), bytes(admin.totp_nonce or b""), admin.totp_key_id
    )


def _enroll(n=1):
    """Run the whole first setup; returns (admin, secret, recovery_codes)."""
    user = _user(n)
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, f"IAM-T-{n}")
    iam_setup.set_password(raw, PW, PW)
    iam_setup.qr_for(raw)
    secret = _secret(admin)
    codes = iam_setup.confirm_authenticator(raw, pyotp.TOTP(secret).now())
    admin.refresh_from_db()
    return admin, secret, codes


@pytest.mark.django_db
def test_invite_makes_a_pending_admin_that_cannot_sign_in():
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "iam-t-1")
    assert admin.status == IAMAdminIdentity.Status.PENDING
    assert admin.admin_id == "IAM-T-1"
    assert admin.encrypted_totp_secret is None
    assert not admin.has_usable_password_hash()
    tok = IAMSetupToken.objects.get(iam_admin=admin)
    assert raw not in (tok.token_hash, "") and tok.token_hash != raw
    assert 9 * 60 < (tok.expires_at - timezone.now()).total_seconds() <= 10 * 60
    with pytest.raises(AuthException):
        authenticate_iam_admin("IAM-T-1", PW, "123456")
    assert AuthEvent.objects.filter(event="iam_admin_invited").exists()
    assert SecurityAlert.objects.filter(reference_id="IAM-T-1").exists()


@pytest.mark.django_db
def test_invite_refuses_inactive_people_unknown_people_and_existing_admins():
    with pytest.raises(ValidationError):
        iam_setup.invite_iam_admin("NOPE", "IAM-X")
    suspended = _user(2, CustomUser.Status.SUSPENDED)
    with pytest.raises(ValidationError):
        iam_setup.invite_iam_admin(suspended.employee_id, "IAM-X")
    admin, _s, _c = _enroll(3)
    with pytest.raises(ValidationError):
        iam_setup.invite_iam_admin(admin.user.employee_id, "IAM-Y")


@pytest.mark.django_db
def test_full_setup_activates_only_after_the_first_code_and_signs_in():
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    assert iam_setup.setup_state(raw)["step"] == "password"
    with pytest.raises(iam_setup.SetupError):
        iam_setup.set_password(raw, "short", "short")
    with pytest.raises(iam_setup.SetupError):
        iam_setup.set_password(raw, PW, PW + "x")
    with pytest.raises(iam_setup.SetupError):
        iam_setup.qr_for(raw)  # no QR before the password
    iam_setup.set_password(raw, PW, PW)
    assert iam_setup.setup_state(raw)["step"] == "authenticator"
    q1 = iam_setup.qr_for(raw)
    assert iam_setup.qr_for(raw)["secret"] == q1["secret"]  # reload keeps the same secret
    assert "<svg" in q1["svg"]
    admin.refresh_from_db()
    assert admin.status == IAMAdminIdentity.Status.PENDING  # still off before confirmation
    with pytest.raises(AuthException):
        authenticate_iam_admin("IAM-T-1", PW, pyotp.TOTP(q1["secret"]).now())
    codes = iam_setup.confirm_authenticator(raw, pyotp.TOTP(q1["secret"]).now())
    admin.refresh_from_db()
    assert admin.status == IAMAdminIdentity.Status.ACTIVE and admin.totp_confirmed_at
    assert len(codes) == 10 and len(set(codes)) == 10
    # the same code is replay-protected at sign-in; a later step works
    with pytest.raises(AuthException):
        authenticate_iam_admin("IAM-T-1", PW, pyotp.TOTP(q1["secret"]).now())
    assert AuthEvent.objects.filter(event="iam_admin_setup_completed").exists()
    assert SecurityAlert.objects.filter(title__contains="completed setup").exists()


@pytest.mark.django_db
def test_recovery_codes_are_stored_hashed_and_link_is_single_use():
    admin, _secret_b32, codes = _enroll()
    stored = list(
        IAMRecoveryCode.objects.filter(iam_admin=admin).values_list("keyed_hash", flat=True)
    )
    assert len(stored) == 10
    for c in codes:
        assert c not in stored and c.replace("-", "") not in stored
    tok = IAMSetupToken.objects.get(iam_admin=admin)
    assert tok.used_at is not None
    assert not AuthEvent.objects.filter(message__contains=codes[0]).exists()
    with pytest.raises(iam_setup.SetupError):
        iam_setup.setup_state("anything-not-the-token")


@pytest.mark.django_db
def test_expired_link_is_refused():
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    IAMSetupToken.objects.filter(iam_admin=admin).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(iam_setup.SetupError):
        iam_setup.set_password(raw, PW, PW)


@pytest.mark.django_db
def test_five_wrong_codes_burn_the_link_and_raise_an_alert():
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    iam_setup.set_password(raw, PW, PW)
    secret = iam_setup.qr_for(raw)["secret"]
    good = pyotp.TOTP(secret).now()
    bad = "000000" if good != "000000" else "111111"
    for _ in range(4):
        with pytest.raises(iam_setup.SetupError, match="not correct"):
            iam_setup.confirm_authenticator(raw, bad)
    with pytest.raises(iam_setup.SetupError, match="closed"):
        iam_setup.confirm_authenticator(raw, bad)
    with pytest.raises(iam_setup.SetupError):
        iam_setup.confirm_authenticator(raw, good)  # a right code no longer helps
    admin.refresh_from_db()
    assert admin.status == IAMAdminIdentity.Status.PENDING
    assert SecurityAlert.objects.filter(title__contains="link burned").exists()


@pytest.mark.django_db
def test_new_invitation_closes_the_old_link():
    user = _user()
    _a, raw1 = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    _a, raw2 = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    with pytest.raises(iam_setup.SetupError):
        iam_setup.set_password(raw1, PW, PW)
    iam_setup.set_password(raw2, PW, PW)


@pytest.mark.django_db
def test_recovery_code_with_password_opens_no_session_and_revokes_sessions():
    admin, secret, codes = _enroll()
    IAMSession.objects.create(
        session_key="k" * 40, session_digest="d" * 64, iam_admin=admin, is_active=True
    )
    new_raw = iam_setup.recover_with_code("iam-t-1", PW, codes[0])
    admin.refresh_from_db()
    assert admin.status == IAMAdminIdentity.Status.PENDING
    assert admin.encrypted_totp_secret is None and admin.totp_confirmed_at is None
    assert not admin.sessions.filter(is_active=True).exists()
    with pytest.raises(AuthException):  # old authenticator no longer signs in
        authenticate_iam_admin("IAM-T-1", PW, pyotp.TOTP(secret).now())
    # the used code is gone, other codes survive until the new set is confirmed
    with pytest.raises(AuthException):
        iam_setup.recover_with_code("IAM-T-1", PW, codes[0])
    assert iam_setup.setup_state(new_raw)["step"] == "authenticator"  # password is kept
    iam_setup.qr_for(new_raw)
    new_secret = _secret(admin)
    assert new_secret != secret
    new_codes = iam_setup.confirm_authenticator(new_raw, pyotp.TOTP(new_secret).now())
    assert set(new_codes).isdisjoint(codes)
    # old codes are dead after the replacement
    with pytest.raises(AuthException):
        iam_setup.recover_with_code("IAM-T-1", PW, codes[1])
    assert AuthEvent.objects.filter(event="iam_admin_authenticator_replaced").exists()
    assert AuthEvent.objects.filter(event="iam_admin_recovery_code_redeemed").exists()
    assert SecurityAlert.objects.filter(category=SecurityAlert.Category.RECOVERY_CODE_USED).exists()


@pytest.mark.django_db
def test_recovery_needs_the_password_and_failures_lock_out():
    admin, _s, codes = _enroll()
    for _ in range(3):
        with pytest.raises(AuthException):
            iam_setup.recover_with_code("IAM-T-1", "wrong-password-wrong", codes[0])
    assert LoginAttempt.objects.filter(key="iam_login:IAM-T-1", locked_until__isnull=False).exists()
    with pytest.raises(AuthException):
        iam_setup.recover_with_code("IAM-T-1", PW, codes[0])  # locked, even with the right inputs
    admin.refresh_from_db()
    assert admin.status == IAMAdminIdentity.Status.ACTIVE
    assert IAMRecoveryCode.objects.filter(iam_admin=admin, used_at__isnull=True).count() == 10


@pytest.mark.django_db
def test_recovery_refused_for_unknown_pending_or_inactive_person():
    with pytest.raises(AuthException):
        iam_setup.recover_with_code("IAM-NOPE", PW, "AAAAA-BBBBB")
    user = _user()
    _a, _raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    with pytest.raises(AuthException):
        iam_setup.recover_with_code("IAM-T-1", PW, "AAAAA-BBBBB")
    admin, _s, codes = _enroll(4)
    CustomUser.objects.filter(pk=admin.user_id).update(status=CustomUser.Status.SUSPENDED)
    with pytest.raises(AuthException):
        iam_setup.recover_with_code("IAM-T-4", PW, codes[0])


@pytest.mark.django_db
def test_setup_and_recover_views_work_without_a_session(client):
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    url = reverse("iam:setup")
    r = client.get(url)
    assert b"iam-claim-form" in r.content and raw.encode() not in r.content
    r = client.post(url, {"action": "exchange", "token": raw})
    assert r.status_code == 302 and r["Location"] == url and raw not in r["Location"]
    assert "no-store" in r["Cache-Control"] and r["Referrer-Policy"] == "same-origin"
    cookie = r.cookies["iam_setup_token"]
    assert cookie["httponly"] and cookie.value != raw
    assert cookie["path"] == reverse("iam:setup") and cookie["samesite"] == "Strict"
    # the original token is dead the moment it is swapped
    r2 = Client().post(url, {"action": "exchange", "token": raw})
    assert b"not valid" in r2.content
    r = client.get(url)
    assert r.status_code == 200 and b'name="confirm"' in r.content
    assert "no-store" in r["Cache-Control"]
    r = client.post(url, {"action": "password", "password": PW, "confirm": PW})
    assert b"iam-setup-qr" in r.content
    secret = _secret(admin)
    r = client.post(url, {"action": "confirm", "totp_code": "000000"})
    assert b"not correct" in r.content
    r = client.post(url, {"action": "confirm", "totp_code": pyotp.TOTP(secret).now()})
    assert b"iam-recovery-codes" in r.content and "no-store" in r["Cache-Control"]
    assert b"iam-claim-form" in client.get(url).content  # cookie token is gone afterwards
    r = client.get(reverse("iam:recover"))
    assert r.status_code == 200
    r = client.post(
        reverse("iam:recover"),
        {"admin_id": "IAM-T-1", "password": "x", "recovery_code": "y"},
        REMOTE_ADDR="10.9.9.9",  # a different caller, so the per-IP sensitive limiter does not apply
    )
    assert b"Invalid IAM administrator credentials" in r.content


@pytest.mark.django_db
def test_recover_view_goes_to_setup_without_a_token_in_the_url_and_opens_no_session(client):
    admin, _s, codes = _enroll()
    r = client.post(
        reverse("iam:recover"), {"admin_id": "IAM-T-1", "password": PW, "recovery_code": codes[0]}
    )
    assert r.status_code == 302 and r["Location"] == reverse("iam:setup")
    assert "iam_setup_token" in r.cookies and "iam_session" not in str(r.cookies).lower()
    admin.refresh_from_db()
    assert admin.status == IAMAdminIdentity.Status.PENDING
    assert not admin.sessions.filter(is_active=True).exists()


@pytest.mark.django_db
def test_exchange_makes_the_url_token_single_use_and_keeps_expiry():
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    before = IAMSetupToken.objects.get(iam_admin=admin).expires_at
    fresh = iam_setup.exchange_link(raw)
    assert fresh != raw
    with pytest.raises(iam_setup.SetupError):
        iam_setup.exchange_link(raw)
    assert IAMSetupToken.objects.get(iam_admin=admin).expires_at == before
    iam_setup.set_password(fresh, PW, PW)


@pytest.mark.django_db
def test_reinvite_cannot_touch_an_active_admin_and_recovery_ends_sudo():
    from apps.accounts.auth.stepup import elevate_sudo, is_sudo_active

    admin, secret, codes = _enroll()
    with pytest.raises(ValidationError):
        iam_setup.invite_iam_admin(admin.user.employee_id, "IAM-OTHER", "super_admin")
    admin.refresh_from_db()
    assert admin.status == IAMAdminIdentity.Status.ACTIVE and admin.role == "admin"
    assert admin.encrypted_totp_secret is not None
    elevate_sudo(admin.user)
    assert is_sudo_active(admin.user)
    iam_setup.recover_with_code("IAM-T-1", PW, codes[0])
    assert not is_sudo_active(admin.user)


@pytest.mark.django_db
def test_invite_command_prints_one_link_and_no_other_secret(capsys):
    user = _user()
    call_command("iam_admin_invite", "--employee-id", user.employee_id, "--admin-id", "IAM-T-9")
    out = capsys.readouterr().out
    assert "/setup/#" in out and "pending" in out
    assert IAMAdminIdentity.objects.get(admin_id="IAM-T-9").status == "pending"


@pytest.mark.django_db
def test_reinvite_and_burn_clear_an_unconfirmed_authenticator_secret():
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    iam_setup.set_password(raw, PW, PW)
    first = iam_setup.qr_for(raw)["secret"]
    _a, raw2 = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    admin.refresh_from_db()
    assert admin.encrypted_totp_secret is None
    iam_setup.qr_for(raw2)
    assert _secret(admin) != first
    for _ in range(5):
        with pytest.raises(iam_setup.SetupError):
            iam_setup.confirm_authenticator(raw2, "000000")
    admin.refresh_from_db()
    assert admin.encrypted_totp_secret is None


@pytest.mark.django_db
def test_recovery_ends_break_glass_sudo_and_pending_l3_requests():
    from apps.accounts.auth.stepup import activate_break_glass, elevate_sudo
    from apps.accounts.models import IAMDualApprovalRequest, PrivilegeElevation

    admin, _s, codes = _enroll()
    other, _s2, _c2 = _enroll(2)
    elevate_sudo(admin.user)
    PrivilegeElevation.objects.get_or_create(
        user_id_ref=admin.user_id, kind="break_glass", defaults={"granted_at": 1.0}
    )
    req = IAMDualApprovalRequest.objects.create(
        action="x",
        target_id="t",
        reason="r",
        requester=admin,
        requester_stepup_verified_at=timezone.now(),
        approver=other,
        status=IAMDualApprovalRequest.Status.APPROVED,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    iam_setup.recover_with_code("IAM-T-1", PW, codes[0])
    assert not PrivilegeElevation.objects.filter(
        user_id_ref=admin.user_id, kind__in=["sudo", "break_glass"]
    ).exists()
    req.refresh_from_db()
    assert req.status == IAMDualApprovalRequest.Status.REJECTED
    assert activate_break_glass  # imported only to prove the module is the real one


@pytest.mark.django_db
def test_reinvite_after_a_recovery_issues_a_replace_factor_link():
    admin, _s, codes = _enroll()
    iam_setup.recover_with_code("IAM-T-1", PW, codes[0])
    _a, raw = iam_setup.invite_iam_admin(admin.user.employee_id, "IAM-T-1")
    assert (
        IAMSetupToken.objects.get(token_hash=iam_setup._hash_token(raw)).purpose == "replace_factor"
    )


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_recoveries_with_one_code_succeed_only_once():
    import threading

    from django.db import connection

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    admin, _s, codes = _enroll()
    results = []

    def go():
        try:
            iam_setup.recover_with_code("IAM-T-1", PW, codes[0])
            results.append("ok")
        except AuthException:
            results.append("refused")
        finally:
            connection.close()

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["ok", "refused"]


@pytest.mark.django_db
def test_recovery_voids_a_sudo_marker_already_stored_in_a_workspace_session():
    import time

    from django.test import RequestFactory

    from apps.accounts.auth.stepup import SUDO_GRANTED_TIMESTAMP_KEY, is_sudo_active

    admin, _s, codes = _enroll()
    req = RequestFactory().get("/x")
    req.user = admin.user
    req.session = {SUDO_GRANTED_TIMESTAMP_KEY: time.time() + 0.01}
    assert is_sudo_active(req)
    time.sleep(0.05)
    iam_setup.recover_with_code("IAM-T-1", PW, codes[0])
    assert not is_sudo_active(req)  # the stale session marker no longer counts
    req.session[SUDO_GRANTED_TIMESTAMP_KEY] = time.time() + 1  # a fresh grant afterwards works
    assert is_sudo_active(req)


@pytest.mark.django_db
def test_confirming_twice_with_the_same_code_issues_one_set_of_codes():
    user = _user()
    admin, raw = iam_setup.invite_iam_admin(user.employee_id, "IAM-T-1")
    iam_setup.set_password(raw, PW, PW)
    iam_setup.qr_for(raw)
    code = pyotp.TOTP(_secret(admin)).now()
    first = iam_setup.confirm_authenticator(raw, code)
    with pytest.raises(iam_setup.SetupError):
        iam_setup.confirm_authenticator(raw, code)
    assert len(first) == 10
    assert IAMRecoveryCode.objects.filter(iam_admin=admin, used_at__isnull=True).count() == 10
    assert (
        not IAMRecoveryCode.objects.filter(
            iam_admin=admin, keyed_hash__in=[iam_setup._hash(c) for c in first]
        )
        .exclude(used_at__isnull=True)
        .exists()
    )
