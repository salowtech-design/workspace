"""Live activity feed limit parameter must be validated."""
from unittest import mock

import pytest
from django.test import RequestFactory

from apps.accounts.views import authentication_centre as view_mod


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 20), ("", 20), ("abc", 20), ("5", 5), ("0", 1), ("-3", 1), ("100000", 100), ("100", 100)],
)
def test_parse_feed_limit(raw, expected):
    assert view_mod._parse_feed_limit(raw) == expected


def test_feed_view_with_non_numeric_limit_does_not_500():
    request = RequestFactory().get("/iam/auth/feed/", {"domain": "workspace", "limit": "abc"})
    with mock.patch.object(view_mod, "get_live_activity_feed", return_value=[]) as feed, \
            mock.patch.object(view_mod, "render") as render:
        view_mod.auth_live_activity_feed_view(request)
    feed.assert_called_once_with("workspace", limit=20)
    assert render.called
