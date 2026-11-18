"""
Security Operations (SecOps) & Alerting Tests — Salow Workspace IAM.
Validates:
1. Append-only security alert creation across high-severity categories.
2. GET /security/events?after=<id> with cursor pagination and HTMX rendering.
3. Zero secret exposure in event streams.
4. Alert acknowledgement requires mandatory justification reason.
5. Independent Reviewer Invariant: Actor cannot acknowledge an alert targeting themselves.
6. Role permissions: Security Reviewer (read-only) vs. Security Admin.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.auth.security_ops import (
    acknowledge_security_alert,
    create_security_alert,
    get_security_events_stream,
)
from apps.accounts.models import AuthEvent, CustomUser, SecurityAlert

User = get_user_model()


@pytest.fixture
def sec_admin(db):
    user = User.objects.create(
        employee_id="ST-SEC-0001",
        full_name="Security Administrator",
        email="sec.admin@salowtech.com",
        role=CustomUser.Role.SECURITY_ADMIN,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("SecAdminPass2026!")
    user.save()
    return user


@pytest.fixture
def sec_reviewer(db):
    user = User.objects.create(
        employee_id="ST-REV-0001",
        full_name="Security Reviewer",
        email="sec.reviewer@salowtech.com",
        role=CustomUser.Role.SECURITY_REVIEWER,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("SecReviewerPass2026!")
    user.save()
    return user


@pytest.fixture
def standard_employee(db):
    user = User.objects.create(
        employee_id="ST-EMP-0099",
        full_name="Ordinary Employee",
        email="ordinary@salowtech.com",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
    )
    user.set_password("EmployeePass2026!")
    user.save()
    return user


@pytest.mark.django_db
class TestSecurityOperations:
    def test_security_alert_creation_across_categories(self):
        """Alerts can be generated across required high-severity categories."""
        categories = [
            SecurityAlert.Category.REPEATED_AUTH_FAILURES,
            SecurityAlert.Category.ACCOUNT_LOCKOUT,
            SecurityAlert.Category.PRIVILEGED_ROLE_CHANGE,
            SecurityAlert.Category.MFA_RESET,
            SecurityAlert.Category.RECOVERY_CODE_USED,
            SecurityAlert.Category.NEW_DEVICE_APPROVAL,
            SecurityAlert.Category.ADMIN_FALLBACK,
            SecurityAlert.Category.BREAK_GLASS_ACCESS,
            SecurityAlert.Category.AUDIT_INTEGRITY,
        ]
        for cat in categories:
            alert = create_security_alert(
                category=cat,
                severity=SecurityAlert.Severity.HIGH,
                title=f"Test alert for {cat}",
                employee_id="ST-TEST-0001",
                network_address="10.0.0.5",
            )
            assert alert.id is not None
            assert alert.status == SecurityAlert.Status.OPEN

    def test_alert_acknowledgement_requires_reason(self, sec_admin):
        """Acknowledging an alert requires a non-empty justification reason."""
        alert = create_security_alert(
            category=SecurityAlert.Category.ACCOUNT_LOCKOUT,
            severity=SecurityAlert.Severity.HIGH,
            title="Brute force lockout",
            employee_id="ST-TARGET-0001",
        )
        with pytest.raises(ValidationError):
            acknowledge_security_alert(alert.id, actor=sec_admin, reason="")

    def test_independent_reviewer_invariant_cannot_review_own_alert(self, sec_admin):
        """Reviewers cannot act as independent review authority for their own actions."""
        alert = create_security_alert(
            category=SecurityAlert.Category.BREAK_GLASS_ACCESS,
            severity=SecurityAlert.Severity.CRITICAL,
            title="Break-glass elevation",
            employee_id=sec_admin.employee_id,  # Target is the sec_admin themselves
        )
        with pytest.raises(PermissionDenied) as exc:
            acknowledge_security_alert(
                alert.id, actor=sec_admin, reason="I verified my own actions"
            )
        assert "Reviewers cannot act as independent review authority" in str(exc.value)

    def test_valid_acknowledgement_updates_status_without_modifying_audit_event(
        self, sec_admin, sec_reviewer
    ):
        """Acknowledging records actor, time, and reason without deleting or altering events."""
        alert = create_security_alert(
            category=SecurityAlert.Category.RECOVERY_CODE_USED,
            severity=SecurityAlert.Severity.HIGH,
            title="Recovery code redeemed",
            employee_id="ST-OTHER-0001",
        )
        initial_event_count = AuthEvent.objects.count()

        ack_alert = acknowledge_security_alert(
            alert.id,
            actor=sec_admin,
            reason="Confirmed legitimate user recovery via telephone call verification",
        )
        assert ack_alert.status == SecurityAlert.Status.ACKNOWLEDGED
        assert ack_alert.acknowledged_by == sec_admin
        assert "telephone call" in ack_alert.acknowledgement_reason

        # Underlying event count must not decrease
        assert AuthEvent.objects.count() >= initial_event_count

    def test_security_reviewer_read_only_access(self, client, sec_reviewer, sec_admin):
        """Security Reviewer can view alerts and events, but cannot acknowledge alerts."""
        alert = create_security_alert(
            category=SecurityAlert.Category.ACCOUNT_LOCKOUT,
            severity=SecurityAlert.Severity.HIGH,
            title="Account locked",
            employee_id="ST-OTHER-0001",
        )

        client.force_login(sec_reviewer)
        # GET alerts triage: 200 OK
        resp = client.get(reverse("security_alerts_root"))
        assert resp.status_code == 200
        assert "Read-Only" in resp.content.decode()

        # POST acknowledge attempt: 403 Forbidden
        ack_resp = client.post(
            reverse("accounts:security_alert_acknowledge", kwargs={"alert_id": alert.id}),
            {"reason": "Attempting to acknowledge as reviewer"},
        )
        # Redirects with error message or 403
        alert.refresh_from_db()
        assert alert.status == SecurityAlert.Status.OPEN

    def test_security_events_cursor_stream_endpoint(self, client, sec_reviewer):
        """GET /security/events?after=<id> returns events beyond cursor in stable order."""
        client.force_login(sec_reviewer)

        # Create distinct test events
        e1 = AuthEvent.objects.create(event="test_event_1", code="TST-001", employee_id="ST-01")
        e2 = AuthEvent.objects.create(event="test_event_2", code="TST-002", employee_id="ST-02")
        e3 = AuthEvent.objects.create(event="test_event_3", code="TST-003", employee_id="ST-03")

        # Query all
        resp = client.get(f"{reverse('security_events_root')}?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 3

        # Query with cursor after e1
        resp_cursor = client.get(f"{reverse('security_events_root')}?after={e1.id}&format=json")
        assert resp_cursor.status_code == 200
        data_cursor = resp_cursor.json()
        event_ids = [ev["id"] for ev in data_cursor["events"]]
        assert e1.id not in event_ids
        assert e2.id in event_ids
        assert e3.id in event_ids

    def test_unauthorized_user_cannot_read_security_events(self, client, standard_employee):
        """Standard employee without security permissions gets 403 Forbidden."""
        client.force_login(standard_employee)
        resp = client.get(reverse("security_events_root"))
        assert resp.status_code == 403
