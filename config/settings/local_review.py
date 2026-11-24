"""Local review settings: dev settings plus the two dedicated hostnames, plain HTTP, local SQLite only.

The database is forced to a local SQLite file here, whatever TIDB_* or USE_TIDB variables are set, so
`migrate`, `runserver` and the seeder can never reach a real database with these settings.
Use with a hosts-file entry mapping iam.salowtech.com and app.salowtech.com to 127.0.0.1.
"""

from .dev import *  # noqa: F403
from .dev import ALLOWED_HOSTS as _DEV_HOSTS
from .dev import BASE_DIR

ALLOWED_HOSTS = [*_DEV_HOSTS, "iam.salowtech.com", "app.salowtech.com"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "local_review.sqlite3",
    }
}
# Plain HTTP: no Secure cookies and no __Host- prefix (browsers drop __Host- cookies over HTTP).
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_NAME = "salow_workspace_session"
IAM_SESSION_COOKIE_NAME = "salow_iam_session"
