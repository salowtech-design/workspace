"""The issue form must prefill today's date in the app's time zone (Asia/Kolkata), not the UTC date."""
import datetime as dt
from unittest import mock

import pytest

from apps.certificates.forms import CertificateIssueForm


@pytest.mark.django_db
def test_issue_form_prefills_local_date_after_midnight_ist():
    # 2026-11-26 19:04 UTC is 2026-11-27 00:34 in Asia/Kolkata
    fake_now = dt.datetime(2026, 11, 26, 19, 4, tzinfo=dt.timezone.utc)
    with mock.patch("django.utils.timezone.now", return_value=fake_now):
        html = str(CertificateIssueForm()["issue_date"])
    assert 'value="2026-11-27"' in html, html


@pytest.mark.django_db
def test_issue_form_keeps_a_date_the_user_posted():
    form = CertificateIssueForm(initial={"issue_date": dt.date(2026, 1, 5)})
    assert 'value="2026-01-05"' in str(form["issue_date"])
