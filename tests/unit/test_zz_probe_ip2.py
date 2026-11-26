import pytest
from django.test import Client
from apps.accounts.models import CustomUser, AuthEvent

CASES = ["0:0:0:0:0:ffff:255.255.255.255", "0000:0000:0000:0000:0000:ffff:255.255.255.255", "fe80::1%eth0", "fe80::1%1", "2001:0db8:0000:0000:0000:0000:0000:0001", "::ffff:203.0.113.9"]

@pytest.mark.django_db
@pytest.mark.parametrize("ip", CASES)
def test_probe(ip):
    import ipaddress
    CustomUser.objects.create_user(employee_id="ZZ-1", full_name="Z", password="Right-Pass-9")
    c = Client(HTTP_HOST="app.salowtech.com", REMOTE_ADDR="127.0.0.1", raise_request_exception=False)
    r = c.post("/auth/login/", {"employee_id": "ZZ-1", "password": "bad"}, HTTP_CF_CONNECTING_IP=ip)
    try:
        ipaddress.ip_address(ip); parsed = True
    except ValueError:
        parsed = False
    stored = list(AuthEvent.objects.values_list("network_address", flat=True))
    assert r.status_code == 200, (ip, len(ip), "parses" if parsed else "invalid", r.status_code, stored)
