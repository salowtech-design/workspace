import pytest
from django.test import Client

from apps.accounts.models import CustomUser
from apps.core.foundation.notify import notify
from apps.core.models import Notification

HOST = "app.salowtech.com"
pytestmark = pytest.mark.django_db


def _u(eid, status="active"):
    return CustomUser.objects.create_user(
        employee_id=eid, full_name=f"P {eid}", password="ValidStrongPassword123!",
        role="employee", status=status,
    )  # fmt: skip


def _c(u):
    c = Client(HTTP_HOST=HOST)
    c.force_login(u)
    return c


def test_only_own_notifications_with_count_in_nav_and_mark_read():
    a, b = _u("IN-1"), _u("IN-2")
    notify(a, "x", "Mine <b>one</b>", "/automation/")
    notify(b, "x", "Not yours")
    c = _c(a)
    page = c.get("/notifications/").content.decode()
    assert "Mine &lt;b&gt;one" in page and "Not yours" not in page
    assert "Notifications (1)" in c.get("/chat/").content.decode()
    assert c.post("/notifications/read/").status_code == 302
    assert Notification.objects.filter(user=a, read_at__isnull=True).count() == 0
    assert Notification.objects.filter(user=b, read_at__isnull=True).count() == 1
    assert "Notifications (" not in c.get("/chat/").content.decode()


def test_needs_sign_in_workspace_host_and_active_status():
    assert Client(HTTP_HOST=HOST).get("/notifications/").status_code == 302
    a = _u("IN-3")
    assert _c(a).get("/notifications/", HTTP_HOST="iam.salowtech.com").status_code == 404
    a.status = "suspended"
    a.save()
    assert _c(a).get("/notifications/").status_code in (302, 404)
    c = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)
    c.force_login(_u("IN-4"))
    assert c.post("/notifications/read/").status_code == 403
