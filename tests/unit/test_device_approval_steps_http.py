"""Second-device approval step order over HTTP, with no sign-in (the pages are reached by request id; see the wait-page
finding in the reports, which this test does not change or approve). Rows are created directly with a future expiry.
  - pending request: the email-code page and the face page both send the visitor to the login page;
  - approved request, code not yet verified: the email-code page opens (control); the face page still goes to login;
  - a wrong email code stays on the code page with its error, leaves email_otp_verified False, and the face page still goes
    to login; no signed-in session exists after any of these requests;
  - email_otp_verified True (set directly as the control): the face page no longer sends the visitor to login.
Limits: test_step_order uses GET and one wrong-code POST; the later tests add the correct code, the 5-minute window after
approval and the lock after 5 wrong codes. The face check itself and the 60 s request expiry are not exercised; test client without CSRF; one machine, one run; not an authorization proof."""

import datetime as dt

from django.test import Client
from django.utils import timezone

from apps.accounts.models import CustomUser, DeviceApprovalRequest

HOST = "app.salowtech.com"


def _req(status):
    u = CustomUser.objects.create(employee_id="DV-1", full_name="Dv One", email="dv1@salowtech.com", role="employee",
                                  status="active", is_active=True)  # fmt: skip
    return DeviceApprovalRequest.objects.create(user=u, status=status, requesting_device_ip="10.9.9.9",
                                                expires_at=timezone.now() + dt.timedelta(minutes=10))  # fmt: skip


def _urls(r):
    base = f"/auth/devices/approval/{r.request_id}"
    return base + "/email-otp/", base + "/face/"


def _goes_to_login(resp):
    return resp.status_code in (301, 302) and "login" in resp["Location"]


def test_step_order(db):
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR="10.8.0.1")
    r = _req(DeviceApprovalRequest.Status.PENDING)
    otp, face = _urls(r)
    assert _goes_to_login(c.get(otp)) and _goes_to_login(c.get(face))
    r.status = DeviceApprovalRequest.Status.APPROVED
    r.save()
    page = c.get(otp)
    assert page.status_code == 200 and b"Email Verification Challenge" in page.content, "control: the code page opens once approved"
    assert _goes_to_login(c.get(face)), "face page must not open before the code is verified"
    wrong = c.post(otp, {"email_otp": "000000"})
    assert wrong.status_code == 200 and b"Invalid or expired email OTP" in wrong.content
    r.refresh_from_db()
    assert r.email_otp_verified is False and r.completed_at is None
    assert _goes_to_login(c.get(face))
    assert "_auth_user_id" not in c.session
    r.email_otp_verified = True
    r.save()
    after = c.get(face)
    assert after.status_code == 200 and b"Biometric Face Verification" in after.content, (
        f"control: face page should open once the code is verified (status {after.status_code})"
    )
    assert "_auth_user_id" not in c.session, "a GET of the face page must not sign anyone in"


# ---- correct code, 5-minute window and lockout (added later; same limits: no face check, test client without CSRF) ----
import hashlib  # noqa: E402
import hmac  # noqa: E402

from apps.accounts.auth.config import get_activation_hmac_key  # noqa: E402


def _approved(eid, age_minutes):
    u = CustomUser.objects.create(employee_id=eid, full_name=eid, email=f"{eid.lower()}@salowtech.com", role="employee",
                                  status="active", is_active=True)  # fmt: skip
    now = timezone.now()
    h = hmac.new(get_activation_hmac_key(), b"123456", hashlib.sha256).hexdigest()
    return DeviceApprovalRequest.objects.create(user=u, status=DeviceApprovalRequest.Status.APPROVED, requesting_device_ip="10.9.9.9",
                                                expires_at=now + dt.timedelta(minutes=10), approved_at=now - dt.timedelta(minutes=age_minutes),
                                                email_otp_hash=h)  # fmt: skip


def test_correct_code_inside_five_minutes_opens_the_face_step(db):
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR="10.8.0.2")
    r = _approved("DV-OK", 1)
    otp, face = _urls(r)
    resp = c.post(otp, {"email_otp": "123456"})
    assert resp.status_code == 302 and resp["Location"] == face
    r.refresh_from_db()
    assert r.email_otp_verified is True and r.completed_at is None
    assert "_auth_user_id" not in c.session


def test_correct_code_after_five_minutes_is_refused(db):
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR="10.8.0.3")
    r = _approved("DV-OLD", 6)  # same code as the passing control above, approved 6 minutes ago
    otp, face = _urls(r)
    resp = c.post(otp, {"email_otp": "123456"})
    assert resp.status_code == 200 and b"Invalid or expired email OTP" in resp.content
    r.refresh_from_db()
    assert r.email_otp_verified is False and _goes_to_login(c.get(face))


def test_five_wrong_codes_lock_the_request_and_the_page_says_so(db):
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR="10.8.0.4")
    r = _approved("DV-LOCK", 1)
    otp, face = _urls(r)
    for _ in range(5):
        wrong = c.post(otp, {"email_otp": "000000"})
        assert wrong.status_code == 200 and b"Invalid or expired email OTP" in wrong.content
    locked = c.post(otp, {"email_otp": "123456"})  # the CORRECT code, after five failures
    assert locked.status_code == 200 and b"locked due to consecutive failures" in locked.content
    assert b"Reference:" not in locked.content  # the page shows the plain lock message only
    r.refresh_from_db()
    assert r.email_otp_verified is False and _goes_to_login(c.get(face))
    assert "_auth_user_id" not in c.session


def test_a_non_lock_auth_error_is_not_shown_on_the_code_page(db, monkeypatch):
    from apps.accounts.auth.errors import AuthException
    from apps.accounts.views import device_approval as dv

    def boom(request_id, otp):
        raise AuthException("AUTH-DEV-999", step="x", message_override="internal detail SECRET-MARK")

    monkeypatch.setattr(dv, "verify_device_email_otp", boom)
    r = _approved("DV-EXC", 1)
    otp, _face = _urls(r)
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR="10.8.0.5", raise_request_exception=False)
    resp = c.post(otp, {"email_otp": "123456"})
    assert resp.status_code == 500 and b"SECRET-MARK" not in resp.content
