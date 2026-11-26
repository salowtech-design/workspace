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
