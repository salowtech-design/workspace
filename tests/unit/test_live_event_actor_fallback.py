"""Live feed actor: recorded actors stay, verified sign-ins name the actor, claims are labelled."""

import pytest
from django.utils import timezone

from apps.accounts.auth.live_events import public_payload
from apps.accounts.models import AuthEvent


def _evt(event, employee_id, actor=""):
    e = AuthEvent(
        event=event,
        code="INFO",
        step="s",
        employee_id=employee_id,
        actor_employee_id=actor,
        reference_id="ref",
    )
    e.time = timezone.now()
    return e


@pytest.mark.parametrize("domain", ["iam", "workspace"])
def test_successful_sign_in_names_the_identity_as_verified_actor(db, domain):
    p = public_payload(_evt("auth.terminal.success", "ID-1"), domain)
    assert (p["actor"], p["actor_state"], p["claimed_identity"]) == ("ID-1", "verified", "")


@pytest.mark.parametrize("domain", ["iam", "workspace"])
@pytest.mark.parametrize(
    "event",
    [
        "auth.requested",
        "auth.password_check.fail",
        "auth.mfa_verification.fail",
        "auth.terminal.failure",
        "totp_invalid",
        "password_verified",
        "activation_failed",
        "login_failed_wrong_password",
        "login_failed_user_not_found",
    ],
)
def test_failed_or_partial_sign_in_never_names_the_submitted_id_as_actor(db, domain, event):
    p = public_payload(_evt(event, "SOMEONE-ELSE"), domain)
    assert p["actor"] == ""
    assert p["actor_state"] == "claimed"
    assert p["claimed_identity"] == "SOMEONE-ELSE"


def test_unknown_id_is_a_claim_not_an_actor(db):
    p = public_payload(_evt("auth.password_check.fail", "NO-SUCH-ID"), "iam")
    assert p["actor"] == "" and p["claimed_identity"] == "NO-SUCH-ID"


def test_recorded_actor_is_never_replaced_or_relabelled(db):
    p = public_payload(_evt("auth.terminal.success", "IAM-A", actor="IAM-OTHER"), "iam")
    assert (p["actor"], p["actor_state"]) == ("IAM-OTHER", "recorded")
    p = public_payload(_evt("auth.password_check.fail", "IAM-A", actor="IAM-OTHER"), "iam")
    assert (p["actor"], p["actor_state"], p["claimed_identity"]) == ("IAM-OTHER", "recorded", "")


def test_non_sign_in_iam_row_without_actor_does_not_borrow_the_target(db):
    p = public_payload(_evt("iam_role_changed", "ST-EMP-0009"), "iam")
    assert p["actor"] == "" and p["actor_state"] == "none"


@pytest.mark.parametrize("domain", ["iam", "workspace"])
@pytest.mark.parametrize(
    "event",
    [
        "auth.session.revoked",
        "auth.policy.updated",
        "auth.lock.released",
        "auth.identity.created",
        "auth.device.revoked",
    ],
)
def test_authenticated_control_events_are_never_shown_as_sign_in_claims(db, domain, event):
    recorded = public_payload(_evt(event, "ADM-1", actor="ADM-1"), domain)
    assert (recorded["actor"], recorded["actor_state"], recorded["claimed_identity"]) == (
        "ADM-1",
        "recorded",
        "",
    )
    unrecorded = public_payload(_evt(event, "ADM-1"), domain)
    assert unrecorded["actor_state"] != "claimed" and unrecorded["claimed_identity"] == ""
    # No identity is manufactured for the IAM domain when no actor was recorded.
    if domain == "iam":
        assert unrecorded["actor"] == ""


def test_revoke_session_records_the_acting_admin_as_actor(db):
    from django.contrib.auth.hashers import make_password

    from apps.accounts.auth.authentication_centre import execute_revoke_session
    from apps.accounts.auth.iam_auth import create_iam_admin_identity
    from apps.accounts.models import CustomUser, IAMSession

    user = CustomUser.objects.create(
        employee_id="ST-ACT1",
        full_name="ACT1",
        email="st-act1@salowtech.com",
        password=make_password("SyntheticPass2026!@#x"),
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    admin, _ = create_iam_admin_identity(
        user=user,
        admin_id="IAM-ACT-01",
        password="IamActorSecretPassword2026!#",
        role="super_admin",
    )
    target = IAMSession.objects.create(
        session_key="k" * 64,
        iam_admin=admin,
        is_active=True,
    )
    execute_revoke_session(target.session_key, "iam", admin, "drill")
    ev = AuthEvent.objects.get(event="auth.session.revoked")
    assert ev.actor_employee_id == "IAM-ACT-01"
    p = public_payload(ev, "iam")
    assert (p["actor"], p["actor_state"], p["claimed_identity"]) == ("IAM-ACT-01", "recorded", "")


def test_iam_sessions_list_template_uses_real_fields():
    src = open("templates/accounts/admin/auth_dashboard_iam.html", encoding="utf-8").read()
    assert "s.expires_at" not in src  # IAMSession has no such field, it rendered blank
    assert "s.last_activity" in src and 'slice:":12"' in src
