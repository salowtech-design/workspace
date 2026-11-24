"""Regression: AuthEvent.reference_id must hold a 36-char UUID (strict MySQL/TiDB rejects overflow)."""

import uuid

import pytest

from apps.accounts.models import AuthEvent


@pytest.mark.django_db
def test_authevent_reference_id_holds_uuid():
    assert AuthEvent._meta.get_field("reference_id").max_length >= 36
    ev = AuthEvent.objects.create(
        event="x", code="AUTH-TST-001", step="s", reference_id=str(uuid.uuid4())
    )
    ev.refresh_from_db()
    assert len(ev.reference_id) == 36


@pytest.mark.django_db
def test_overlong_login_identifier_does_not_crash_audit():
    from apps.accounts.auth.audit import log_auth_event

    rec = log_auth_event(
        event="e", code="C" * 50, step="s", employee_id="X" * 200, reference_id="R" * 200
    )
    assert len(rec.employee_id) == 32 and len(rec.reference_id) == 64
