import uuid

from django.conf import settings
from django.db import models

BODY_MAX = 8000
NAME_MAX = 120


class Space(models.Model):
    """A direct message (exactly two people) or a named group. Channels, threads, reactions,
    mentions, attachments, edit, delete and retention are NOT built."""

    DM, GROUP = "dm", "group"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=8)
    name = models.CharField(max_length=NAME_MAX, blank=True)
    last_seq = models.BigIntegerField(default=0)  # per-space order, set under the row lock
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)


class Member(models.Model):
    MEMBER, MANAGER = "member", "manager"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="members")
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_memberships"
    )
    role = models.CharField(max_length=8, default=MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    last_read_seq = models.BigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["space", "person"], name="chat_member_uniq")]
        indexes = [models.Index(fields=["person", "left_at"])]


class Message(models.Model):
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="messages")
    seq = models.BigIntegerField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_messages"
    )
    body = models.TextField()
    client_msg_id = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["space", "seq"], name="chat_msg_seq_uniq"),
            models.UniqueConstraint(
                fields=["space", "author", "client_msg_id"], name="chat_msg_client_uniq"
            ),
        ]


class DmPair(models.Model):
    """One row per pair of people, lower id first. The unique key is what stops two DM spaces."""

    user_low = models.BigIntegerField()
    user_high = models.BigIntegerField()
    space = models.OneToOneField(Space, on_delete=models.CASCADE, related_name="pair")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_low", "user_high"], name="dm_pair_uniq")
        ]
