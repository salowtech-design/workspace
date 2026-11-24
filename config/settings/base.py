"""
Base Django settings for Salow Workspace.
Shared across development, testing, and production environments.
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Application definition
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.directory.apps.DirectoryConfig",
    "apps.attendance.apps.AttendanceConfig",
    "apps.face.apps.FaceConfig",
    "apps.posts.apps.PostsConfig",
    "apps.certificates.apps.CertificatesConfig",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.core.middleware.RateLimitMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.HostSecurityIsolationMiddleware",
    "apps.accounts.middleware.AccountSecurityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.csp_nonce",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Custom User Model (enforced before initial migrations)
AUTH_USER_MODEL = "accounts.CustomUser"

# Password Validation & Hashing
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 12,
        },
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization & Timezone (Mandatory: Asia/Kolkata business date)
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Session & Cookie Security Defaults (Phase 1 v4.0 & v6.0 Section 10, Security Contract v1.0 Section 4)
SESSION_ENGINE = "apps.accounts.auth.session_backend"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 43200  # Strictly 12 hours absolute lifetime (Section 10)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True  # Fresh authentication on browser close/reopen (Section 10)

# In production (HTTPS), prefix with __Host- per RFC 6265bis.
# In insecure HTTP dev/test, use un-prefixed name because user agents drop __Host- on plain HTTP.
if os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true":
    SESSION_COOKIE_NAME = "__Host-salow_workspace_session"
    IAM_SESSION_COOKIE_NAME = "__Host-salow_iam_session"
else:
    SESSION_COOKIE_NAME = "salow_workspace_session"
    IAM_SESSION_COOKIE_NAME = "salow_iam_session"

CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# Login & Logout redirection targets
LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/attendance/"
LOGOUT_REDIRECT_URL = "/auth/login/"

# Audit Checkpoint Independent Storage & Custody (Subsystem 1)
AUDIT_CHECKPOINT_DIR = Path(os.environ.get("AUDIT_CHECKPOINT_DIR", BASE_DIR / "var" / "audit_checkpoints"))
AUDIT_ACTIVE_KEY_ID = os.environ.get("AUDIT_ACTIVE_KEY_ID", "k1")

# Hostname Routing & Device Capacity Configuration (Final IAM & Multi-Device)
IAM_HOSTNAME = os.environ.get("IAM_HOSTNAME", "iam.salowtech.com")
WORKSPACE_HOSTNAME = os.environ.get("WORKSPACE_HOSTNAME", "app.salowtech.com")
MAX_CONCURRENT_DEVICES = int(os.environ.get("MAX_CONCURRENT_DEVICES", "3"))


