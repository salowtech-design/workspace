"""Test settings that run the suite against a real TiDB server instead of SQLite.

Point TIDB_TEST_HOST/PORT at an isolated TiDB (for example a local tidb-server). Never use
the production cluster: Django creates and drops a database named test_<NAME>.
"""

import os

from .test import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django_tidb",
        "NAME": os.environ.get("TIDB_TEST_DB_NAME", "salow_test"),
        "USER": os.environ.get("TIDB_TEST_USER", "root"),
        "PASSWORD": os.environ.get("TIDB_TEST_PASSWORD", ""),
        "HOST": os.environ.get("TIDB_TEST_HOST", "127.0.0.1"),
        "PORT": int(os.environ.get("TIDB_TEST_PORT", 4000)),
        "OPTIONS": {"charset": "utf8mb4"},
        "TEST": {"NAME": "test_" + os.environ.get("TIDB_TEST_DB_NAME", "salow_test")},
    }
}
