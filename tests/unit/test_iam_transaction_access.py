"""IAM authentication transaction detail (iam host, route name iam:auth_transaction_detail).

Seeds one transaction with a marker in its identity reference and a secret-looking value in a stage's metadata, then:
  - anonymous, a signed-in workspace employee, and a signed-in workspace super admin (no IAM session) get a redirect to
    the IAM sign-in for a real id and for an unknown id, with no marker in body or headers, and the two answers match in
    status and body (so the response does not tell whether the id exists);
  - an IAM admin with a real IAM session (positive control) gets 200 showing the transaction and its stages, but not the
    stage metadata value, and gets 404 for an unknown id.
Limits: one IAM admin, one employee role per case, test client without CSRF, GET only; the existing
tests/unit/test_iam_investigation_transactions.py covers the redirect without a marker or an exists-or-not comparison.
One machine, one run; this does not test the other IAM console routes."""

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.auth.iam_auth import IAM_SESSION_COOKIE_NAME, create_iam_admin_identity, create_iam_session
from apps.accounts.models import AuthenticationTransaction, CustomUser, IAMAdminIdentity

IAM = "iam.salowtech.com"
MARK = "ZqTxMarker"
SECRET = "ZqStageSecretValue"


def _person(eid, role):
    return CustomUser.objects.create_user(employee_id=eid, full_name=f"{eid} Person", password="ValidPass2026!@#",
                                          role=role, status=CustomUser.Status.ACTIVE)  # fmt: skip


@pytest.fixture
def world(db):
    tx = AuthenticationTransaction.objects.create(
        transaction_id="tx-zq-real-1", correlation_id="corr-zq", domain="iam", identity_ref=f"{MARK}-IAM-ID", stage="completed",
        outcome="success",
        stages=[{"stage": "password_check", "result": "pass", "reason": "ok", "timestamp": "2026-11-28T03:00:56+05:30",
                 "duration_ms": 3.2, "metadata": {"secret": SECRET}}],
    )  # fmt: skip
    admin_person = _person("ZQ-IAM-ADM", CustomUser.Role.SUPER_ADMIN)
    ident, _totp = create_iam_admin_identity(user=admin_person, admin_id="IAM-ZQ-01", password="Strong16CharMasterPass!",
                                             role=IAMAdminIdentity.Role.SUPER_ADMIN)  # fmt: skip
    req = RequestFactory().get("/", HTTP_HOST=IAM)
    create_iam_session(ident, req, assurance_level=1)
    return {"tx": tx, "token": req.session_key_iam, "employee": _person("ZQ-EMP-1", CustomUser.Role.EMPLOYEE),
            "super": _person("ZQ-SUP-1", CustomUser.Role.SUPER_ADMIN)}  # fmt: skip


def _leaks(r):
    headers = " ".join(f"{k}: {v}" for k, v in r.headers.items())
    return any(m in r.content.decode("utf-8", "replace") + headers for m in (MARK, SECRET))


def test_no_iam_session_gets_the_same_redirect_for_real_and_unknown_ids(world):
    w = world
    real = reverse("iam:auth_transaction_detail", args=[w["tx"].transaction_id])
    unknown = reverse("iam:auth_transaction_detail", args=["tx-zq-nope-9"])
    for who in (None, w["employee"], w["super"]):
        c = Client(HTTP_HOST=IAM, REMOTE_ADDR="10.5.0.1")
        if who is not None:
            c.force_login(who)
        a, b = c.get(real), c.get(unknown)
        assert a.status_code == b.status_code == 302, (who, a.status_code, b.status_code)
        assert "login" in a["Location"] and "login" in b["Location"]
        assert a.content == b.content, "body differs between a real and an unknown id"
        assert not _leaks(a) and not _leaks(b)


def test_iam_admin_session_sees_the_transaction_but_not_stage_metadata(world):
    w = world
    c = Client(HTTP_HOST=IAM, REMOTE_ADDR="10.5.0.2")
    c.cookies[IAM_SESSION_COOKIE_NAME] = w["token"]
    c.cookies["salow_iam_session"] = w["token"]  # the non-secure-cookie name used outside HTTPS
    ok = c.get(reverse("iam:auth_transaction_detail", args=[w["tx"].transaction_id]))
    body = ok.content.decode()
    assert ok.status_code == 200, (ok.status_code, ok.headers.get("Location"))
    assert w["tx"].transaction_id in body and "PASSWORD_CHECK" in body, "positive control: the admin should see it"
    assert SECRET not in body, "stage metadata value is shown"
    assert c.get(reverse("iam:auth_transaction_detail", args=["tx-zq-nope-9"])).status_code == 404
