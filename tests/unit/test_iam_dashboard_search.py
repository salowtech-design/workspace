"""The IAM console dashboard search box (?q=) filters the people list and the sign-in audit list."""

import pytest

from apps.accounts.models import AuthEvent, CustomUser
from tests.unit.test_admin_mfa_reset_level3 import HOST, _mk
from tests.unit.test_iam_platform_complete import _iam_console_client

pytestmark = pytest.mark.django_db


def test_dashboard_search_returns_the_page_and_filters_the_audit_list():
    a = _mk("ST-SRCH-A", CustomUser.Role.SUPER_ADMIN)
    c, _s = _iam_console_client(a, "issue_mfa_reset")
    AuthEvent.objects.create(event="iam_search_probe", employee_id="ST-SRCH-HIT")
    AuthEvent.objects.create(event="iam_search_probe", employee_id="OTHER-PERSON")
    r_all = c.get("/iam/dashboard/", HTTP_HOST=HOST)
    assert r_all.status_code == 200
    r = c.get("/iam/dashboard/?q=ST-SRCH", HTTP_HOST=HOST)
    assert r.status_code == 200
    ids = {e.employee_id for e in r.context["audit_events"]}
    assert "ST-SRCH-HIT" in ids and "OTHER-PERSON" not in ids, ids
    assert {e.employee_id for e in r_all.context["audit_events"]} >= {"ST-SRCH-HIT", "OTHER-PERSON"}
