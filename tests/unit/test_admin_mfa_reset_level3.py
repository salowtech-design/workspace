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


def _mk(eid, role):
    return CustomUser.objects.create_user(
        employee_id=eid,
        full_name=eid,
        email=f"{eid.lower()}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=role,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


def test_admin_target_second_approver_may_be_an_admin_per_recovery_policy():
    a1 = _mk("ST-MRL3-A1", CustomUser.Role.ADMIN)
    a2 = _mk("ST-MRL3-A2", CustomUser.Role.ADMIN)
    target = _mk("ST-MRL3-T1", CustomUser.Role.ADMIN)
    c1, s1 = _iam_console_client(a1, "issue_mfa_reset")
    c2, s2 = _iam_console_client(a2, "issue_mfa_reset")
    _submit(c1, s1, target.employee_id)
    req = IAMDualApprovalRequest.objects.get(target_id=target.employee_id)
    data = {"action": "approve_dual_request", "request_id": str(req.request_id)}
    _bind_stepup(c2, s2, data, action=req.action)
    c2.post("/iam/dashboard/", data, HTTP_HOST=HOST)
    req.refresh_from_db()
    assert req.status == IAMDualApprovalRequest.Status.APPROVED
    assert not ActivationCode.objects.filter(user=target).exists()
    _submit(c1, s1, target.employee_id)
    assert ActivationCode.objects.filter(user=target).count() == 1


def test_super_admin_target_still_needs_a_super_admin_approver():
    sa = _mk("ST-MRL3-S1", CustomUser.Role.SUPER_ADMIN)
    _mk("ST-MRL3-S3", CustomUser.Role.SUPER_ADMIN)
    adm = _mk("ST-MRL3-A3", CustomUser.Role.ADMIN)
    target = _mk("ST-MRL3-T2", CustomUser.Role.SUPER_ADMIN)
    c1, s1 = _iam_console_client(sa, "issue_mfa_reset")
    c2, s2 = _iam_console_client(adm, "issue_mfa_reset")
    _submit(c1, s1, target.employee_id)
    req = IAMDualApprovalRequest.objects.get(target_id=target.employee_id)
    data = {"action": "approve_dual_request", "request_id": str(req.request_id)}
    # An Admin is refused before any step-up is offered for a Super Admin target
    c2.post("/iam/dashboard/", data, HTTP_HOST=HOST)
    req.refresh_from_db()
    assert req.status == IAMDualApprovalRequest.Status.PENDING


def test_other_level3_actions_still_need_a_super_admin_decider():
    from apps.accounts.models import IAMAdminIdentity
    from apps.accounts.views.admin_iam import _l3_deciders

    adm_target = _mk("ST-MRL3-T3", CustomUser.Role.ADMIN)
    sup = (IAMAdminIdentity.Role.SUPER_ADMIN,)
    assert _l3_deciders("change_role", adm_target.employee_id) == sup
    assert _l3_deciders("issue_mfa_reset", "ST-NOPE") == sup
    assert IAMAdminIdentity.Role.ADMIN in _l3_deciders("issue_mfa_reset", adm_target.employee_id)
    emp = _mk("ST-MRL3-E1", CustomUser.Role.EMPLOYEE)
    assert _l3_deciders("issue_mfa_reset", emp.employee_id) == sup


def test_level3_guidance_names_the_roles_that_may_decide():
    from apps.accounts.views.admin_iam import _decider_phrase

    adm = _mk("ST-MRL3-T4", CustomUser.Role.ADMIN)
    sup = _mk("ST-MRL3-T5", CustomUser.Role.SUPER_ADMIN)
    assert "Administrator or Super Administrator" in _decider_phrase(
        "issue_mfa_reset", adm.employee_id
    )
    assert _decider_phrase("issue_mfa_reset", sup.employee_id) == "a different Super Administrator"
