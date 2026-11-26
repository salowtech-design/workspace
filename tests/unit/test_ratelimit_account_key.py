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
