"""Two threads consuming the same approved Level 3 request at the same instant.

The write is slowed (synthetic window) so a missing row lock shows up. The control run
removes select_for_update to prove the test is sensitive to the lock."""

import threading
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.db.models.query import QuerySet
from django.utils import timezone

from apps.accounts.auth.iam_auth import consume_dual_approval_request
from apps.accounts.models import CustomUser, IAMAdminIdentity, IAMDualApprovalRequest


def _admin(n):
    u = CustomUser.objects.create_user(
        employee_id=f"ST-CC{n}",
        full_name=f"CC{n}",
        email=f"st-cc{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    return IAMAdminIdentity.objects.create(
        admin_id=f"IAM-CC{n}", user=u, role="super_admin", status="active"
    )


def _race(lock):
    requester, approver = _admin(1), _admin(2)
    now = timezone.now()
    req = IAMDualApprovalRequest.objects.create(
        action="create_employee",
        target_id="CCTARGET",
        reason="race test",
        requester=requester,
        requester_stepup_verified_at=now,
        approver=approver,
        approver_stepup_verified_at=now,
        expires_at=now + timedelta(minutes=10),
        status=IAMDualApprovalRequest.Status.APPROVED,
    )
    results = []
    orig_save = IAMDualApprovalRequest.save

    def slow_save(self, *a, **k):
        time.sleep(0.6)
        return orig_save(self, *a, **k)

    barrier = threading.Barrier(2)

    def go():
        try:
            barrier.wait(5)
            try:
                consume_dual_approval_request(req.request_id, "create_employee", "CCTARGET")
                results.append("ok")
            except Exception as err:  # noqa: BLE001
                results.append(type(err).__name__)
        finally:
            connection.close()

    patches = [mock.patch.object(IAMDualApprovalRequest, "save", slow_save)]
    if not lock:
        patches.append(mock.patch.object(QuerySet, "select_for_update", lambda self, *a, **k: self))
    for p in patches:
        p.start()
    try:
        threads = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
    finally:
        for p in patches:
            p.stop()
    req.refresh_from_db()
    return sorted(results), req.status


@pytest.mark.django_db(transaction=True)
def test_one_approved_request_is_consumed_by_exactly_one_of_two_concurrent_callers():
    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    results, status = _race(lock=True)
    assert results.count("ok") == 1, results
    assert status == IAMDualApprovalRequest.Status.CONSUMED


@pytest.mark.django_db(transaction=True)
def test_control_without_the_row_lock_lets_both_callers_consume():
    """Sensitivity control: proves the test above would catch a missing lock."""
    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    results, _status = _race(lock=False)
    assert results.count("ok") == 2, results
