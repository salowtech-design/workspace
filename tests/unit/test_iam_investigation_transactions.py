import pytest
from django.urls import reverse

from apps.accounts.models import AuthenticationTransaction
from apps.accounts.views import admin_iam


@pytest.mark.django_db
def test_investigation_search_uses_sign_in_transactions(rf, monkeypatch):
    AuthenticationTransaction.objects.create(
        transaction_id="tx-aaaa-1111",
        correlation_id="corr-find-me",
        domain="iam",
        identity_ref="IAM-X1",
        stage="terminal",
        outcome="success",
    )
    AuthenticationTransaction.objects.create(
        transaction_id="tx-bbbb-2222",
        correlation_id="corr-other",
        domain="iam",
        identity_ref="IAM-X2",
        stage="password",
        outcome="failed",
    )
    from apps.accounts.auth.authentication_centre import search_auth_records

    hit = search_auth_records(domain="iam", query="corr-find", time_window_hours=0)
    assert [t.transaction_id for t in hit] == ["tx-aaaa-1111"]
    hit = search_auth_records(domain="iam", outcome="failed", stage="password", time_window_hours=0)
    assert [t.transaction_id for t in hit] == ["tx-bbbb-2222"]
    assert reverse("iam:auth_transaction_detail", args=["tx-aaaa-1111"])
    assert hasattr(admin_iam, "search_auth_records")


@pytest.mark.django_db
def test_transaction_detail_renders_recorded_stage_list(rf):
    from django.template.loader import render_to_string

    from apps.accounts.views.authentication_centre import _stage_rows

    stages = [
        {
            "stage": "password_check",
            "result": "pass",
            "reason": "ok",
            "timestamp": "2026-11-25T03:00:56+05:30",
            "duration_ms": 3.2,
            "metadata": {"secret": "x"},
        },
        {
            "stage": "completed",
            "result": "success",
            "reason": "credentials_and_mfa_valid",
            "timestamp": "2026-11-25T03:00:57+05:30",
            "duration_ms": 0.0,
            "metadata": {},
        },
    ]
    rows = _stage_rows(stages)
    assert [r["name"] for r in rows] == ["password_check", "completed"]
    assert all("metadata" not in r for r in rows)
    tx = AuthenticationTransaction.objects.create(
        transaction_id="tx-render-1",
        correlation_id="c1",
        domain="iam",
        identity_ref="IAM-X1",
        stage="completed",
        outcome="success",
        stages=stages,
    )
    html = render_to_string(
        "accounts/admin/auth_transaction_detail.html",
        {"iam_admin": None, "tx": tx, "stages": rows, "linked_events": []},
        request=rf.get("/"),
    )
    assert "PASSWORD_CHECK" in html and "credentials_and_mfa_valid" in html
    assert "No pipeline stages recorded" not in html


@pytest.mark.django_db
def test_transaction_detail_and_investigation_denied_without_iam_session(client, django_user_model):
    from apps.accounts.models import CustomUser

    AuthenticationTransaction.objects.create(
        transaction_id="tx-deny-1",
        correlation_id="c-deny",
        domain="iam",
        identity_ref="IAM-X1",
        stage="completed",
        outcome="success",
    )
    host = "iam.salowtech.com"
    detail = reverse("iam:auth_transaction_detail", args=["tx-deny-1"])
    r = client.get(detail, HTTP_HOST=host)
    assert r.status_code == 302 and "login" in r["Location"]
    # unknown id must not reveal whether it exists
    r2 = client.get(reverse("iam:auth_transaction_detail", args=["tx-nope"]), HTTP_HOST=host)
    assert r2.status_code == 302 and "login" in r2["Location"]
    # a signed-in workspace employee (even Super Admin) has no IAM session
    emp = CustomUser.objects.create_user(
        employee_id="ST-DENY-1",
        full_name="D",
        password="ValidPass2026!@#",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
    )
    client.force_login(emp)
    r3 = client.get(detail, HTTP_HOST=host)
    assert r3.status_code == 302 and "login" in r3["Location"]
    r4 = client.get(reverse("iam:dashboard") + "?tab=investigation", HTTP_HOST=host)
    assert r4.status_code in (302, 403)
    assert b"tx-deny-1" not in r3.content and b"tx-deny-1" not in r4.content


@pytest.mark.django_db
def test_search_filters_device_risk_and_date():
    import datetime

    from apps.accounts.auth.authentication_centre import search_auth_records

    AuthenticationTransaction.objects.create(
        transaction_id="tx-f-1",
        correlation_id="c-f1",
        domain="iam",
        identity_ref="IAM-A",
        device_ref="dev-abc",
        risk_decision="challenge",
        stage="mfa",
        outcome="challenged",
    )
    AuthenticationTransaction.objects.create(
        transaction_id="tx-f-2",
        correlation_id="c-f2",
        domain="iam",
        identity_ref="IAM-B",
        device_ref="dev-xyz",
        risk_decision="allow",
        stage="completed",
        outcome="success",
    )

    def ids(**kw):
        found = search_auth_records(domain="iam", time_window_hours=0, **kw)
        return sorted(t.transaction_id for t in found)

    assert ids(device="abc") == ["tx-f-1"]
    assert ids(risk="allow") == ["tx-f-2"]
    today = datetime.date.today()
    assert ids(date_from=today, date_to=today) == ["tx-f-1", "tx-f-2"]
    assert ids(date_from=today + datetime.timedelta(days=2)) == []
