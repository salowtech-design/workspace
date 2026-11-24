"""Sensitive-endpoint throttling is per IP and per account+IP, so an attacker cannot burn a victim's quota."""

import pytest
from django.test import Client

from apps.core.models import RateLimitBucket


def _post(ip, emp):
    c = Client(HTTP_HOST="localhost", REMOTE_ADDR=ip)
    return c.post(
        "/auth/login/", {"employee_id": emp, "password": "wrong-pass-1", "totp_code": "000000"}
    )


@pytest.mark.django_db
def test_attacker_cannot_exhaust_victims_quota_from_another_ip():
    for _ in range(12):
        _post("203.0.113.9", "ST-VICTIM-01")
    attacker = _post("203.0.113.9", "ST-VICTIM-01")
    assert attacker.status_code == 429  # attacker's own IP is throttled
    victim = _post("198.51.100.7", "ST-VICTIM-01")
    assert victim.status_code != 429  # the victim from a different address is not


@pytest.mark.django_db
def test_per_ip_limit_still_applies_across_many_account_ids():
    codes = [_post("203.0.113.50", f"ST-SPRAY-{i:02d}").status_code for i in range(10)]
    assert 429 in codes  # spraying many IDs from one IP is stopped by the per-IP bucket


@pytest.mark.django_db
def test_bucket_keys_are_account_and_ip_scoped():
    _post("203.0.113.77", "ST-KEYS-01")
    keys = set(RateLimitBucket.objects.values_list("bucket_key", flat=True))
    assert "acc:ST-KEYS-01:203.0.113.77:sensitive" in keys
    assert not any(k == "acc:ST-KEYS-01:sensitive" for k in keys)
