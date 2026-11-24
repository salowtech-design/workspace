"""Local review settings: dev settings plus the two dedicated hostnames, plain HTTP, SQLite only.

Use with a hosts-file entry mapping iam.salowtech.com and app.salowtech.com to 127.0.0.1.
Never use against a real database: the synthetic-data seeder refuses anything except local SQLite.
"""

from .dev import *  # noqa: F403
from .dev import ALLOWED_HOSTS as _DEV_HOSTS

ALLOWED_HOSTS = [*_DEV_HOSTS, "iam.salowtech.com", "app.salowtech.com"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
