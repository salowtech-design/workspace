"""ADAPTER LOGIC ONLY: Add-ons step 2 schema and state machine (apps/addons). Real database rows, but no view, install service,
webhook, token or grant exists, and the authority gate refuses everyone because who may act is an undecided user choice.
One machine, one run."""

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from apps.accounts.models import CustomUser
from apps.addons import services as svc
from apps.addons.manifest import validate_manifest
from apps.addons.models import Addon, AddonInstall, AddonVersion


def _user(eid="AD-1"):
    return CustomUser.objects.create(employee_id=eid, full_name=eid, email=f"{eid.lower()}@salowtech.com", role="employee",
                                     status="active", is_active=True)  # fmt: skip


def test_transitions_match_the_blueprint_figure_and_nothing_else():
    allowed = {("submit", "draft"): "review", ("approve", "review"): "approved", ("reject", "review"): "draft",
               ("suspend", "approved"): "suspended", ("restore", "suspended"): "approved"}  # fmt: skip
    for action in svc.ACTIONS:
        for state in Addon.STATES:
            if (action, state) in allowed:
                assert svc._next_state(action, state) == allowed[(action, state)]
            else:
                with pytest.raises(ValueError):
                    svc._next_state(action, state)
    for bad in (None, [], {}, "nope"):
        with pytest.raises(ValueError):
            svc._next_state("approve", bad)


def test_perform_checks_authority_before_reading_anything(db):
    for actor in (_user("AD-P"), None):
        for action in svc.ACTIONS:
            with pytest.raises(PermissionDenied):
                svc.perform(actor, action, object())  # the version argument is never touched


def test_model_validation_and_what_this_database_run_observed_for_a_bad_status(db):
    from django.core.exceptions import ValidationError

    from apps.addons.models import check_install_row

    check_install_row("org", ""), check_install_row("user", "p1")
    for bad in (("org", "p1"), ("user", ""), ("team", "x"), (None, "")):
        with pytest.raises(ValidationError):
            check_install_row(*bad)
    owner = _user("AD-V")
    addon = Addon(name="X", owner=owner, status="bogus")
    with pytest.raises(ValidationError):
        addon.full_clean()  # choices are checked by full_clean only
    for v in (AddonVersion(addon=Addon.objects.create(name="V", owner=owner), version="1.0.0", manifest={}, review_state="bogus"),):
        with pytest.raises(ValidationError):
            v.full_clean()  # model validation (choices), not database enforcement
    inst = AddonInstall(addon=Addon.objects.create(name="I", owner=owner), scope_target="team", target_id="x", installed_by=owner)
    with pytest.raises(ValidationError):
        inst.full_clean()
    # What this database run did with a bad status: recorded, not required either way (no claim about why).
    try:
        with transaction.atomic():
            Addon.objects.create(name="Y", owner=owner, status="bogus")
        rejected = False
    except IntegrityError:
        rejected = True
    stored = Addon.objects.filter(status="bogus").exists()
    assert stored is (not rejected), "a rejected insert must leave no row and an accepted one must leave one"
    open("/tmp/addon_db_check_probe.txt", "w").write(f"observed_db_rejected_bad_status={rejected}\n")


def test_addon_delete_with_a_version_is_refused_one_orm_path_only(db):
    from django.db.models import ProtectedError

    owner = _user("AD-D")
    addon = Addon.objects.create(name="Z", owner=owner)
    AddonVersion.objects.create(addon=addon, version="1.0.0", manifest={})
    with pytest.raises(ProtectedError):
        addon.delete()


def test_authority_gate_refuses_every_action_for_every_kind_of_user(db):
    admin = CustomUser.objects.create(employee_id="AD-ADM", full_name="Adm", email="adm@salowtech.com", role="admin",
                                      status="active", is_active=True, is_staff=True, is_superuser=True)  # fmt: skip
    for actor in (_user(), admin, None):
        for action in svc.ACTIONS + ("install",):
            with pytest.raises(PermissionDenied):
                svc.require_authority(actor, action)


def test_rows_can_store_validated_manifest_output_nothing_enforces_it_and_uniqueness_holds(db):
    owner = _user()
    addon = Addon.objects.create(name="Demo", owner=owner)
    assert addon.status == Addon.DRAFT
    manifest = validate_manifest({"name": "Demo", "version": "1.0.0"})
    AddonVersion.objects.create(addon=addon, version="1.0.0", manifest=manifest)
    with pytest.raises(IntegrityError), transaction.atomic():
        AddonVersion.objects.create(addon=addon, version="1.0.0", manifest=manifest)
    AddonInstall.objects.create(addon=addon, scope_target=AddonInstall.USER, target_id="p1", installed_by=owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        AddonInstall.objects.create(addon=addon, scope_target=AddonInstall.USER, target_id="p1", installed_by=owner)
    assert AddonInstall.objects.get().granted_scopes == [] and AddonVersion.objects.get().manifest["scopes"] == []
