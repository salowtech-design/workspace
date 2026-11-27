import csv
import datetime as dt
import io
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from apps.attendance import services as at
from apps.attendance.models import WorkSession
from tests.unit.test_attendance_integrity import OK, _u

HOST = "app.salowtech.com"


def _rows(resp):
    return list(csv.reader(io.StringIO(resp.content.decode())))


@pytest.mark.django_db
def test_csv_is_my_own_month_matching_the_page_and_nothing_else():
    me, other = _u("AT-C1"), _u("AT-C2")
    kol = ZoneInfo("Asia/Kolkata")
    day = dt.datetime(2026, 10, 14, 9, 30, tzinfo=kol)
    at.check_in(me, OK, instant=day)
    at.check_out(me, instant=day + timedelta(hours=8, minutes=15))
    at.check_in(me, OK, instant=day + timedelta(days=1))  # left open
    at.check_in(other, OK, instant=day)
    c = Client(HTTP_HOST=HOST)
    c.force_login(me)
    r = c.get("/attendance/history/download/?month=2026-10")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert "attachment" in r["Content-Disposition"] and "2026-10" in r["Content-Disposition"]
    assert r["Cache-Control"] == "private, no-store"
    rows = _rows(r)
    assert rows[0][:2] == ["Date", "Check in (Asia/Kolkata)"] and len(rows) == 3  # header plus my 2 sessions
    assert rows[1][:4] == ["2026-10-14", "09:30", "17:45", "8.25"]
    assert rows[2][0] == "2026-10-15" and rows[2][3] == ""
    assert "AT-C2" not in r.content.decode() and "P AT-C" not in r.content.decode()
    # a user id in the query is ignored: still only my rows
    r2 = c.get("/attendance/history/download/", {"month": "2026-10", "user": other.pk, "user_id": other.pk})
    assert _rows(r2) == rows
    # the page and the file agree on the number of rows
    assert c.get("/attendance/history/?month=2026-10").content.decode().count("cal-row") == len(rows) - 1
    assert len(_rows(c.get("/attendance/history/download/?month=2026-09"))) == 1  # header only


@pytest.mark.django_db
def test_csv_refuses_bad_month_anonymous_inactive_and_neutralises_formulas():
    me = _u("AT-C3")
    c = Client(HTTP_HOST=HOST)
    assert c.get("/attendance/history/download/").status_code == 302
    c.force_login(me)
    for bad in ("2026-13", "abc", "2026-10-01", "1999-01"):
        assert c.get("/attendance/history/download/", {"month": bad}).status_code == 400
    assert c.get("/attendance/history/download/", HTTP_HOST="iam.salowtech.com").status_code == 404
    kol = ZoneInfo("Asia/Kolkata")
    day = dt.datetime(2026, 10, 14, 9, 30, tzinfo=kol)
    at.check_in(me, OK, instant=day)
    # method/state labels are fixed choices, so a formula cell can only come from a future label change;
    # the helper is pinned directly through a patched label
    from unittest import mock

    with mock.patch.object(WorkSession, "get_verification_method_display", return_value="=cmd|x"):
        body = c.get("/attendance/history/download/?month=2026-10").content.decode()
    assert "'=cmd|x" in body and ",=cmd" not in body
    me.status = "suspended"
    me.save()
    assert c.get("/attendance/history/download/").status_code in (302, 403, 404)
