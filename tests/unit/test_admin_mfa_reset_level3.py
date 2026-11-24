"""An MFA reset for an Admin or Super Admin account goes through the Level 3 two-person path:
request, a different Super Admin approves, the requester submits again and the code is issued once."""

import pytest

from apps.accounts.models import (
    ActivationCode,
    AuthEvent,
    CustomUser,
    IAMDualApprovalRequest,
)
from tests.unit.test_iam_platform_complete import _bind_stepup, _iam_console_client

pytestmark = pytest.mark.django_db
HOST = "iam.salowtech.com"


def _sa(n):
    u = CustomUser.objects.create_user(
        employee_id=f"ST-MRL3-SA{n}",
        full_name=f"SA{n}",
        email=f"st-mrl3-sa{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=CustomUser.Role.SUPER_ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    c, sess = _iam_console_client(u, "issue_mfa_reset")
    return u, c, sess


def _form(eid):
    return {"action": "issue_mfa_reset", "target_employee_id": eid}


def _submit(c, sess, eid):
    data = _form(eid)
    _bind_stepup(c, sess, data)
    return c.post("/iam/dashboard/", data, HTTP_HOST=HOST)


def test_admin_target_needs_second_super_admin_then_issues_once():
    u1, c1, s1 = _sa(1)
    u2, c2, s2 = _sa(2)
    admin = CustomUser.objects.create_user(
        employee_id="ST-MRL3-ADM",
        full_name="Target Admin",
        email="st-mrl3-adm@salowtech.com",
        password="Xx-pass-2026!ab",
        role=CustomUser.Role.ADMIN,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    eid = admin.employee_id
    _submit(c1, s1, eid)
    req = IAMDualApprovalRequest.objects.filter(target_id=eid).first()
    assert req and req.status == IAMDualApprovalRequest.Status.PENDING
    assert not ActivationCode.objects.filter(user=admin).exists()  # nothing issued on request
    # Submitting again before approval still issues nothing
    _submit(c1, s1, eid)
    assert not ActivationCode.objects.filter(user=admin).exists()
    # A different Super Admin approves; approval alone issues nothing
    data = {"action": "approve_dual_request", "request_id": str(req.request_id)}
    _bind_stepup(c2, s2, data, action=req.action)
    c2.post("/iam/dashboard/", data, HTTP_HOST=HOST)
    req.refresh_from_db()
    assert req.status == IAMDualApprovalRequest.Status.APPROVED
    assert not ActivationCode.objects.filter(user=admin).exists()
    # The requester submits the identical request: the code is issued and the approval used up
    _submit(c1, s1, eid)
    assert (
        ActivationCode.objects.filter(user=admin, purpose=ActivationCode.Purpose.MFA_RESET).count()
        == 1
    )
    req.refresh_from_db()
    assert req.status == IAMDualApprovalRequest.Status.CONSUMED
    evt = AuthEvent.objects.filter(event="mfa_reset_code_issued", employee_id=eid).first()
    assert evt and u2.employee_id in evt.message  # the approver is recorded
    # Replay: the used approval cannot issue a second code
    _submit(c1, s1, eid)
    assert ActivationCode.objects.filter(user=admin).count() == 1


def test_employee_target_still_single_step():
    u1, c1, s1 = _sa(1)
    emp = CustomUser.objects.create_user(
        employee_id="ST-MRL3-EMP",
        full_name="Emp",
        email="st-mrl3-emp@salowtech.com",
        password="Xx-pass-2026!ab",
        role=CustomUser.Role.EMPLOYEE,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )
    _submit(c1, s1, emp.employee_id)
    assert (
        ActivationCode.objects.filter(user=emp, purpose=ActivationCode.Purpose.MFA_RESET).count()
        == 1
    )
    assert not IAMDualApprovalRequest.objects.filter(target_id=emp.employee_id).exists()
