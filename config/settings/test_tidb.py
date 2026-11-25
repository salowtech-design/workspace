"""Test settings that run the suite against a real TiDB server instead of SQLite.

Point TIDB_TEST_HOST/PORT at an isolated TiDB (for example a local tidb-server). Never use
the production cluster: Django creates and drops a database named test_<NAME>.
"""

import os
import tempfile
from pathlib import Path

from .test import *  # noqa: F403

# Scratch endpoint must be named explicitly and be on this machine. No default port, so a missing
# variable can never fall through to a real server on 4000.
_HOST = os.environ.get("TIDB_TEST_HOST", "")
_PORT = os.environ.get("TIDB_TEST_PORT", "")
if _HOST not in ("127.0.0.1", "localhost", "::1") or not _PORT.isdigit():
    raise RuntimeError(
        "Set TIDB_TEST_HOST to a loopback address (127.0.0.1) and TIDB_TEST_PORT to the port of a "
        "scratch TiDB. These settings refuse any other host and have no default port."
    )

# config.settings.test pins the audit checkpoint folder inside the repo. Take it from the run
# instead, or use a fresh temporary folder, so a run never writes into the repository.
AUDIT_CHECKPOINT_DIR = Path(
    os.environ.get("AUDIT_CHECKPOINT_DIR") or tempfile.mkdtemp(prefix="audit-")
)

DATABASES = {
    "default": {
        "ENGINE": "django_tidb",
        "NAME": os.environ.get("TIDB_TEST_DB_NAME", "salow_test"),
        "USER": os.environ.get("TIDB_TEST_USER", "root"),
        "PASSWORD": os.environ.get("TIDB_TEST_PASSWORD", ""),
        "HOST": _HOST,
        "PORT": int(_PORT),
        "OPTIONS": {"charset": "utf8mb4"},
        "TEST": {"NAME": "test_" + os.environ.get("TIDB_TEST_DB_NAME", "salow_test")},
    }
}
