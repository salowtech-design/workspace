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


def test_opening_marks_only_that_notice_read_and_goes_to_its_link():
    a, b = _u("IO-1"), _u("IO-2")
    n1 = notify(a, "x", "First", "/automation/")
    n2 = notify(a, "x", "Second", "/calendar/")
    nb = notify(b, "x", "Theirs", "/automation/")
    c = _c(a)
    page = c.get("/notifications/").content.decode()
    assert f'action="/notifications/open/{n1.pk}/"' in page and "Notifications (2)" in page
    assert c.get(f"/notifications/open/{n1.pk}/").status_code == 405  # a GET or prefetch never changes state
    n1.refresh_from_db()
    assert n1.read_at is None
    r = c.post(f"/notifications/open/{n1.pk}/")
    assert r.status_code == 302 and r["Location"] == "/automation/"
    n1.refresh_from_db(); n2.refresh_from_db(); nb.refresh_from_db()
    assert n1.read_at is not None and n2.read_at is None and nb.read_at is None
    assert "Notifications (1)" in c.get("/chat/").content.decode()
    first = n1.read_at
    c.post(f"/notifications/open/{n1.pk}/")
    n1.refresh_from_db()
    assert n1.read_at == first  # an already-read notice keeps its read time


def test_post_needs_the_csrf_token():
    a = _u("IO-5")
    n = notify(a, "x", "One", "/automation/")
    c = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)
    c.force_login(a)
    assert c.post(f"/notifications/open/{n.pk}/").status_code == 403
    n.refresh_from_db()
    assert n.read_at is None


def test_other_peoples_missing_inactive_and_unsafe_links():
    a, b = _u("IO-3"), _u("IO-4")
    nb = notify(b, "x", "Theirs", "/automation/")
    c = _c(a)
    assert c.post(f"/notifications/open/{nb.pk}/").status_code == 404
    assert c.post("/notifications/open/99999999/").status_code == 404
    nb.refresh_from_db()
    assert nb.read_at is None  # someone else's open did not touch it
    bad = Notification.objects.create(user=a, kind="x", title="Bad", link="//evil.example/x")
    none = Notification.objects.create(user=a, kind="x", title="No link", link="")
    r = c.post(f"/notifications/open/{bad.pk}/")
    assert r.status_code == 302 and r["Location"] == "/notifications/"
    assert c.post(f"/notifications/open/{none.pk}/")["Location"] == "/notifications/"
    page = c.get("/notifications/").content.decode()
    assert "evil.example" not in page
    assert Client(HTTP_HOST=HOST).post(f"/notifications/open/{bad.pk}/").status_code == 302  # login redirect
    a.status = "suspended"; a.save()
    assert c.post(f"/notifications/open/{bad.pk}/").status_code in (302, 403, 404)


@pytest.mark.parametrize("link", [
    "//evil.example/x", "/\\evil.example", "\\\\evil.example", "https://evil.example/x", "javascript:alert(1)",
    "/ok\tx", "/ok\nx", "/ok\x00x", "/ok x", " /ok", "evil.example", "/\x7fx", "/\x85x",
])
def test_stored_unsafe_links_never_redirect_outside(link):
    a = _u("IO-9")
    n = Notification.objects.create(user=a, kind="x", title="T", link=link)
    c = _c(a)
    r = c.post(f"/notifications/open/{n.pk}/")
    assert r.status_code == 302 and r["Location"] == "/notifications/"
    n.refresh_from_db()
    assert n.read_at is not None  # it is still marked read, just not followed
