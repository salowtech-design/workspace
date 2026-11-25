"""A policy form that does not carry a field must keep the stored value for it."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from apps.accounts.auth.authentication_centre import get_active_policy
from apps.accounts.views import authentication_centre as view_mod


def _post(data):
    request = RequestFactory().post("/iam/auth/action/", data)
    request.session = {}
    request._messages = FallbackStorage(request)
    fake = SimpleNamespace(iam_admin=SimpleNamespace(user=object()))
    captured = {}

    def fake_save(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(version=99)

    with (
        mock.patch.object(view_mod, "_require_iam_admin_session", return_value=(fake, None)),
        mock.patch.object(view_mod, "save_policy_version", side_effect=fake_save),
    ):
        view_mod.auth_control_action_view(request)
    return captured


@pytest.mark.django_db
def test_iam_form_without_flags_keeps_stored_mfa_and_device_settings():
    stored = get_active_policy("iam")
    assert stored.mfa_required is True
    got = _post(
        {
            "action_type": "update_policy",
            "domain": "iam",
            "password_min_length": "16",
            "failed_login_threshold": "3",
            "idle_session_timeout_minutes": "5",
            "absolute_session_timeout_hours": "8",
            "justification": "unchanged",
        }
    )
    assert got["mfa_required"] is stored.mfa_required
    assert got["device_approval_required"] is stored.device_approval_required
    assert got["lockout_window_minutes"] == stored.lockout_window_minutes
    assert got["lockout_duration_minutes"] == stored.lockout_duration_minutes


@pytest.mark.django_db
def test_workspace_form_checkboxes_still_control_the_flags():
    base = {
        "action_type": "update_policy",
        "domain": "workspace",
        "policy_flags_present": "1",
        "justification": "x",
    }
    off = _post(base)
    assert off["mfa_required"] is False and off["device_approval_required"] is False
    on = _post({**base, "mfa_required": "on", "device_approval_required": "on"})
    assert on["mfa_required"] is True and on["device_approval_required"] is True


def test_workspace_policy_form_carries_the_flags_marker():
    src = open("templates/accounts/admin/auth_dashboard_workspace.html", encoding="utf-8").read()
    assert 'name="policy_flags_present"' in src
