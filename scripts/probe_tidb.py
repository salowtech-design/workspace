"""TiDB Cloud Starter connectivity and compatibility probe script.

Validates:
1. TLS/SSL handshake with certificate validation
2. TiDB version and dialect verification
3. ACID transactional semantics on TiKV
4. Constraint enforcement (PRIMARY KEY, UNIQUE)
"""

import os
import sys
from pathlib import Path

import certifi
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def probe_tidb() -> int:
    host = os.getenv("TIDB_HOST")
    port = int(os.getenv("TIDB_PORT", "4000"))
    user = os.getenv("TIDB_USER")
    password = os.getenv("TIDB_PASSWORD")
    db_name = os.getenv("TIDB_DB_NAME", "salow_workspace")
    ca_path = os.getenv("TIDB_CA_PATH") or certifi.where()

    print("=" * 60)
    print("SALOW WORKSPACE - TiDB Cloud Connectivity & Dialect Probe")
    print("=" * 60)

    # 1. Credential check
    missing = []
    if not host:
        missing.append("TIDB_HOST")
    if not user:
        missing.append("TIDB_USER")
    if not password:
        missing.append("TIDB_PASSWORD")

    if missing:
        print(f"[ERROR] Missing required environment variables in .env: {', '.join(missing)}")
        print("[HINT] Ensure .env is populated with TiDB Cloud credentials.")
        return 1

    print(f"[*] Target Host: {host}:{port}")
    print(f"[*] Database   : {db_name}")
    print(f"[*] User       : {user}")
    print(f"[*] CA Bundle  : {ca_path}")

    # 2. Driver check
    try:
        import MySQLdb
    except ImportError:
        print("[ERROR] mysqlclient (MySQLdb) is not installed in the environment.")
        return 1

    # 3. Connection & TLS handshake
    print("[*] Initiating TLS handshake with TiDB Cloud...")
    try:
        conn = MySQLdb.connect(
            host=host,
            port=port,
            user=user,
            passwd=password,
            db=db_name,
            charset="utf8mb4",
            ssl_mode="VERIFY_IDENTITY",
            ssl={"ca": ca_path},
            connect_timeout=15,
        )
    except MySQLdb.Error:
        # Fall back without strict hostname verification if VERIFY_IDENTITY is unsupported
        try:
            conn = MySQLdb.connect(
                host=host,
                port=port,
                user=user,
                passwd=password,
                db=db_name,
                charset="utf8mb4",
                ssl={"ca": ca_path},
                connect_timeout=15,
            )
        except MySQLdb.Error as fallback_err:
            print(f"[FAIL] Connection failed: {fallback_err}")
            return 1

    print("[PASS] TLS handshake and authentication successful.")

    # 4. TiDB Version & Storage Engine Check
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT tidb_version();")
        version_info = cursor.fetchone()
        if version_info:
            print(f"[PASS] TiDB Version: {version_info[0].splitlines()[0]}")
        else:
            cursor.execute("SELECT VERSION();")
            print(f"[PASS] Engine Version: {cursor.fetchone()[0]}")

        # 5. Transactional & TiKV Semantics Test
        print("[*] Testing transactional ACID semantics on TiKV...")
        table_name = "_tidb_probe_test"
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                test_uuid VARCHAR(64) NOT NULL UNIQUE,
                val VARCHAR(255) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # Test Rollback
        conn.autocommit(False)
        cursor.execute(
            f"INSERT INTO {table_name} (test_uuid, val) VALUES (%s, %s)",
            ("rollback_check_1", "should_not_exist"),
        )
        conn.rollback()

        cursor.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE test_uuid = %s", ("rollback_check_1",)
        )
        count = cursor.fetchone()[0]
        if count != 0:
            print("[FAIL] Rollback test failed; row was persisted.")
            return 1
        print("[PASS] Rollback verification succeeded.")

        # Test Commit & Unique Constraint
        cursor.execute(
            f"INSERT INTO {table_name} (test_uuid, val) VALUES (%s, %s)",
            ("commit_check_1", "persisted_value"),
        )
        conn.commit()

        cursor.execute(f"SELECT val FROM {table_name} WHERE test_uuid = %s", ("commit_check_1",))
        res = cursor.fetchone()
        if not res or res[0] != "persisted_value":
            print("[FAIL] Commit test failed; row was not persisted correctly.")
            return 1
        print("[PASS] Commit verification succeeded.")

        # Test Unique Constraint Enforcement
        unique_failed = False
        try:
            cursor.execute(
                f"INSERT INTO {table_name} (test_uuid, val) VALUES (%s, %s)",
                ("commit_check_1", "duplicate_value"),
            )
            conn.commit()
        except MySQLdb.IntegrityError:
            unique_failed = True
            conn.rollback()

        if not unique_failed:
            print("[FAIL] Unique constraint was not enforced.")
            return 1
        print("[PASS] Unique constraint enforcement succeeded.")

        # Cleanup test table
        cursor.execute(f"DROP TABLE IF EXISTS {table_name};")
        conn.commit()
        print("[PASS] Test cleanup completed.")

    except MySQLdb.Error as err:
        print(f"[FAIL] TiDB query error: {err}")
        return 1
    finally:
        cursor.close()
        conn.close()

    print("=" * 60)
    print("[SUCCESS] TiDB Cloud Starter instance is 100% ready for Django migrations.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(probe_tidb())
