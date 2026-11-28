"""Second-device approval step order over HTTP, with no sign-in (the pages are reached by request id; see the wait-page
finding in the reports, which this test does not change or approve). Rows are created directly with a future expiry.
  - pending request: the email-code page and the face page both send the visitor to the login page;
  - approved request, code not yet verified: the email-code page opens (control); the face page still goes to login;
  - a wrong email code stays on the code page with its error, leaves email_otp_verified False, and the face page still goes
    to login; no signed-in session exists after any of these requests;
  - email_otp_verified True (set directly as the control): the face page no longer sends the visitor to login.
Limits: GET and one wrong-code POST only; the correct code, the face check itself, lockout after 5 failures and the 60 s
expiry are not exercised; test client without CSRF; one machine, one run; not an authorization proof."""

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
