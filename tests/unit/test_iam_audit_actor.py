"""IAM sign-in, step-up and Level 3 events record who acted in the structured actor column."""

import pytest

from apps.accounts.models import AuthEvent, CustomUser
from tests.unit.test_admin_mfa_reset_level3 import HOST, _form, _mk
from tests.unit.test_iam_platform_complete import _bind_stepup, _iam_console_client

pytestmark = pytest.mark.django_db


def test_level3_request_and_approval_record_their_actors():
    a = _mk("ST-ACT-R1", CustomUser.Role.SUPER_ADMIN)
    b = _mk("ST-ACT-R2", CustomUser.Role.SUPER_ADMIN)
    t = _mk("ST-ACT-T1", CustomUser.Role.ADMIN)
    c1, s1 = _iam_console_client(a, "issue_mfa_reset")
    c2, s2 = _iam_console_client(b, "issue_mfa_reset")
    data = _form(t.employee_id)
    _bind_stepup(c1, s1, data)
    c1.post("/iam/dashboard/", data, HTTP_HOST=HOST)
    from apps.accounts.models import IAMDualApprovalRequest

    req = IAMDualApprovalRequest.objects.get(target_id=t.employee_id)
    d2 = {"action": "approve_dual_request", "request_id": str(req.request_id)}
    _bind_stepup(c2, s2, d2, action=req.action)
    c2.post("/iam/dashboard/", d2, HTTP_HOST=HOST)
    requested = AuthEvent.objects.filter(event="iam_dual_approval_requested").first()
    granted = AuthEvent.objects.filter(event="iam_dual_approval_granted").first()
    assert requested and requested.actor_employee_id == a.employee_id
    assert granted and granted.actor_employee_id == b.employee_id
