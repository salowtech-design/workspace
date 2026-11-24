"""The IAM console view is also mounted on the workspace host. Without an IAM session it
must not run assurance-gated mutations (it used to skip the Level 2 / Level 3 checks)."""

import pytest
from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import CustomUser

pytestmark = pytest.mark.django_db


def _user(eid, role):
    return CustomUser.objects.create(
        employee_id=eid,
        full_name=eid,
        email=eid.lower() + "@salowtech.com",
        email_verified=True,
        password=make_password("SyntheticPass2026!@#x"),
        role=role,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


@pytest.mark.parametrize(
    "actor_role,new_role",
    [
        (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SUPER_ADMIN),
        (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN),
        (CustomUser.Role.ADMIN, CustomUser.Role.HR),
    ],
)
def test_role_change_without_iam_session_is_refused(actor_role, new_role):
    actor = _user("EMP-WH-ACTOR", actor_role)
    target = _user("EMP-WH-TARGET", CustomUser.Role.EMPLOYEE)
    _user("EMP-WH-SA2", CustomUser.Role.SUPER_ADMIN)
    c = Client()
    c.force_login(actor)
    c.post(
        "/auth/admin/iam/",
        {"action": "update_role", "target_employee_id": target.employee_id, "new_role": new_role},
        HTTP_HOST="app.salowtech.com",
    )
    target.refresh_from_db()
    assert target.role == CustomUser.Role.EMPLOYEE


def _post(actor, **data):
    c = Client()
    c.force_login(actor)
    return c.post("/auth/admin/iam/", data, HTTP_HOST="app.salowtech.com")


@pytest.mark.parametrize("role", [CustomUser.Role.HR, CustomUser.Role.MANAGER])
def test_hr_and_manager_cannot_suspend_exit_or_reactivate_admins(role):
    actor = _user("EMP-WH-LOW", role)
    admin = _user("EMP-WH-ADM", CustomUser.Role.ADMIN)
    for action in ("suspend_account", "exit_account"):
        _post(actor, action=action, target_employee_id=admin.employee_id)
    admin.refresh_from_db()
    assert admin.is_active and admin.status == CustomUser.Status.ACTIVE
    admin.is_active = False
    admin.status = CustomUser.Status.SUSPENDED
    admin.save()
    _post(actor, action="reactivate_account", target_employee_id=admin.employee_id)
    admin.refresh_from_db()
    assert admin.status == CustomUser.Status.SUSPENDED


def test_unknown_role_value_is_never_saved():
    actor = _user("EMP-WH-A2", CustomUser.Role.ADMIN)
    target = _user("EMP-WH-T2", CustomUser.Role.EMPLOYEE)
    _post(actor, action="update_role", target_employee_id="EMP-WH-T2", new_role="bogus")
    target.refresh_from_db()
    assert target.role == CustomUser.Role.EMPLOYEE


def test_last_active_super_admin_cannot_be_demoted_or_suspended(db):
    from apps.accounts.views.admin_iam import _would_remove_last_super_admin

    only = _user("EMP-WH-SA1", CustomUser.Role.SUPER_ADMIN)
    assert _would_remove_last_super_admin(only) is True
    _user("EMP-WH-SA2B", CustomUser.Role.SUPER_ADMIN)
    # Standing minimum is two active Super Admins: with one other left, still refused.
    assert _would_remove_last_super_admin(only) is True
    _user("EMP-WH-SA3B", CustomUser.Role.SUPER_ADMIN)
    assert _would_remove_last_super_admin(only) is False


class TestStepUpIsBoundToActorActionAndPayload:
    def _world(self):
        from tests.unit.test_iam_platform_complete import _iam_console_client

        def mk(eid, role):
            return CustomUser.objects.create_user(
                employee_id=eid,
                full_name=eid,
                email=f"{eid.lower()}@salowtech.com",
                password="Xx-pass-2026!ab",
                role=role,
                status=CustomUser.Status.ACTIVE,
                is_active=True,
            )

        admin = mk("ST-RP-ADM", CustomUser.Role.ADMIN)
        t1 = mk("ST-RP-T1", CustomUser.Role.EMPLOYEE)
        t2 = mk("ST-RP-T2", CustomUser.Role.EMPLOYEE)
        c, sess = _iam_console_client(admin, "suspend_account")
        return c, sess, t1, t2

    def _form(self, eid):
        return {"action": "suspend_account", "target_employee_id": eid}

    def test_step_up_granted_for_target_a_is_refused_for_target_b_at_first_use(self, db):
        from tests.unit.test_iam_platform_complete import _bind_stepup

        c, sess, t1, t2 = self._world()
        _bind_stepup(c, sess, self._form("ST-RP-T1"))
        url = "/iam/dashboard/"
        resp = c.post(url, self._form("ST-RP-T2"), HTTP_HOST="iam.salowtech.com")
        assert "/iam/stepup/" in resp.url
        t2.refresh_from_db()
        assert t2.status == CustomUser.Status.ACTIVE

    def test_step_up_for_target_a_works_for_a_once_and_cannot_be_replayed(self, db):
        from tests.unit.test_iam_platform_complete import _bind_stepup

        c, sess, t1, t2 = self._world()
        _bind_stepup(c, sess, self._form("ST-RP-T1"))
        url = "/iam/dashboard/"
        c.post(url, self._form("ST-RP-T1"), HTTP_HOST="iam.salowtech.com")
        t1.refresh_from_db()
        assert t1.status == CustomUser.Status.SUSPENDED
        c.post(url, self._form("ST-RP-T2"), HTTP_HOST="iam.salowtech.com")
        t2.refresh_from_db()
        assert t2.status == CustomUser.Status.ACTIVE

    def test_step_up_without_a_recorded_request_binds_nothing(self, db):
        """A step-up taken without the console asking for it (no pending request) carries no
        payload binding, so it authorises no mutation."""
        c, sess, t1, t2 = self._world()
        resp = c.post("/iam/dashboard/", self._form("ST-RP-T1"), HTTP_HOST="iam.salowtech.com")
        assert "/iam/stepup/" in resp.url
        t1.refresh_from_db()
        assert t1.status == CustomUser.Status.ACTIVE


class TestWorkspacePolicyAndStepUpPage:
    def _client(self):
        from tests.unit.test_iam_platform_complete import _iam_console_client

        sa = CustomUser.objects.create_user(
            employee_id="ST-RP-SA",
            full_name="SA",
            email="st-rp-sa@salowtech.com",
            password="Xx-pass-2026!ab",
            role=CustomUser.Role.SUPER_ADMIN,
            status=CustomUser.Status.ACTIVE,
            is_active=True,
        )
        return _iam_console_client(sa, "update_workspace_security_policy")

    def _form(self, key):
        return {
            "tab": "workspace_security",
            "policy_key": key,
            "policy_value": "on",
            "justification": "regression",
        }

    def test_policy_step_up_is_bound_to_the_exact_policy_change(self, db):
        from apps.accounts.models import WorkspaceSecurityPolicy
        from tests.unit.test_iam_platform_complete import _bind_stepup

        c, sess = self._client()
        _bind_stepup(c, sess, self._form("policy_a"), action="update_workspace_security_policy")
        c.post("/iam/dashboard/", self._form("policy_b"), HTTP_HOST="iam.salowtech.com")
        assert not WorkspaceSecurityPolicy.objects.filter(policy_key="policy_b").exists()
        c.post("/iam/dashboard/", self._form("policy_a"), HTTP_HOST="iam.salowtech.com")
        assert WorkspaceSecurityPolicy.objects.filter(policy_key="policy_a").exists()

    def test_step_up_page_names_the_change_and_says_single_use(self, db):
        c, sess = self._client()
        c.post("/iam/dashboard/", self._form("policy_a"), HTTP_HOST="iam.salowtech.com")
        page = c.get(
            "/iam/stepup/?action=update_workspace_security_policy", HTTP_HOST="iam.salowtech.com"
        ).content.decode()
        assert "You are approving" in page and "policy_key = policy_a" in page
        assert "once" in page and "elevate assurance for 5 minutes" not in page


class TestLevel3TwoPersonFlow:
    def _sa(self, n):
        from tests.unit.test_iam_platform_complete import _iam_console_client

        u = CustomUser.objects.create_user(
            employee_id=f"ST-L3-SA{n}",
            full_name=f"SA{n}",
            email=f"st-l3-sa{n}@salowtech.com",
            password="Xx-pass-2026!ab",
            role=CustomUser.Role.SUPER_ADMIN,
            status=CustomUser.Status.ACTIVE,
            is_active=True,
        )
        c, sess = _iam_console_client(u, "suspend_account")
        return u, c, sess

    def _form(self, eid):
        return {"action": "suspend_account", "target_employee_id": eid}

    def _decide(self, c, sess, req, verb="approve_dual_request"):
        from tests.unit.test_iam_platform_complete import _bind_stepup

        data = {"action": verb, "request_id": str(req.request_id), "tab": "l3_approvals"}
        _bind_stepup(c, sess, data, action=req.action)
        return c.post("/iam/dashboard/", data, HTTP_HOST="iam.salowtech.com")

    def _request_suspend(self, c, sess, eid):
        from apps.accounts.models import IAMDualApprovalRequest
        from tests.unit.test_iam_platform_complete import _bind_stepup

        _bind_stepup(c, sess, self._form(eid))
        c.post("/iam/dashboard/", self._form(eid), HTTP_HOST="iam.salowtech.com")
        return (
            IAMDualApprovalRequest.objects.filter(target_id=eid).order_by("-created_at").first()
            if hasattr(IAMDualApprovalRequest, "created_at")
            else IAMDualApprovalRequest.objects.filter(target_id=eid).first()
        )

    def test_super_admin_suspend_needs_a_different_approver_then_executes_once(self, db):
        from apps.accounts.models import IAMDualApprovalRequest
        from tests.unit.test_iam_platform_complete import _bind_stepup

        u1, c1, s1 = self._sa(1)
        u2, c2, s2 = self._sa(2)
        u3, _c3, _s3 = self._sa(3)
        u4, _c4, _s4 = self._sa(4)
        req = self._request_suspend(c1, s1, u3.employee_id)
        assert req and req.status == IAMDualApprovalRequest.Status.PENDING
        u3.refresh_from_db()
        assert u3.status == CustomUser.Status.ACTIVE  # nothing executed on request
        # Requester cannot approve their own request
        c1.post(
            "/iam/dashboard/",
            {"action": "approve_dual_request", "request_id": str(req.request_id)},
            HTTP_HOST="iam.salowtech.com",
        )
        req.refresh_from_db()
        assert req.status == IAMDualApprovalRequest.Status.PENDING
        # Re-submitting before approval does not execute
        _bind_stepup(c1, s1, self._form(u3.employee_id))
        c1.post("/iam/dashboard/", self._form(u3.employee_id), HTTP_HOST="iam.salowtech.com")
        u3.refresh_from_db()
        assert u3.status == CustomUser.Status.ACTIVE
        # A different Super Admin approves with their own step-up
        self._decide(c2, s2, req)
        req.refresh_from_db()
        assert req.status == IAMDualApprovalRequest.Status.APPROVED
        u3.refresh_from_db()
        assert u3.status == CustomUser.Status.ACTIVE  # approval alone does not execute
        # Requester submits the identical change: executes and consumes the approval
        _bind_stepup(c1, s1, self._form(u3.employee_id))
        c1.post("/iam/dashboard/", self._form(u3.employee_id), HTTP_HOST="iam.salowtech.com")
        u3.refresh_from_db()
        req.refresh_from_db()
        assert u3.status == CustomUser.Status.SUSPENDED
        assert req.status == IAMDualApprovalRequest.Status.CONSUMED
        # Approval cannot be replayed on another Super Admin
        _bind_stepup(c1, s1, self._form(u4.employee_id))
        c1.post("/iam/dashboard/", self._form(u4.employee_id), HTTP_HOST="iam.salowtech.com")
        u4.refresh_from_db()
        assert u4.status == CustomUser.Status.ACTIVE

    def test_standing_minimum_of_two_super_admins_is_kept(self, db):
        from apps.accounts.models import IAMDualApprovalRequest

        u1, c1, s1 = self._sa(1)
        u2, c2, s2 = self._sa(2)
        c1.post("/iam/dashboard/", self._form(u2.employee_id), HTTP_HOST="iam.salowtech.com")
        assert not IAMDualApprovalRequest.objects.filter(target_id=u2.employee_id).exists()
        u2.refresh_from_db()
        assert u2.status == CustomUser.Status.ACTIVE

    def test_approval_is_revalidated_at_execution_if_approver_loses_role(self, db):
        from apps.accounts.models import IAMAdminIdentity, IAMDualApprovalRequest
        from tests.unit.test_iam_platform_complete import _bind_stepup

        u1, c1, s1 = self._sa(1)
        u2, c2, s2 = self._sa(2)
        u3, _c3, _s3 = self._sa(3)
        u4, _c4, _s4 = self._sa(4)
        req = self._request_suspend(c1, s1, u3.employee_id)
        self._decide(c2, s2, req)
        req.refresh_from_db()
        assert req.status == IAMDualApprovalRequest.Status.APPROVED
        # The approver is demoted after approving, before the requester executes
        IAMAdminIdentity.objects.filter(user=u2).update(role=IAMAdminIdentity.Role.ADMIN)
        _bind_stepup(c1, s1, self._form(u3.employee_id))
        c1.post("/iam/dashboard/", self._form(u3.employee_id), HTTP_HOST="iam.salowtech.com")
        u3.refresh_from_db()
        req.refresh_from_db()
        assert u3.status == CustomUser.Status.ACTIVE
        assert req.status == IAMDualApprovalRequest.Status.APPROVED
