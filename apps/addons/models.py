"""Add-ons step 2: tables only (blueprint 9.11 addon, addon_version, addon_install).

BUILD STATE: schema and a pure state machine. No view, no install service, no webhook table, no API token, and no grant of
any kind exists. WHO may submit, approve, suspend or install, whether installs are per organisation or per user, and the
list of scopes are user-owned product and security choices that are not made; see services.py, which refuses everyone.

Invariants and what enforces them: choices= and the CheckConstraints below are declarations. Django validates choices only in
full_clean(), not on save(). A database enforces a CheckConstraint only if it is configured to; the repo notes that TiDB may
ignore them depending on tidb_enable_check_constraint, but I did not verify that cause. tests/unit/test_addon_schema.py records
what the real TiDB run OBSERVED for a bad status (not rejected in the run reported with this change) and does not require either
outcome. check_install_row is a helper that nothing calls; it is not model enforcement. Foreign keys use PROTECT, so the one ORM
path tested (Addon.delete() with a version) refuses; queryset deletes, raw SQL and user-account deletion were not tested. No delete
path exists and none should be built until retention is decided. Future design note: PROTECT on owner and installed_by will interact
with any user-erasure process, which is not designed yet.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

NAME_MAX = 80


class Addon(models.Model):
    DRAFT, REVIEW, APPROVED, SUSPENDED = "draft", "review", "approved", "suspended"
    STATES = (DRAFT, REVIEW, APPROVED, SUSPENDED)

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=NAME_MAX)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_addons")
    status = models.CharField(max_length=10, default=DRAFT, choices=[(x, x) for x in STATES])  # how this derives from version states is undecided and unbuilt
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(status__in=("draft", "review", "approved", "suspended")), name="addon_status_valid")]


class AddonVersion(models.Model):
    addon = models.ForeignKey(Addon, on_delete=models.PROTECT, related_name="versions")  # PROTECT: no cascade delete of review history
    version = models.CharField(max_length=20)
    # Meant to hold the output of apps.addons.manifest.validate_manifest, but NOTHING enforces that: no writer exists yet and
    # the field is not checked. Scopes are not bounded by the installer's rights either (the scope list is empty and unbuilt).
    manifest = models.JSONField()
    review_state = models.CharField(max_length=10, default=Addon.DRAFT, choices=[(x, x) for x in Addon.STATES])  # the state the transitions in services.py move
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["addon", "version"], name="addon_version_unique"),
            models.CheckConstraint(check=models.Q(review_state__in=("draft", "review", "approved", "suspended")), name="addon_version_state_valid"),
        ]  # fmt: skip


class AddonInstall(models.Model):
    ORG, USER = "org", "user"

    addon = models.ForeignKey(Addon, on_delete=models.PROTECT, related_name="installs")
    scope_target = models.CharField(max_length=4, choices=[("org", "org"), ("user", "user")])
    target_id = models.CharField(max_length=40, blank=True, default="")  # the person's public id for a user install, empty for org
    granted_scopes = models.JSONField(default=list)
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["addon", "scope_target", "target_id"], name="addon_install_unique"),
            models.CheckConstraint(
                check=(models.Q(scope_target="org", target_id="") | (models.Q(scope_target="user") & ~models.Q(target_id=""))),
                name="addon_install_target_matches_scope",
            ),
        ]  # fmt: skip


def check_install_row(scope_target, target_id):
    """Unwired helper, NOT model enforcement: org installs have an empty target, user installs a non-empty one."""
    if scope_target == AddonInstall.ORG and target_id == "":
        return
    if scope_target == AddonInstall.USER and target_id != "":
        return
    raise ValidationError("An org install has no target and a user install needs one.")
