"""Settings for the browser drills only: the TiDB test settings, plus the audit checkpoint folder taken from the run's temp dir.
config.settings.test pins AUDIT_CHECKPOINT_DIR inside the repo, which would let drills write there."""

import os
from pathlib import Path

from config.settings.test_tidb import *  # noqa: F403

AUDIT_CHECKPOINT_DIR = Path(os.environ["AUDIT_CHECKPOINT_DIR"])

if os.environ.get("LOAD_EXC_LOG"):  # load_http only: send Django request exceptions (type, message, stack) to stderr, which the run keeps in uvicorn.log
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"err": {"class": "logging.StreamHandler"}},
        "loggers": {"django.request": {"handlers": ["err"], "level": "ERROR"}, "django.db.backends": {"handlers": ["err"], "level": "ERROR"}},
    }
