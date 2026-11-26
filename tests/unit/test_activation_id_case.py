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
    reason="RECORDED FINDING, behaviour not changed: the activation chain never looks at status. "
    "A suspended or locked person who has a usable password and no confirmed authenticator "
    "walks the steps, is set to active and gets a verified session. Which statuses may use "
    "activation is not decided, so this stays as a recorded expectation.",
)
@pytest.mark.parametrize("status", ["suspended", "locked", "disabled", "exited", "inactive"])
def test_activation_chain_should_not_reactivate_a_blocked_person(status):
    import pyotp

    from apps.accounts.auth.mfa_totp import get_or_create_pending_totp

    u = _user("AC-BLOCK-1")
    CustomUser.objects.filter(pk=u.pk).update(status=status)
    u.refresh_from_db()
    c = Client(HTTP_HOST="app.salowtech.com")
    r = c.post(reverse("accounts:activate"), {"employee_id": "AC-BLOCK-1", "password": PW})
    if r.status_code == 302:
        secret, _, _ = get_or_create_pending_totp(u)
        c.post(reverse("accounts:activate_mfa_setup"), {"code": pyotp.TOTP(secret).now()})
        c.post(reverse("accounts:activate_recovery_codes"), {"confirmed_saved": "1"})
    u.refresh_from_db()
    assert u.status == status


@pytest.mark.django_db
def test_activation_overlong_id_creates_no_session_and_fails_closed():
    """RECORDED: an id longer than the rate-limit key column makes the rate-limit middleware
    raise on the real TiDB store (strict mode), and it answers 503 (fails closed). SQLite
    accepts the long key and the page answers 200. Either way no session is created. The
    middleware is not changed here."""
    _user()
    c = Client(HTTP_HOST="app.salowtech.com", raise_request_exception=False)
    r = c.post(reverse("accounts:activate"), {"employee_id": "x" * 20000, "password": PW})
    assert r.status_code in (200, 503) and ACTIVATION_SESSION_KEY not in c.session
