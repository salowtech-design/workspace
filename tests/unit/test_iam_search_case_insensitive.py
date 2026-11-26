"""IAM keyword filters ignore case on TiDB's binary collation. Same rows and columns as before; only the
text match changed. A literal % or _ is not a wildcard."""

import pytest
from django.test import Client

from apps.accounts.auth.authentication_centre import search_auth_records
from apps.accounts.models import AuthEvent, AuthenticationTransaction, CustomUser
from tests.unit.test_admin_mfa_reset_level3 import HOST, _mk
from tests.unit.test_iam_platform_complete import _iam_console_client

pytestmark = pytest.mark.django_db


def _tx(txn, corr, ident, reason="", device=""):
    return AuthenticationTransaction.objects.create(
        transaction_id=txn, correlation_id=corr, domain="iam", identity_ref=ident,
        stage="terminal", outcome="success", reason_category=reason, device_ref=device,
    )  # fmt: skip


def test_authentication_records_search_ignores_case_and_treats_percent_underscore_literally():
    _tx("tx-Case-1", "Corr-Mixed", "Iam-Mixed-1", reason="Bad_Password", device="Laptop-ABC")
    _tx("tx-other-2", "corr-zzz", "other", reason="badXpassword", device="phone")
    ids = lambda **kw: sorted(t.transaction_id for t in search_auth_records(domain="iam", time_window_hours=0, **kw))
    assert ids(query="corr-mixed") == ["tx-Case-1"]
    assert ids(query="IAM-MIXED") == ["tx-Case-1"]
    assert ids(query="TX-CASE") == ["tx-Case-1"]
    assert ids(query="bad_password") == ["tx-Case-1"]  # underscore is literal, not "any character"
    assert ids(query="%") == []
    assert ids(device="laptop-abc") == ["tx-Case-1"]
    assert ids(query="nomatch") == []


def test_dashboard_people_audit_and_investigation_filters_ignore_case():
    a = _mk("ST-CI-A", CustomUser.Role.SUPER_ADMIN)
    _mk("ST-CI-B", CustomUser.Role.ADMIN)
    AuthEvent.objects.create(event="probe", employee_id="ST-CI-HIT", reference_id="Ref-Mixed-9")
    AuthEvent.objects.create(event="probe", employee_id="OTHER-PERSON", reference_id="ref-other")
    c, _s = _iam_console_client(a, "issue_mfa_reset")
    r = c.get("/iam/dashboard/?q=st-ci-hit", HTTP_HOST=HOST)
    assert {e.employee_id for e in r.context["audit_events"]} == {"ST-CI-HIT"}
    r = c.get("/iam/dashboard/?q=st-ci-b", HTTP_HOST=HOST)
    assert [u.employee_id for u in r.context["users"]] == ["ST-CI-B"]
    r = c.get("/iam/dashboard/?q_flow=ref-mixed", HTTP_HOST=HOST)
    assert [e.employee_id for e in r.context["investigation_events"]] == ["ST-CI-HIT"]
    r = c.get("/iam/dashboard/?q_flow=%25", HTTP_HOST=HOST)
    assert list(r.context["investigation_events"]) == []  # a literal percent is not a wildcard


def test_console_auth_events_filters_ignore_case_and_keep_the_admin_gate():
    a = _mk("ST-CI-ADM", CustomUser.Role.ADMIN)
    AuthEvent.objects.create(event="probe", code="AUTH-ACT-001", employee_id="ST-CI-HIT", reference_id="r1")
    AuthEvent.objects.create(event="probe", code="AUTH-ACT-002", employee_id="OTHER", reference_id="r2")
    c = Client()
    c.force_login(a)
    r = c.get("/auth/console/auth-events/", {"code": "auth-act-001"})
    assert r.status_code == 200
    assert {e.employee_id for e in r.context["events"]} == {"ST-CI-HIT"}
    r = c.get("/auth/console/auth-events/", {"emp": "st-ci"})
    assert {e.employee_id for e in r.context["events"]} == {"ST-CI-HIT"}
    emp = _mk("ST-CI-EMP", CustomUser.Role.EMPLOYEE)
    c2 = Client()
    c2.force_login(emp)
    assert c2.get("/auth/console/auth-events/", {"code": "auth"}).status_code == 302
