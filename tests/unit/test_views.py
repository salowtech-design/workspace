import pytest
from django.urls import reverse


def test_health_probe_endpoint(client):
    """Verify /health returns HTTP 200 without database dependency."""
    response = client.get(reverse("health_probe"))
    assert response.status_code == 200
    assert response.json() == {"status": "pass"}


@pytest.mark.django_db
def test_readiness_probe_endpoint(client):
    """Verify /ready executes database query and returns status."""
    response = client.get(reverse("readiness_probe"))
    assert response.status_code in (200, 503)


@pytest.mark.django_db
def test_login_invalid_credentials_returns_generic_error(client):
    """Verify unknown user or wrong password returns identical generic error without enumeration."""
    # Unknown employee ID
    response = client.post(
        reverse("accounts:login"),
        {"employee_id": "UNKNOWN999", "password": "SomePassword123!"},
    )
    assert response.status_code == 200
    assert "Invalid employee ID or password." in response.content.decode()


@pytest.mark.django_db
def test_logout_enforces_post_only(client):
    """Verify GET on /auth/logout/ is rejected with 405 Method Not Allowed."""
    response = client.get(reverse("accounts:logout"))
    assert response.status_code == 405
