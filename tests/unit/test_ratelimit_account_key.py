"""The per-account rate-limit key is built from the typed employee id. A typed id longer than the
key column used to raise on a strict database (MySQL 1406) and the request answered 503."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.accounts.views.activation import ACTIVATION_SESSION_KEY
from apps.core.models import RateLimitBucket, RateLimitRecord


def _post(eid, ip="127.0.0.1", pw="x"):
    c = Client(HTTP_HOST="app.salowtech.com", REMOTE_ADDR=ip, raise_request_exception=False)
    return c, c.post(reverse("accounts:activate"), {"employee_id": eid, "password": pw})


@pytest.mark.django_db
def test_overlong_id_gets_the_normal_page_no_session_and_short_keys():
    c, r = _post("x" * 20000)
    assert r.status_code == 200 and not r.has_header("Location")
    assert ACTIVATION_SESSION_KEY not in c.session
    assert RateLimitRecord.objects.count() >= 2
    assert max(len(k) for k in RateLimitRecord.objects.values_list("key", flat=True)) <= 128
    assert max(len(k) for k in RateLimitBucket.objects.values_list("bucket_key", flat=True)) <= 191


@pytest.mark.django_db
def test_ids_that_share_a_long_prefix_do_not_collide_in_the_key():
    _post("A" * 200 + "1")
    _post("A" * 200 + "2")
    acc = set(RateLimitBucket.objects.filter(bucket_key__startswith="acc:").values_list("bucket_key", flat=True))
    assert len(acc) == 2, acc


@pytest.mark.django_db
def test_limits_still_count_per_ip_and_per_id_for_long_and_normal_ids():
    from apps.core.middleware import RateLimitMiddleware

    limit = RateLimitMiddleware.SENSITIVE_LIMIT
    for eid, ip, other in (("x" * 500, "10.1.1.1", "10.2.2.2"), ("ST-REAL-1", "10.5.5.5", "10.6.6.6")):
        codes = [_post(eid, ip=ip)[1].status_code for _ in range(limit + 1)]
        assert codes[:limit] == [200] * limit and codes[limit] == 429, (eid[:6], codes)
        # a different address is counted on its own bucket
        assert _post(eid, ip=other)[1].status_code == 200
    RateLimitBucket.objects.all().delete()
    # two different ids from the same address share the per-IP bucket, as before
    codes = [_post(f"id-{i}" + "y" * 40, ip="10.3.3.3")[1].status_code for i in range(limit + 1)]
    assert codes[-1] == 429


@pytest.mark.django_db
def test_real_length_ids_keep_their_plain_key():
    _post("ST-LIVE-9", ip="10.4.4.4")
    assert RateLimitBucket.objects.filter(bucket_key="acc:ST-LIVE-9:10.4.4.4:sensitive").count() == 1


@pytest.mark.django_db
def test_overlong_forwarded_client_address_from_a_trusted_proxy_does_not_overflow_the_key():
    """REMOTE_ADDR is a trusted proxy here, so its forwarded header is read. A header that is not an IP
    address falls back to the proxy's address (the key digest for long values stays as a second guard)."""
    c = Client(HTTP_HOST="app.salowtech.com", REMOTE_ADDR="127.0.0.1", raise_request_exception=False)
    r = c.post(reverse("accounts:activate"), {"employee_id": "ST-X-1", "password": "x"}, HTTP_CF_CONNECTING_IP="9" * 5000)
    assert r.status_code == 200 and not r.has_header("Location")
    assert ACTIVATION_SESSION_KEY not in c.session
    assert max(len(k) for k in RateLimitBucket.objects.values_list("bucket_key", flat=True)) <= 191
    # the malformed value is no longer used as the address at all: the proxy's own address is counted
    assert RateLimitBucket.objects.filter(bucket_key="127.0.0.1:sensitive").count() == 1
    # a normal forwarded address keeps its plain key
    c.post(reverse("accounts:activate"), {"employee_id": "ST-X-2", "password": "x"}, HTTP_CF_CONNECTING_IP="203.0.113.9")
    assert RateLimitBucket.objects.filter(bucket_key="203.0.113.9:sensitive").count() == 1


def _fwd_login(ip, header="HTTP_CF_CONNECTING_IP", remote="127.0.0.1", path="/auth/login/"):
    c = Client(HTTP_HOST="app.salowtech.com", REMOTE_ADDR=remote, raise_request_exception=False)
    r = c.post(path, {"employee_id": "ZZ-IP-1", "password": "bad"}, **{header: ip})
    return c, r


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["HTTP_CF_CONNECTING_IP", "HTTP_X_FORWARDED_FOR"])
@pytest.mark.parametrize("bad", ["9" * 5000, "not-an-ip", "1.2.3.4.5", "999.1.1.1", "<script>", "1.2.3.4 evil", "\x00", "fe80::1::2"])
def test_malformed_forwarded_address_never_faults_and_is_not_stored(header, bad):
    from apps.accounts.models import AuthEvent

    CustomUser.objects.create_user(employee_id="ZZ-IP-1", full_name="Z", password="Right-Pass-9")
    _, r = _fwd_login(bad, header)
    assert r.status_code == 200, (header, len(bad), r.status_code)
    stored = set(AuthEvent.objects.values_list("network_address", flat=True))
    assert all(v in (None, "127.0.0.1") for v in stored), stored
    # the limiter counted this request against the proxy's own address, so the key is plain
    assert RateLimitBucket.objects.filter(bucket_key="127.0.0.1:sensitive").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("good", ["203.0.113.9", "2001:db8::1", "::ffff:203.0.113.9"])
def test_valid_forwarded_address_from_a_trusted_proxy_is_still_used(good):
    CustomUser.objects.create_user(employee_id="ZZ-IP-1", full_name="Z", password="Right-Pass-9")
    _, r = _fwd_login(good)
    assert r.status_code == 200
    assert RateLimitBucket.objects.filter(bucket_key=f"{good}:sensitive").exists()
    _, r2 = _fwd_login(good, "HTTP_X_FORWARDED_FOR")
    assert r2.status_code in (200, 429)


@pytest.mark.django_db
def test_forwarded_header_from_an_untrusted_peer_is_still_ignored():
    CustomUser.objects.create_user(employee_id="ZZ-IP-1", full_name="Z", password="Right-Pass-9")
    _, r = _fwd_login("203.0.113.77", remote="198.51.100.5")
    assert r.status_code == 200
    assert RateLimitBucket.objects.filter(bucket_key="198.51.100.5:sensitive").exists()
    assert not RateLimitBucket.objects.filter(bucket_key__startswith="203.0.113.77").exists()


@pytest.mark.django_db
def test_per_ip_limit_still_applies_to_a_valid_forwarded_address_and_a_fallback_address():
    from apps.core.middleware import RateLimitMiddleware

    limit = RateLimitMiddleware.SENSITIVE_LIMIT
    for ip in ("203.0.113.50", "garbage"):
        codes = [_fwd_login(ip, path="/auth/activate/")[1].status_code for _ in range(limit + 1)]
        assert codes[-1] == 429, (ip, codes)
