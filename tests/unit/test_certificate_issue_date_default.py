"""The issue and reissue forms must prefill today's date in the app's time zone (Asia/Kolkata), not the UTC date.

The zone is pinned in each test with timezone.override, so the result does not depend on settings.TIME_ZONE.
"""
import datetime as dt
import zoneinfo
from unittest import mock

import pytest
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.certificates.forms import CertificateIssueForm
from apps.certificates.services import issue_certificate, revoke_certificate
from apps.certificates.views import certificate_issue_view

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
# 2026-11-26 19:04 UTC is 2026-11-27 00:34 in Asia/Kolkata
FAKE_NOW = dt.datetime(2026, 11, 26, 19, 4, tzinfo=dt.timezone.utc)


@pytest.mark.django_db
def test_issue_form_prefills_local_date_after_midnight_ist():
    with timezone.override(IST), mock.patch("django.utils.timezone.now", return_value=FAKE_NOW):
        html = str(CertificateIssueForm()["issue_date"])
    assert 'value="2026-11-27"' in html, html


@pytest.mark.django_db
def test_issue_form_keeps_a_date_the_user_posted():
    form = CertificateIssueForm(initial={"issue_date": dt.date(2026, 1, 5)})
    assert 'value="2026-01-05"' in str(form["issue_date"])


@pytest.mark.django_db
def test_reissue_page_prefills_local_date_and_the_old_values():
    admin = CustomUser.objects.create_superuser(employee_id="ST-ADM-0099", full_name="Admin", password="SuperMasterAdminPassword2026!")
    who = CustomUser.objects.create_user(employee_id="ST-INT-0099", full_name="Grace Hopper", password="ValidPassword123!", role=CustomUser.Role.INTERN, status=CustomUser.Status.ACTIVE)
    old = revoke_certificate(certificate=issue_certificate(recipient=who, title="Original", description="Citation", issued_by=admin), revoked_by=admin, reason="Wrong name")
    req = RequestFactory().get("/certificates/issue/", {"reissue": str(old.uuid)})
    req.user = admin
    with timezone.override(IST), mock.patch("django.utils.timezone.now", return_value=FAKE_NOW):
        html = certificate_issue_view(req).content.decode()
    assert "Reissuing" in html and "Original" in html  # the old title is carried over
    assert 'name="issue_date"' in html and 'value="2026-11-27"' in html
