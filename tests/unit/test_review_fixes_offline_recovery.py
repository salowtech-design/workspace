"""post-restore reconciliation builds table names from a prefix; the prefix must be validated."""

from unittest import mock

import pytest

from apps.accounts.auth.offline_recovery import execute_post_restore_security_reconciliation


@pytest.mark.parametrize("prefix", ["x`; DROP TABLE a;--", "a b", "a-b", "é"])
def test_rejects_unsafe_table_prefix(prefix):
    cursor = mock.Mock()
    with pytest.raises(ValueError):
        execute_post_restore_security_reconciliation(cursor, prefix)
    cursor.execute.assert_not_called()


@pytest.mark.parametrize("prefix", ["", "salow_", "T1_"])
def test_accepts_plain_table_prefix(prefix):
    cursor = mock.Mock(rowcount=2)
    result = execute_post_restore_security_reconciliation(cursor, prefix)
    assert cursor.execute.call_count == 4
    assert result["iam_sessions_revoked"] == 2
    assert f"`{prefix}accounts_iamsession`" in cursor.execute.call_args_list[0].args[0]
