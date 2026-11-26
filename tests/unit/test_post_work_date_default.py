"""The work log form prefills today's date in the app zone (Asia/Kolkata), not the UTC date; given dates are kept."""
import datetime as dt
import zoneinfo
from unittest import mock

import pytest
from django.utils import timezone

from apps.posts.forms import PostForm

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
FAKE_NOW = dt.datetime(2026, 11, 26, 19, 4, tzinfo=dt.timezone.utc)  # 27 Nov 00:34 IST


@pytest.mark.django_db
def test_new_log_form_prefills_local_date_after_midnight_ist():
    with timezone.override(IST), mock.patch("django.utils.timezone.now", return_value=FAKE_NOW):
        html = str(PostForm()["work_date"])
    assert 'value="2026-11-27"' in html, html


@pytest.mark.django_db
def test_given_work_date_is_kept():
    form = PostForm(initial={"work_date": dt.date(2026, 1, 5)})
    assert 'value="2026-01-05"' in str(form["work_date"])


@pytest.mark.django_db
def test_posted_work_date_is_kept_on_submit():
    from apps.posts.models import Post

    cat = list(Post.Category)[0].value
    form = PostForm(data={"title": "t", "category": cat, "content": "c", "work_date": "2026-01-05"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["work_date"] == dt.date(2026, 1, 5)


@pytest.mark.django_db
def test_existing_log_keeps_its_own_date():
    from apps.posts.models import Post

    form = PostForm(instance=Post(work_date=dt.date(2026, 1, 5)))
    with timezone.override(IST), mock.patch("django.utils.timezone.now", return_value=FAKE_NOW):
        html = str(form["work_date"])
    assert 'value="2026-01-05"' in html, html
