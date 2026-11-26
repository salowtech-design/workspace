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


def test_business_audit_filters_actor_action_dates_and_bad_input():
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.models import AuditLog

    a = _mk("ST-AUD-A", CustomUser.Role.SUPER_ADMIN)
    b = _mk("ST-AUD-B", CustomUser.Role.ADMIN)
    old = AuditLog.objects.create(actor=b, action="Policy.Old", target_type="t", target_id="1")
    AuditLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))  # fixture only
    AuditLog.objects.create(actor=b, action="Policy.Changed", target_type="t", target_id="2")
    AuditLog.objects.create(actor=a, action="Device.Revoked", target_type="d", target_id="3")
    AuditLog.objects.create(actor=None, action="System.Tick", target_type="s", target_id="4")
    c, _s = _iam_console_client(a, "issue_mfa_reset")

    def acts(q):
        r = c.get("/iam/dashboard/?tab=audit" + q, HTTP_HOST=HOST)
        assert r.status_code == 200, q
        return sorted(x.action for x in r.context["business_audits"] if x.action != "iam_search_probe"), r

    allacts, r = acts("")
    assert {"Policy.Old", "Policy.Changed", "Device.Revoked", "System.Tick"} <= set(allacts)
    assert acts("&b_actor=st-aud-b")[0] == ["Policy.Changed", "Policy.Old"]  # case-blind
    assert acts("&b_action=device")[0] == ["Device.Revoked"]
    assert acts("&b_action=policy&b_actor=ST-AUD-B")[0] == ["Policy.Changed", "Policy.Old"]
    today = timezone.localdate().isoformat()
    assert "Policy.Old" not in acts(f"&b_from={today}")[0]
    assert acts(f"&b_to={(timezone.localdate() - timedelta(days=30)).isoformat()}")[0] == ["Policy.Old"]
    assert acts("&b_action=%25")[0] == []  # a wildcard is matched literally, not as "everything"
    bad, r = acts("&b_from=not-a-date&b_to=2026-13-45&b_action=" + "x" * 500)
    assert bad == [] and "was not a date" in r.content.decode()
    ok, r = acts("&b_from=not-a-date")
    assert "Policy.Changed" in ok and "From date was not a date" in r.content.decode()
    assert "<script" not in acts("&b_actor=%3Cscript%3E")[1].content.decode().split("audit-filter")[1][:400]
