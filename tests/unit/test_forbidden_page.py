import pytest
from django.test import Client

from apps.accounts.models import CustomUser


@pytest.mark.django_db
def test_a_403_renders_the_styled_page_and_keeps_the_status_and_hides_the_reason():
    u = CustomUser.objects.create(
        employee_id="FB-1", full_name="Plain Person", email="fb-1@salowtech.com",
        role="employee", status="active", is_active=True,
    )  # fmt: skip
    c = Client(HTTP_HOST="app.salowtech.com")
    c.force_login(u)
    r = c.get("/attendance/admin/")
    body = r.content.decode()
    assert r.status_code == 403
    assert "You don't have access to this page" in body
    assert "Back to home" in body
    assert "<html" in body and "/static/css/tokens.css" in body  # the app layout, not a bare page
    assert "Forbidden" not in body
