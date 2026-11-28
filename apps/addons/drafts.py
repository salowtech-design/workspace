"""Add-ons private drafts: a catalogue of MY add-ons, a draft editor and a view-only review page.
Not a completed module. Nothing here submits, approves, suspends, installs, delivers, or runs an add-on; perform() in
services.py still refuses everyone. Only the maker can see or change a draft (an implementation default, not an approved
policy: who may SEE other people's add-ons, review them or install them is undecided). A saved version is always a draft.
Manifests are checked by validate_manifest with its EMPTY registries, so a manifest that names a scope or a hook is refused."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404

from apps.addons.manifest import NAME_MAX, ManifestError, _plain, validate_manifest
from apps.addons.models import Addon, AddonVersion
from apps.core.foundation.audit import audit

MAX_ADDONS, MAX_VERSIONS, MANIFEST_TEXT_MAX = 20, 20, 20000  # build defaults


def mine(actor):
    return Addon.objects.filter(owner=actor).order_by("-updated_at")[:MAX_ADDONS]


def get_mine(actor, public_id):
    a = Addon.objects.filter(public_id=public_id, owner_id=actor.pk).first()
    if a is None:
        raise Http404  # a missing add-on and someone else's add-on look the same
    return a


def create_addon(actor, name):
    problems = []
    name = _plain(name, NAME_MAX, "Name", problems)  # the same rules the manifest check applies, so a created name can be saved
    if problems:
        raise ValidationError(problems)
    with transaction.atomic():
        # lock the owner's user row first so two creates by one person cannot both pass the count (quota check, nothing more)
        get_user_model().objects.select_for_update().filter(pk=actor.pk).first()
        if Addon.objects.filter(owner=actor).count() >= MAX_ADDONS:
            raise ValidationError(f"You can have at most {MAX_ADDONS} add-ons.")
        a = Addon.objects.create(name=name, owner=actor)
        audit(actor, "addons.draft_create", "addon", a.pk)
    return a


def save_version(actor, public_id, manifest_text):
    """Validate and store a DRAFT manifest, creating the version or replacing the same version number while it is a draft."""
    if not isinstance(manifest_text, str) or len(manifest_text) > MANIFEST_TEXT_MAX:
        raise ValidationError(f"The manifest must be text of at most {MANIFEST_TEXT_MAX} characters.")
    try:
        raw = json.loads(manifest_text)
    except ValueError:
        raise ValidationError("The manifest is not valid JSON.") from None
    except RecursionError:
        raise ValidationError("The manifest is nested too deeply.") from None
    try:
        clean = validate_manifest(raw)
    except ManifestError as exc:
        raise ValidationError(list(exc.args[0]) if exc.args and isinstance(exc.args[0], list) else [str(exc)]) from None
    with transaction.atomic():
        addon = Addon.objects.select_for_update().filter(public_id=public_id, owner_id=actor.pk).first()
        if addon is None:
            raise Http404
        if clean["name"] != addon.name:
            raise ValidationError("The manifest name must match the add-on name.")
        v = addon.versions.filter(version=clean["version"]).first()
        if v is not None:
            if v.review_state != Addon.DRAFT:
                raise ValidationError("That version is no longer a draft.")
            v.manifest = clean
            v.save()
        else:
            if addon.versions.count() >= MAX_VERSIONS:
                raise ValidationError(f"At most {MAX_VERSIONS} versions.")
            try:
                with transaction.atomic():
                    v = AddonVersion.objects.create(addon=addon, version=clean["version"], manifest=clean)
            except IntegrityError:
                raise ValidationError("That version was just saved elsewhere. Reload.") from None
        addon.save()  # touches updated_at
        audit(actor, "addons.draft_save", "addon", addon.pk, version=clean["version"])
    return v


def starter(addon):
    return json.dumps({"name": addon.name, "version": "1.0.0", "cards": [{"title": "Hello", "blocks": [{"type": "text", "text": "A plain text card."}]}]}, indent=2)
