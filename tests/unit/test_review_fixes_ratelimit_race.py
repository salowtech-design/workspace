"""Found by the TiDB concurrency run: two simultaneous first requests for a rate-limit key raced on the
unique bucket_key, logged a database error and (on sensitive endpoints) returned a spurious 503."""
from datetime import timedelta
from unittest import mock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.core.middleware import RateLimitMiddleware
from apps.core.models import RateLimitBucket


class _MissesOnce:
    """Simulates the loser of the race: the first read sees no row although the winner has just inserted it."""

    def __init__(self, real):
        self.real, self.first_call = real, True

    def filter(self, **kw):
        outer = self

        class _Q:
            def first(self_inner):
                if outer.first_call:
                    outer.first_call = False
                    return None
                return outer.real.filter(**kw).first()

        return _Q()

    def get(self, **kw):
        return self.real.get(**kw)


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=False)
def test_first_request_race_counts_against_winner_row_and_does_not_503():
    RateLimitMiddleware.reset()
    req = RequestFactory().post("/auth/login/", {"employee_id": "RACE-1"}, REMOTE_ADDR="198.51.100.9")
    key = "198.51.100.9:sensitive"
    RateLimitBucket.objects.create(bucket_key=key, count=1, reset_at=timezone.now() + timedelta(seconds=60))
    mw = RateLimitMiddleware(lambda r: HttpResponse("ok"))
    with mock.patch.object(RateLimitBucket.objects, "select_for_update", side_effect=lambda: _MissesOnce(RateLimitBucket.objects)):
        resp = mw(req)
    assert resp.status_code == 200
    assert RateLimitBucket.objects.get(bucket_key=key).count == 2
