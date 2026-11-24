from django.urls import path

from apps.accounts.views.activation import (
    activate_code_view,
    activate_mfa_setup_view,
    activate_mfa_verify_view,
    activate_password_view,
    activate_recovery_codes_view,
)
from apps.accounts.views.admin_iam import (
    admin_iam_dashboard_view,
    admin_iam_live_stream_view,
    admin_security_shield_view,
)
from apps.accounts.views.device_approval import (
    check_pending_device_approval_view,
    device_admin_approve_view,
    device_approval_status_view,
    device_approval_wait_view,
    device_decision_view,
    device_email_otp_view,
    device_face_verify_view,
)
from apps.accounts.views.security_operations import (
    security_alert_acknowledge_view,
    security_alerts_view,
    security_events_view,
)
from apps.accounts.views.login import (
    login_view,
    logout_view,
    mfa_challenge_view,
)
from apps.accounts.views.passkeys import (
    passkey_auth_options_view,
    passkey_auth_verify_view,
    passkey_delete_view,
    passkey_register_options_view,
    passkey_register_verify_view,
)
from apps.accounts.views.recovery import (
    admin_approve_recovery_view,
    admin_recovery_console_view,
    admin_reject_recovery_view,
    email_reset_landing_view,
    email_set_password_view,
    forgot_password_view,
    mfa_replacement_recovery_codes_view,
    mfa_replacement_setup_view,
    mfa_replacement_verify_view,
    passkey_recovery_verify_view,
    verify_email_view,
)
from apps.accounts.views.security import (
    change_password_view,
    console_auth_events_view,
    get_draft_view,
    idle_lock_view,
    session_keepalive_view,
    session_status_view,
    idle_unlock_view,
    save_draft_view,
    security_dashboard_view,
    sudo_elevation_view,
    terminate_all_other_sessions_view,
    terminate_session_view,
)

app_name = "accounts"

urlpatterns = [
    # Everyday Authentication
    path("login/", login_view, name="login"),
    path("mfa/challenge/", mfa_challenge_view, name="mfa_challenge"),
    path("logout/", logout_view, name="logout"),
    # First-Time Activation Flow
    path("activate/", activate_code_view, name="activate"),
    path("activate/password/", activate_password_view, name="activate_password"),
    path("activate/mfa/setup/", activate_mfa_setup_view, name="activate_mfa_setup"),
    path("activate/mfa/verify/", activate_mfa_verify_view, name="activate_mfa_verify"),
    path(
        "activate/recovery-codes/",
        activate_recovery_codes_view,
        name="activate_recovery_codes",
    ),
    # Central Admin IAM & Security Shield Area
    path("admin/iam/", admin_iam_dashboard_view, name="admin_iam"),
    path("admin/iam/live-stream/", admin_iam_live_stream_view, name="admin_iam_live_stream"),
    path("admin/shield/", admin_security_shield_view, name="admin_shield"),
    # Password Recovery & Multi-Factor Workflows (Methods 1, 2, 3)
    path("recovery/forgot-password/", forgot_password_view, name="forgot_password"),
    path("recovery/email/reset/", email_reset_landing_view, name="email_reset_landing"),
    path("recovery/password/set/", email_set_password_view, name="email_set_password"),
    path("recovery/mfa/verify/", mfa_replacement_verify_view, name="mfa_replacement_verify"),
    path("recovery/mfa/setup/", mfa_replacement_setup_view, name="mfa_replacement_setup"),
    path(
        "recovery/mfa/recovery-codes/",
        mfa_replacement_recovery_codes_view,
        name="mfa_replacement_recovery_codes",
    ),
    path(
        "recovery/passkey/verify/",
        passkey_recovery_verify_view,
        name="passkey_recovery_verify",
    ),
    path("recovery/console/", admin_recovery_console_view, name="admin_recovery_console"),
    path(
        "recovery/console/<int:request_id>/approve/",
        admin_approve_recovery_view,
        name="admin_approve_recovery",
    ),
    path(
        "recovery/console/<int:request_id>/reject/",
        admin_reject_recovery_view,
        name="admin_reject_recovery",
    ),
    # Company Email Ownership Verification
    path("email/verify/", verify_email_view, name="verify_email"),
    # Passkeys (WebAuthn / FIDO2)
    path(
        "passkeys/register/options/",
        passkey_register_options_view,
        name="passkey_register_options",
    ),
    path(
        "passkeys/register/verify/",
        passkey_register_verify_view,
        name="passkey_register_verify",
    ),
    path(
        "passkeys/auth/options/",
        passkey_auth_options_view,
        name="passkey_auth_options",
    ),
    path(
        "passkeys/auth/verify/",
        passkey_auth_verify_view,
        name="passkey_auth_verify",
    ),
    path(
        "passkeys/<int:passkey_id>/delete/",
        passkey_delete_view,
        name="passkey_delete",
    ),
    # Security, Password & Session Management
    path("security/", security_dashboard_view, name="security"),
    path("security/sudo/", sudo_elevation_view, name="sudo"),
    path("security/password/change/", change_password_view, name="change_password"),
    path(
        "security/sessions/<str:session_key>/terminate/",
        terminate_session_view,
        name="terminate_session",
    ),
    path(
        "security/sessions/terminate-all-others/",
        terminate_all_other_sessions_view,
        name="terminate_all_other_sessions",
    ),
    # Idle Lock & Draft Persistence
    path("session/status/", session_status_view, name="session_status"),
    path("session/keepalive/", session_keepalive_view, name="session_keepalive"),
    path("security/idle-lock/", idle_lock_view, name="idle_lock"),
    path("security/idle-unlock/", idle_unlock_view, name="idle_unlock"),
    path("security/draft/save/", save_draft_view, name="save_draft"),
    path("security/draft/get/", get_draft_view, name="get_draft"),
    # Admin Audit Console
    path("console/auth-events/", console_auth_events_view, name="console_auth_events"),
    # Second-Device Verification Workflow (Section 9)
    path(
        "devices/approval/pending-check/",
        check_pending_device_approval_view,
        name="check_pending_device_approval",
    ),
    path(
        "devices/approval/<uuid:request_id>/wait/",
        device_approval_wait_view,
        name="device_approval_wait",
    ),
    path(
        "devices/approval/<uuid:request_id>/status/",
        device_approval_status_view,
        name="device_approval_status",
    ),
    path(
        "devices/approval/<uuid:request_id>/decision/",
        device_decision_view,
        name="device_decision",
    ),
    path(
        "devices/approval/<uuid:request_id>/email-otp/",
        device_email_otp_view,
        name="device_email_otp",
    ),
    path(
        "devices/approval/<uuid:request_id>/face/",
        device_face_verify_view,
        name="device_face_verify",
    ),
    path(
        "devices/approval/<uuid:request_id>/admin-approve/",
        device_admin_approve_view,
        name="device_admin_approve",
    ),
    # Dedicated Security Operations (Section 13)
    path("security/events/", security_events_view, name="security_events"),
    path("security/alerts/", security_alerts_view, name="security_alerts"),
    path(
        "security/alerts/<int:alert_id>/acknowledge/",
        security_alert_acknowledge_view,
        name="security_alert_acknowledge",
    ),
]
