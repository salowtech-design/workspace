"""Empty-state messages sit outside the scrolling tables, and appear only for an empty list."""
import re
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from django.template.loader import render_to_string

DIR_MSG = "No employees match the search filter."
SEC_MSGS = {
    "passkeys": "No passkeys registered.",
    "sessions": "No active sessions found.",
    "recent_events": "No recent security events recorded.",
}


def _user():
    return NS(
        employee_id="E1", full_name="A B", pk=1, id=1, email="a@b.c", get_role_display="Employee",
        department=None, profile=None, role="EMPLOYEE", is_active=True,
    )


def _dir(users):
    return render_to_string(
        "directory/list.html",
        {"users": users, "departments": [], "query": "", "selected_dept": ""},
    )


def _sec(**lists):
    ctx = {"passkeys": [], "sessions": [], "recent_events": []}
    ctx.update(lists)
    return render_to_string("accounts/security/dashboard.html", ctx)


def _tables_text(page):
    return " ".join(re.findall(r"<table.*?</table>", page, re.S))


def test_directory_empty_message_present_and_outside_table():
    page = _dir([])
    assert DIR_MSG in page
    assert DIR_MSG not in _tables_text(page)
    assert "<th>" in _tables_text(page) or "<th" in _tables_text(page)  # columns stay


def test_directory_message_absent_with_rows():
    page = _dir([_user()])
    assert DIR_MSG not in page
    assert "E1" in page


@pytest.mark.parametrize("name", list(SEC_MSGS))
def test_security_empty_message_present_and_outside_table(name):
    page = _sec()
    assert SEC_MSGS[name] in page
    assert SEC_MSGS[name] not in _tables_text(page)


def test_security_messages_absent_with_rows():
    now = datetime(2026, 1, 1, 12, 0)
    pk = NS(pk=1, id=1, name="K", created_at=now, last_used_at=now, is_active=True, credential_id="c")
    sess = NS(pk=1, id=1, session_key="s", user_agent="UA", ip_address="1.1.1.1", last_activity=now,
              created_at=now, device_label="D", is_current=False, status="ACTIVE", get_status_display="Active")
    evt = NS(pk=1, id=1, event_type="LOGIN", get_event_type_display="Login", created_at=now, outcome="OK",
             network_address="1.1.1.1", success=True, timestamp=now)
    page = _sec(passkeys=[pk], sessions=[sess], recent_events=[evt])
    for msg in SEC_MSGS.values():
        assert msg not in page
    # each list alone also silences only its own message
    only = _sec(passkeys=[pk])
    assert SEC_MSGS["passkeys"] not in only and SEC_MSGS["sessions"] in only and SEC_MSGS["recent_events"] in only
