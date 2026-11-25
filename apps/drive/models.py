import uuid

from django.conf import settings
from django.db import models


class Folder(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="drive_folders"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    name = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    trashed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    trash_batch = models.UUIDField(null=True, blank=True, db_index=True)
    trashed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["owner", "parent"])]


class DriveFile(models.Model):
    """File record. Bytes live behind the storage interface; only the key is kept here."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="drive_files"
    )
    folder = models.ForeignKey(
        Folder, null=True, blank=True, on_delete=models.PROTECT, related_name="files"
    )
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=127, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    current_version = models.PositiveIntegerField(default=0)  # 0 = no bytes recorded yet
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    trashed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    trash_batch = models.UUIDField(null=True, blank=True, db_index=True)
    trashed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["owner", "folder"])]


class FileVersion(models.Model):
    file = models.ForeignKey(DriveFile, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    storage_key = models.CharField(
        max_length=64, unique=True
    )  # opaque random id, never derived from a name
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["file", "number"], name="drive_version_unique")
        ]


class Share(models.Model):
    """Explicit grant to one person on one folder or file. No grant, no access."""

    class Role(models.TextChoices):
        VIEWER = "viewer", "Viewer"
        COMMENTER = "commenter", "Commenter"
        EDITOR = "editor", "Editor"

    folder = models.ForeignKey(
        Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="shares"
    )
    file = models.ForeignKey(
        DriveFile, null=True, blank=True, on_delete=models.CASCADE, related_name="shares"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="drive_shares"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(folder__isnull=False, file__isnull=True)
                | models.Q(folder__isnull=True, file__isnull=False),
                name="drive_share_exactly_one_target",
            ),
            models.UniqueConstraint(fields=["folder", "user"], name="drive_share_folder_user"),
            models.UniqueConstraint(fields=["file", "user"], name="drive_share_file_user"),
        ]


class DriveChange(models.Model):
    """Append-only change journal, one row per mutation. Trashed items stay as tombstones.

    Ids and short facts only, never names or content. Clients and later sync read it in order.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # The person whose access changed (share, unshare), so a sync client can find revocations
    # addressed to it. Null for changes that concern everyone.
    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    item_type = models.CharField(max_length=10)  # "folder" or "file"
    item_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=20)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["owner", "id"])]


class ManifestEpoch(models.Model):
    """One counter per person. Bumped only while that person's account row is locked, so the
    committed epochs for one person form a gap free sequence."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="drive_manifest_epoch"
    )
    epoch = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class ManifestChange(models.Model):
    """Append only. Each row states the ABSOLUTE state for one root, so replaying rows in
    epoch order over a newer listing converges. Public ids only, never integer pks."""

    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        TRASHED = "trashed", "Trashed"
        REVOKED = "revoked", "Revoked"

    class Scope(models.TextChoices):
        ITEM = "item", "Item"
        SUBTREE = "subtree", "Subtree"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="drive_manifest_changes"
    )
    epoch = models.PositiveBigIntegerField()
    item_type = models.CharField(max_length=10)
    item_public_id = models.UUIDField()
    scope = models.CharField(max_length=8, choices=Scope.choices)
    state = models.CharField(max_length=8, choices=State.choices)
    role = models.CharField(max_length=10, blank=True, default="")
    reason = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user_id", "epoch", "id"]
        indexes = [models.Index(fields=["user", "epoch"])]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "epoch", "item_public_id"], name="drive_manifest_row_unique"
            )
        ]
