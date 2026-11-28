"""Reviewed model-file gate for the face pipeline (adapter logic only).

A model file may be used only if its SHA-256 is pinned in REVIEWED_MODEL_SHA256 for its role. The table is EMPTY: no weight
file has been reviewed (licence and training-data provenance of the published weights are unresolved, see docs/LICENSES.md),
so every file is refused. Adding a hash is a reviewed code change made after the provenance review, not a setting or an
environment variable. Nothing here loads, parses or runs a model, and nothing calls it yet; it does not enable the live path."""

import hashlib
import os
import stat
from typing import Mapping

ROLES = ("detector", "embedder", "liveness")
MAX_MODEL_BYTES = 64 * 1024 * 1024  # build default size cap; a refusal reads no further than this

# role -> set of lowercase hex sha256 values of reviewed files. Empty on purpose.
REVIEWED_MODEL_SHA256: Mapping[str, frozenset] = {role: frozenset() for role in ROLES}


class ModelFileNotReviewed(Exception):
    """The file is missing, not a regular file, too large, or its hash is not pinned for the role. Fail closed."""


def verified_model_path(role: str, path: str, allowlist: Mapping[str, frozenset] = REVIEWED_MODEL_SHA256) -> str:
    """Return the real path of the file only if it is a regular file whose sha256 (over at most MAX_MODEL_BYTES read) is pinned.

    The `allowlist` argument exists for tests only; production callers must use the module table.
    TOCTOU: the returned path was checked at THIS moment only. The file can change afterwards, so a future loader must not
    reopen the returned path; it must hash and load the same opened bytes or descriptor in one step."""
    if role not in ROLES:
        raise ModelFileNotReviewed(f"Unknown model role {role!r}.")
    pinned = allowlist.get(role, frozenset())
    if not pinned:
        raise ModelFileNotReviewed(f"No reviewed model file is pinned for role {role!r}.")
    try:
        real = os.path.realpath(path)
        st = os.stat(real)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_MODEL_BYTES:
            raise ModelFileNotReviewed("Model file is not a regular file within the size limit.")
        digest = hashlib.sha256()
        total = 0
        with open(real, "rb") as fh:
            while True:
                chunk = fh.read(min(1024 * 1024, MAX_MODEL_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_MODEL_BYTES:
                    raise ModelFileNotReviewed("Model file is not a regular file within the size limit.")
                digest.update(chunk)
    except (OSError, ValueError):
        raise ModelFileNotReviewed("Model file is not readable.") from None
    if digest.hexdigest() not in pinned:
        raise ModelFileNotReviewed("Model file hash is not in the reviewed list.")
    return real
