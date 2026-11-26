"""The activation page takes the employee id the way every other path stores it: trimmed and
upper case. On the real TiDB store (utf8mb4_bin columns) a case-insensitive lookup does not
exist, so a lower-case id with the right password used to find nobody there."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.accounts.views.activation import ACTIVATION_SESSION_KEY

PW = "Correct-Horse-9"


def _user(eid="AC-CASE-1"):
    u = CustomUser.objects.create_user(employee_id=eid, full_name="Case Person", password=PW)
    u.status, u.is_active = "active", True
    u.save()
    return u


@pytest.mark.django_db
@pytest.mark.parametrize("typed", ["AC-CASE-1", "ac-case-1", "Ac-Case-1", "  ac-case-1  "])
def test_activation_password_step_accepts_the_id_in_any_case(typed):
    u = _user()
    c = Client(HTTP_HOST="app.salowtech.com")
    r = c.post(reverse("accounts:activate"), {"employee_id": typed, "password": PW})
    assert r.status_code == 302 and r["Location"].endswith(reverse("accounts:activate_mfa_setup")), (typed, r.status_code, r.get("Location"))
    assert c.session[ACTIVATION_SESSION_KEY] == u.pk


@pytest.mark.django_db
def test_activation_password_step_still_refuses_wrong_password_and_unknown_id():
    _user()
    for eid, pw in (("ac-case-1", "wrong-password"), ("nobody-1", PW), ("", PW), ("ac-case-1", "")):
        c = Client(HTTP_HOST="app.salowtech.com")
        r = c.post(reverse("accounts:activate"), {"employee_id": eid, "password": pw})
        assert ACTIVATION_SESSION_KEY not in c.session, (eid, pw)
        assert r.status_code == 200


@pytest.mark.django_db
def test_activation_still_finds_a_legacy_row_stored_in_lower_case_when_typed_that_way():
    u = _user("AC-LEGACY-1")
    CustomUser.objects.filter(pk=u.pk).update(employee_id="ac-legacy-1")  # around save()
    c = Client(HTTP_HOST="app.salowtech.com")
    r = c.post(reverse("accounts:activate"), {"employee_id": "ac-legacy-1", "password": PW})
    assert r.status_code == 302 and c.session[ACTIVATION_SESSION_KEY] == u.pk
    c2 = Client(HTTP_HOST="app.salowtech.com")
    r = c2.post(reverse("accounts:activate"), {"employee_id": "ac-legacy-1", "password": "wrong-password"})
    assert r.status_code == 200 and ACTIVATION_SESSION_KEY not in c2.session


@pytest.mark.django_db
@pytest.mark.parametrize("typed", ["\x00", "a\x00b", "\u0131", "\u00df-1", "\U0001F600", "'; DROP TABLE x;--", "%", "_", " \t\n "])
def test_activation_malformed_ids_give_the_generic_page_never_a_fault_or_a_session(typed):
    _user()
    c = Client(HTTP_HOST="app.salowtech.com", raise_request_exception=False)
    r = c.post(reverse("accounts:activate"), {"employee_id": typed, "password": PW})
    assert r.status_code == 200 and ACTIVATION_SESSION_KEY not in c.session


@pytest.mark.django_db
def test_every_save_path_stores_upper_case_ids():
    u = CustomUser.objects.create_user(employee_id=" ac-save-1 ", full_name="S", password=PW)
    assert u.employee_id == "AC-SAVE-1"
    v = CustomUser(employee_id="ac-save-2", full_name="S2")
    v.set_unusable_password()
    v.save()
    assert CustomUser.objects.get(pk=v.pk).employee_id == "AC-SAVE-2"
    w, _ = CustomUser.objects.get_or_create(employee_id="ac-save-3", defaults={"full_name": "S3"})
    assert CustomUser.objects.get(pk=w.pk).employee_id == "AC-SAVE-3"


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RECORDED FINDING, behaviour not changed: the activation chain never looks at status. "
    "What this row proves is only the status flip: a blocked person with a usable password and no "
    "confirmed authenticator walks the steps and is set back to active. It does not assert the "
    "session; the protected-page check is a separate row. Which statuses may use activation is "
    "not decided, so this stays as a recorded expectation.",
)
@pytest.mark.parametrize("is_active", [True, False])
@pytest.mark.parametrize("status", ["suspended", "locked", "disabled", "exited", "inactive"])
def test_activation_chain_should_not_reactivate_a_blocked_person(status, is_active):
    # "inactive" is a model status value; is_active is a separate boolean, so both are covered.
    import pyotp

    from apps.accounts.auth.mfa_totp import get_or_create_pending_totp

    u = _user("AC-BLOCK-1")
    CustomUser.objects.filter(pk=u.pk).update(status=status, is_active=is_active)
    u.refresh_from_db()
    assert CustomUser.objects.get(pk=u.pk).status == status  # the status value is valid and stored
    c = Client(HTTP_HOST="app.salowtech.com")
    r = c.post(reverse("accounts:activate"), {"employee_id": "AC-BLOCK-1", "password": PW})
    if r.status_code == 302:
        secret, _, _ = get_or_create_pending_totp(u)
        c.post(reverse("accounts:activate_mfa_setup"), {"code": pyotp.TOTP(secret).now()})
        c.post(reverse("accounts:activate_recovery_codes"), {"confirmed_saved": "1"})
    u.refresh_from_db()
    assert u.status == status, "activation changed a blocked person's status"


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RECORDED FINDING, behaviour not changed: with is_active True the verified session the chain "
    "issues to a blocked person reaches a protected page. Only the is_active True rows are claimed; "
    "with is_active False an earlier probe showed the session was not honoured.",
)
@pytest.mark.parametrize("status", ["suspended", "locked", "disabled", "exited", "inactive"])
def test_activation_chain_session_of_a_blocked_active_person_reaches_no_protected_page(status):
    import pyotp

    from apps.accounts.auth.mfa_totp import get_or_create_pending_totp

    u = _user("AC-BLOCK-5")
    CustomUser.objects.filter(pk=u.pk).update(status=status, is_active=True)
    c = Client(HTTP_HOST="app.salowtech.com")
    assert c.post(reverse("accounts:activate"), {"employee_id": "AC-BLOCK-5", "password": PW}).status_code in (200, 302)
    secret, _, _ = get_or_create_pending_totp(u)
    c.post(reverse("accounts:activate_mfa_setup"), {"code": pyotp.TOTP(secret).now()})
    c.post(reverse("accounts:activate_recovery_codes"), {"confirmed_saved": "1"})
    assert c.get("/attendance/").status_code == 302, "a blocked person reached a protected page"


@pytest.mark.django_db
def test_activation_overlong_id_creates_no_session_and_fails_closed():
    """An id longer than any real one gets the normal page: the rate-limit key now uses a digest
    (a5b14f5), so the strict-column 503 no longer occurs. Detailed rows: test_ratelimit_account_key."""
    _user()
    c = Client(HTTP_HOST="app.salowtech.com", raise_request_exception=False)
    r = c.post(reverse("accounts:activate"), {"employee_id": "x" * 20000, "password": PW})
    # no redirect means no next activation step was granted (the session is not read here: on
    # TiDB the failed insert leaves the test transaction unusable)
    assert r.status_code == 200 and not r.has_header("Location")
