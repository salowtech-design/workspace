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
