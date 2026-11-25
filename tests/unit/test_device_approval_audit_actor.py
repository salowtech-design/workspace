"""Device approval decisions store the authenticated person who made them as the actor."""

import pytest

from apps.accounts.auth.device_approval import (
    approve_device_by_existing_session,
    create_device_approval_request,
    reject_device_request,
)
from apps.accounts.models import AuthEvent, CustomUser


def _user(n, role=CustomUser.Role.EMPLOYEE):
    return CustomUser.objects.create_user(
        employee_id=f"ST-DAA{n}",
        full_name=f"DAA{n}",
        email=f"st-daa{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=role,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


@pytest.mark.django_db
def test_primary_device_approval_records_the_approving_session_user():
    user = _user(1)
    req = create_device_approval_request(user=user)
    approve_device_by_existing_session(
        req.request_id, user, submitted_number=req.number_challenge, approving_device_id="k"
    )
    ev = AuthEvent.objects.get(event="device_approved_by_user")
    assert ev.employee_id == user.employee_id
    assert ev.actor_employee_id == user.employee_id


@pytest.mark.django_db
def test_rejection_records_the_caller_as_actor():
    user = _user(2)
    req = create_device_approval_request(user=user)
    reject_device_request(req.request_id, actor=user, reason="not me")
    ev = AuthEvent.objects.get(event="device_approval_rejected")
    assert ev.actor_employee_id == user.employee_id


@pytest.mark.django_db
def test_requested_event_has_no_actor_because_nobody_is_authenticated_yet():
    user = _user(3)
    create_device_approval_request(user=user)
    ev = AuthEvent.objects.get(event="device_approval_requested")
    assert ev.employee_id == user.employee_id
    assert ev.actor_employee_id == ""


@pytest.mark.django_db
def test_another_employee_cannot_reject_someone_elses_request():
    from django.core.exceptions import PermissionDenied

    from apps.accounts.models import DeviceApprovalRequest

    owner, other = _user(4), _user(5)
    req = create_device_approval_request(user=owner)
    with pytest.raises(PermissionDenied):
        reject_device_request(req.request_id, actor=other, reason="not mine")
    req.refresh_from_db()
    assert req.status == DeviceApprovalRequest.Status.PENDING
    assert not AuthEvent.objects.filter(event="device_approval_rejected").exists()
    reject_device_request(req.request_id, actor=owner, reason="mine")
    req.refresh_from_db()
    assert req.status == DeviceApprovalRequest.Status.REJECTED
