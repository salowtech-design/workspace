"""An Admin account's MFA reset needs a second administrator, whichever role issues it.

Also: the IAM logout URL must not change state on a GET."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.auth.reset import check_recovery_authority, issue_mfa_reset_code
from apps.accounts.models import ActivationCode, CustomUser


def _user(n, role):
    return CustomUser.objects.create_user(
        employee_id=f"ST-MR{n}",
        full_name=f"MR{n}",
        email=f"st-mr{n}@salowtech.com",
        password="Xx-pass-2026!ab",
        role=role,
        status=CustomUser.Status.ACTIVE,
        is_active=True,
    )


@pytest.mark.django_db
def test_super_admin_alone_cannot_issue_an_admin_mfa_reset_code():
    sa, admin = _user(1, CustomUser.Role.SUPER_ADMIN), _user(2, CustomUser.Role.ADMIN)
    with pytest.raises(ValidationError):
        issue_mfa_reset_code(target_user=admin, issuing_admin=sa)
    assert not ActivationCode.objects.filter(user=admin).exists()


@pytest.mark.django_db
def test_super_admin_cannot_approve_their_own_admin_reset():
    sa, admin = _user(1, CustomUser.Role.SUPER_ADMIN), _user(2, CustomUser.Role.ADMIN)
    with pytest.raises(ValidationError):
        check_recovery_authority(sa, admin, approving_admin=sa)


@pytest.mark.django_db
def test_a_second_administrator_lets_the_admin_reset_proceed():
    sa, admin = _user(1, CustomUser.Role.SUPER_ADMIN), _user(2, CustomUser.Role.ADMIN)
    other = _user(3, CustomUser.Role.SUPER_ADMIN)
    _, raw = issue_mfa_reset_code(target_user=admin, issuing_admin=sa, approving_admin=other)
    assert raw


@pytest.mark.django_db
def test_an_employee_target_still_needs_no_second_approver():
    sa, emp = _user(1, CustomUser.Role.SUPER_ADMIN), _user(4, CustomUser.Role.EMPLOYEE)
    _, raw = issue_mfa_reset_code(target_user=emp, issuing_admin=sa)
    assert raw


@pytest.mark.django_db
def test_a_non_administrator_cannot_approve():
    sa, admin = _user(1, CustomUser.Role.SUPER_ADMIN), _user(2, CustomUser.Role.ADMIN)
    emp = _user(4, CustomUser.Role.EMPLOYEE)
    with pytest.raises(PermissionDenied):
        check_recovery_authority(sa, admin, approving_admin=emp)


def test_iam_logout_view_refuses_get_and_changes_nothing(rf):
    from apps.accounts.views.iam_admin import iam_logout_view

    assert iam_logout_view(rf.get("/iam/logout/")).status_code == 405
