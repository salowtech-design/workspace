"""Custodian-run restore of a decrypted backup into an empty, explicitly named SCRATCH database.

This is for restore verification only. It never writes to the application database:
- the target name must start with "scratch_" and use only lowercase letters, digits and underscores;
- it must differ from the database name recorded in the backup and from the configured app database;
- it must not exist yet, or exist with zero tables. A database that already holds tables is refused;
- after loading, every table is compared by content and row count, then the scratch database is dropped
  unless the caller asks to keep it for inspection.
No online code path calls this. It runs from scripts/offline_disaster_recovery.py after 2-of-3 shares.
"""

import base64
import decimal
import hashlib
import json
import re

SCRATCH_NAME = re.compile(r"^scratch_[a-z0-9_]{1,48}$")
TABLE_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class ScratchRestoreRefused(Exception):
    """The target is not a safe scratch database. Nothing was written."""


def validate_target(scratch_db: str, *, backup_db: str, app_db: str) -> None:
    if not SCRATCH_NAME.match(scratch_db or ""):
        raise ScratchRestoreRefused(
            "Scratch database name must start with 'scratch_' and use lowercase letters, digits, underscores."
        )
    if scratch_db in {backup_db, app_db}:
        raise ScratchRestoreRefused("Scratch database must not be the application database.")


def _deserialize(val):
    if isinstance(val, dict):
        if val.get("__type__") == "decimal":
            return decimal.Decimal(val["value"])
        if val.get("__type__") == "bytes":
            return base64.b64decode(val["value"])
    return val


def _serialize(val):
    import datetime as _dt

    if isinstance(val, (_dt.datetime, _dt.date)):
        return val.isoformat()
    if isinstance(val, decimal.Decimal):
        return {"__type__": "decimal", "value": str(val)}
    if isinstance(val, (bytes, bytearray)):
        return {"__type__": "bytes", "value": base64.b64encode(bytes(val)).decode("ascii")}
    return val


def _fingerprint(rows) -> str:
    lines = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _load_table(conn, cur, scratch_db: str, name: str, info: dict) -> int:
    create_sql = info["create_sql"]
    if not create_sql.startswith(f"CREATE TABLE `{name}`"):
        raise ScratchRestoreRefused(f"Unexpected schema statement for table {name}.")
    cols, rows = info["columns"], info["rows"]
    if not all(TABLE_NAME.match(c) for c in cols):
        raise ScratchRestoreRefused(f"Unsafe column name in archive for table {name}.")
    cur.execute(create_sql)
    if rows:
        col_names = ", ".join(f"`{c}`" for c in cols)
        marks = ", ".join(["%s"] * len(cols))
        # Names are validated against strict patterns above; values are bound parameters.
        sql = f"INSERT INTO `{scratch_db}`.`{name}` ({col_names}) VALUES ({marks});"  # noqa: S608
        for r in rows:
            cur.execute(sql, tuple(_deserialize(v) for v in r))
        conn.commit()
    cur.execute(f"SELECT * FROM `{scratch_db}`.`{name}`;")  # noqa: S608
    got = [[_serialize(v) for v in row] for row in cur.fetchall()]
    if len(got) != info["row_count"] or _fingerprint(got) != _fingerprint(rows):
        raise ScratchRestoreRefused(f"Restored content differs from the backup for table {name}.")
    return len(got)


def restore_to_scratch(
    conn, payload: dict, scratch_db: str, *, app_db: str, keep: bool = False
) -> dict:
    """Load payload into scratch_db and verify content. Returns a summary. Raises on any refusal or mismatch."""
    validate_target(scratch_db, backup_db=str(payload.get("database", "")), app_db=app_db)
    cur = conn.cursor()
    cur.execute("SHOW DATABASES;")
    existing = {row[0] for row in cur.fetchall()}
    created = False
    if scratch_db in existing:
        cur.execute(f"SHOW TABLES FROM `{scratch_db}`;")
        if cur.fetchall():
            raise ScratchRestoreRefused(f"Database {scratch_db} already contains tables; refusing.")
    else:
        cur.execute(f"CREATE DATABASE `{scratch_db}`;")
        created = True
    tables = payload.get("tables", {})
    for name in tables:
        if not TABLE_NAME.match(name):
            raise ScratchRestoreRefused(f"Unsafe table name in archive: {name[:40]!r}")
    verified_rows = 0
    try:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0;")
        cur.execute(f"USE `{scratch_db}`;")
        for name, info in tables.items():
            verified_rows += _load_table(conn, cur, scratch_db, name, info)
    finally:
        cur.execute("SET FOREIGN_KEY_CHECKS = 1;")
        if not keep and (created or tables):
            cur.execute(f"DROP DATABASE IF EXISTS `{scratch_db}`;")
        conn.commit()
    return {"tables": len(tables), "rows": verified_rows, "kept": keep, "database": scratch_db}
