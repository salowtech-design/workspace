"""Scratch restore refuses unsafe targets before touching anything, and restores faithfully into a safe one."""

import pytest

from apps.accounts.auth.scratch_restore import (
    ScratchRestoreRefused,
    restore_to_scratch,
    validate_target,
)


class _Cur:
    def __init__(self, dbs=(), tables=()):
        self.dbs, self.tables, self.sql = list(dbs), list(tables), []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._last = sql

    def fetchall(self):
        if self._last.startswith("SHOW DATABASES"):
            return [(d,) for d in self.dbs]
        if self._last.startswith("SHOW TABLES"):
            return [(t,) for t in self.tables]
        return []


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass


PAYLOAD = {"database": "salow_prod", "tables": {}}


@pytest.mark.parametrize(
    "name",
    [
        "salow_prod",
        "app",
        "",
        "scratch_",
        "scratch_X",
        "scratch_a;drop",
        "mysql",
        "scratch_a`b",
        "SCRATCH_a",
    ],
)
def test_unsafe_target_names_are_refused(name):
    with pytest.raises(ScratchRestoreRefused):
        validate_target(name, backup_db="salow_prod", app_db="app")


def test_name_equal_to_app_database_is_refused_even_with_prefix():
    with pytest.raises(ScratchRestoreRefused):
        validate_target("scratch_app", backup_db="x", app_db="scratch_app")


def test_non_empty_existing_database_is_refused_and_nothing_is_written():
    cur = _Cur(dbs=["scratch_a"], tables=["users"])
    with pytest.raises(ScratchRestoreRefused):
        restore_to_scratch(_Conn(cur), PAYLOAD, "scratch_a", app_db="app")
    assert not any(s.startswith(("CREATE", "DROP", "INSERT", "USE")) for s in cur.sql)


def test_unsafe_table_and_column_names_in_archive_are_refused():
    bad = {
        "database": "d",
        "tables": {"t`; DROP": {"create_sql": "", "columns": [], "rows": [], "row_count": 0}},
    }
    with pytest.raises(ScratchRestoreRefused):
        restore_to_scratch(_Conn(_Cur()), bad, "scratch_ok", app_db="app")
    bad2 = {
        "database": "d",
        "tables": {
            "t": {
                "create_sql": "CREATE TABLE `t` (a int)",
                "columns": ["a`b"],
                "rows": [[1]],
                "row_count": 1,
            }
        },
    }
    with pytest.raises(ScratchRestoreRefused):
        restore_to_scratch(_Conn(_Cur()), bad2, "scratch_ok", app_db="app")
