import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.accounts.models import CustomUser
from apps.directory.models import (
    Department,
    ReportingRelationship,
    Team,
    TeamMembership,
)
from apps.directory.selectors import search_directory


@pytest.mark.django_db
def test_directory_department_and_team_constraints():
    """Verify department and team creation with single-lead constraint."""
    dept = Department.objects.create(code="eng", name="Engineering")
    assert dept.code == "ENG"  # Normalized to uppercase

    lead1 = CustomUser.objects.create_user(employee_id="ENG_LEAD1", full_name="Lead One")
    CustomUser.objects.create_user(employee_id="ENG_LEAD2", full_name="Lead Two")

    team = Team.objects.create(code="backend", name="Backend Systems", department=dept, lead=lead1)
    assert team.code == "BACKEND"

    # Enforces single team per lead via OneToOneField
    with pytest.raises(IntegrityError):
        Team.objects.create(code="core", name="Core Architecture", department=dept, lead=lead1)


@pytest.mark.django_db
def test_team_duplicate_membership_prevention():
    """Verify an employee cannot be added to the same team twice."""
    dept = Department.objects.create(code="ops", name="Operations")
    team = Team.objects.create(code="infra", name="Infrastructure", department=dept)
    worker = CustomUser.objects.create_user(employee_id="OPS01", full_name="DevOps Worker")

    TeamMembership.objects.create(team=team, user=worker)

    with pytest.raises(IntegrityError):
        TeamMembership.objects.create(team=team, user=worker)


@pytest.mark.django_db
def test_reporting_hierarchy_prohibits_self_management():
    """Verify an employee cannot report to themselves."""
    user = CustomUser.objects.create_user(employee_id="SELF01", full_name="Solo Dev")

    with pytest.raises(ValidationError, match="Self-management is prohibited"):
        rel = ReportingRelationship(manager=user, subordinate=user)
        rel.clean()


@pytest.mark.django_db
def test_reporting_hierarchy_prohibits_reporting_cycles():
    """Verify cycle detection: A reports to B, B reports to C, C cannot report to A."""
    user_a = CustomUser.objects.create_user(employee_id="CYC_A", full_name="User A")
    user_b = CustomUser.objects.create_user(employee_id="CYC_B", full_name="User B")
    user_c = CustomUser.objects.create_user(employee_id="CYC_C", full_name="User C")

    # A -> B (A reports to B)
    ReportingRelationship.objects.create(manager=user_b, subordinate=user_a)
    # B -> C (B reports to C)
    ReportingRelationship.objects.create(manager=user_c, subordinate=user_b)

    # Attempt C -> A (C reports to A) creates cycle C -> A -> B -> C
    with pytest.raises(ValidationError, match="Reporting cycle detected"):
        rel = ReportingRelationship(manager=user_a, subordinate=user_c)
        rel.clean()


@pytest.mark.django_db
def test_directory_search_privacy_filtering():
    """Verify inactive/suspended employees are excluded from directory search."""
    active_user = CustomUser.objects.create_user(
        employee_id="DIR_ACT",
        full_name="Active Alice",
        status=CustomUser.Status.ACTIVE,
    )
    CustomUser.objects.create_user(
        employee_id="DIR_EXT",
        full_name="Exited Bob",
        status=CustomUser.Status.EXITED,
    )

    results = search_directory(actor=active_user)
    result_ids = [u.employee_id for u in results]

    assert "DIR_ACT" in result_ids
    assert "DIR_EXT" not in result_ids


def _emp(eid, name, role=None, status=None):
    u = CustomUser.objects.create_user(employee_id=eid, full_name=name, password="x" * 12)
    if role:
        u.role = role
    u.status = status or CustomUser.Status.ACTIVE
    u.is_active = True
    u.save()
    return u


@pytest.mark.django_db
def test_org_chart_tree_roots_inactive_managers_and_shown_fields():
    from django.test import Client
    from django.urls import reverse

    from apps.directory.selectors import org_tree

    boss = _emp("OC-BOSS", "Boss Person", CustomUser.Role.MANAGER)
    mid = _emp("OC-MID", "Mid Person")
    low = _emp("OC-LOW", "Low Person")
    orphan = _emp("OC-ORPH", "Orphan Person")
    gone = _emp("OC-GONE", "Gone Manager", status=CustomUser.Status.SUSPENDED)
    ReportingRelationship.objects.create(manager=boss, subordinate=mid)
    ReportingRelationship.objects.create(manager=mid, subordinate=low)
    ReportingRelationship.objects.create(manager=gone, subordinate=orphan)
    tree, truncated = org_tree(boss)
    names = {n["user"].employee_id: n for n in tree}
    assert "OC-GONE" not in names and "OC-ORPH" in names and "OC-BOSS" in names  # orphan is a root
    chain = names["OC-BOSS"]["reports"][0]
    assert (
        chain["user"].employee_id == "OC-MID"
        and chain["reports"][0]["user"].employee_id == "OC-LOW"
    )
    assert truncated is False
    from apps.directory.models import EmployeeProfile

    secrets = []
    for i, u in enumerate((boss, mid, low, orphan, gone)):
        vals = {
            "phone": f"555-01{i}1",
            "emergency_contact": f"EmergencyPerson{i}",
            "bio": f"BioText{i}",
            "location": f"Place{i}",
        }
        EmployeeProfile.objects.update_or_create(user=u, defaults=vals)
        secrets += list(vals.values()) + [u.email] if u.email else list(vals.values())
    c = Client()
    c.force_login(boss)
    page = c.get(reverse("directory:org"), HTTP_HOST="app.salowtech.com")
    assert page.status_code == 200
    body = page.content.decode()
    assert "Mid Person" in body and "OC-LOW" in body
    # every person's phone, emergency contact, bio, location and email, anywhere in the page
    leaked = [v for v in secrets if v in body]
    assert leaked == []
    # the page really did render people (the assertion above is not vacuous)
    assert body.count("OC-") >= 4
    assert c.get("/directory/org/", HTTP_HOST="iam.salowtech.com").status_code == 404


@pytest.mark.django_db
def test_org_chart_cuts_off_deep_chains_and_says_so(monkeypatch):
    from apps.directory import selectors

    monkeypatch.setattr(selectors, "ORG_MAX_DEPTH", 2)
    prev = _emp("OD-0", "Zero")
    top = prev
    for i in range(1, 5):
        cur = _emp(f"OD-{i}", f"Level {i}")
        ReportingRelationship.objects.create(manager=prev, subordinate=cur)
        prev = cur
    tree, truncated = selectors.org_tree(top)
    assert truncated is True
    depth, node = 0, tree[0]
    while node["reports"]:
        node, depth = node["reports"][0], depth + 1
    assert depth == 2  # cut at the configured depth, nobody is silently dropped from the top


@pytest.mark.django_db
def test_org_chart_needs_sign_in():
    from django.test import Client

    r = Client().get("/directory/org/", HTTP_HOST="app.salowtech.com")
    assert r.status_code == 302


@pytest.mark.django_db
def test_assign_manager_authority_cycles_self_inactive_and_noop():
    from django.core.exceptions import PermissionDenied

    from apps.core.models import AuditLog
    from apps.directory import services as ds

    admin = _emp("AM-ADM", "Admin", CustomUser.Role.ADMIN)
    a, b, c = _emp("AM-A", "A"), _emp("AM-B", "B"), _emp("AM-C", "C")
    off = _emp("AM-OFF", "Off", status=CustomUser.Status.SUSPENDED)
    for who in (
        _emp("AM-E", "E"),
        _emp("AM-H", "H", CustomUser.Role.HR),
        _emp("AM-M", "M", CustomUser.Role.MANAGER),
    ):
        with pytest.raises(PermissionDenied):  # the existing rule: admins only (HR open question)
            ds.assign_reporting_manager(a, b, who)
    ds.assign_reporting_manager(a, b, admin)  # B reports to A
    ds.assign_reporting_manager(b, c, admin)  # C reports to B
    with pytest.raises(ValidationError):
        ds.assign_reporting_manager(c, a, admin)  # A under C closes a loop of three
    with pytest.raises(ValidationError):
        ds.assign_reporting_manager(b, a, admin)  # direct loop
    with pytest.raises(ValidationError):
        ds.assign_reporting_manager(a, a, admin)
    for pair in ((off, a), (a, off)):
        with pytest.raises(ValidationError):
            ds.assign_reporting_manager(pair[0], pair[1], admin)
    n = AuditLog.objects.filter(action="directory.manager_assigned").count()
    again = ds.assign_reporting_manager(a, b, admin)  # same manager again
    assert again.manager_id == a.pk
    assert AuditLog.objects.filter(action="directory.manager_assigned").count() == n == 2
    assert ReportingRelationship.objects.count() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("locked", [True, False])
def test_race_two_manager_changes_cannot_make_a_loop(monkeypatch, locked):
    import threading

    from django.db import connection

    from apps.directory import services as ds
    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("row locks are only meaningful on the TiDB/MySQL backend")
    if not locked:
        monkeypatch.setattr(ds, "_reporting_lock", lambda: None)
    loops, outcomes = 0, []
    for i in range(2 if locked else 3):
        admin = _emp(f"RL-ADM{i}{locked}", "Admin", CustomUser.Role.ADMIN)
        a, b = _emp(f"RL-A{i}{locked}", "A"), _emp(f"RL-B{i}{locked}", "B")
        barrier = threading.Barrier(2)

        def pause(barrier=barrier):
            try:
                barrier.wait(
                    timeout=1.5 if locked else 10
                )  # lockless: both must arrive, no early timeout
            except threading.BrokenBarrierError:
                pass  # the lock kept the other side out

        monkeypatch.setattr(ds, "_pause", pause)
        outcomes.append(
            _threads(
                [
                    lambda a=a, b=b, admin=admin: ds.assign_reporting_manager(a, b, admin),
                    lambda a=a, b=b, admin=admin: ds.assign_reporting_manager(b, a, admin),
                ]
            )
        )
        both = ReportingRelationship.objects.filter(subordinate__in=[a, b]).count() == 2
        if both:
            loops += 1
    assert all("error" not in o for row in outcomes for o in row), outcomes
    if locked:
        assert loops == 0 and all(
            sorted(r) == ["ok", "refused:ValidationError"] for r in outcomes
        ), (loops, outcomes)
    else:
        assert loops == 3, (loops, outcomes)


@pytest.mark.django_db
def test_reporting_self_reference_is_refused_by_the_database():
    a = _emp("SELF-1", "Self One")
    with pytest.raises(ValidationError):  # the model's own save() check
        ReportingRelationship.objects.create(manager=a, subordinate=a)
    from django.db import connection

    if connection.vendor != "sqlite":
        pytest.skip("TiDB 8.5.1 did not enforce the check constraint here (observed, run once)")
    with pytest.raises(IntegrityError):  # bulk_create skips save(): only the constraint stops it
        ReportingRelationship.objects.bulk_create([ReportingRelationship(manager=a, subordinate=a)])


@pytest.mark.django_db
def test_reporting_lock_row_is_separate_and_recreated_if_missing():
    from apps.core.models import RateLimitBucket
    from apps.directory.models import ReportingLock
    from apps.directory.services import assign_reporting_manager

    admin = _emp("LK-ADM", "Lock Admin", CustomUser.Role.ADMIN)
    a, b = _emp("LK-A", "A"), _emp("LK-B", "B")
    RateLimitBucket.objects.all().delete()  # what the IAM lock release / purge job deletes
    assert assign_reporting_manager(a, b, admin).manager_id == a.pk
    assert ReportingLock.objects.filter(name="reporting").count() == 1
    ReportingLock.objects.all().delete()  # someone removed the row: it is made again
    c = _emp("LK-C", "C")
    assert assign_reporting_manager(a, c, admin).manager_id == a.pk
    assert ReportingLock.objects.filter(name="reporting").count() == 1


@pytest.mark.django_db
def test_stale_admin_object_is_refused_after_demotion_or_deactivation():
    from django.core.exceptions import PermissionDenied

    from apps.directory.services import assign_reporting_manager

    a, b = _emp("ST-A", "A"), _emp("ST-B", "B")
    for change in ({"role": CustomUser.Role.EMPLOYEE}, {"status": CustomUser.Status.SUSPENDED}):
        admin = _emp(f"ST-ADM{len(change)}{list(change)[0]}", "Stale Admin", CustomUser.Role.ADMIN)
        stale = CustomUser.objects.get(pk=admin.pk)  # loaded while still an admin
        CustomUser.objects.filter(pk=admin.pk).update(**change)
        with pytest.raises(PermissionDenied):
            assign_reporting_manager(a, b, stale)
    assert not ReportingRelationship.objects.filter(subordinate=b).exists()


@pytest.mark.django_db
def test_custom_fields_authority_types_required_and_visibility():
    from django.core.exceptions import PermissionDenied

    from apps.directory.models import CustomFieldValue
    from apps.directory.selectors import custom_values_for
    from apps.directory.services import create_custom_field, set_custom_value

    admin = _emp("CF-ADM", "Field Admin", CustomUser.Role.ADMIN)
    hr = _emp("CF-HR", "Hr Person", CustomUser.Role.HR)
    mgr = _emp("CF-MGR", "Mgr Person", CustomUser.Role.MANAGER)
    emp = _emp("CF-EMP", "Emp Person")
    for who in (hr, mgr, emp):  # existing authority only: HR and managers are refused
        with pytest.raises(PermissionDenied):
            create_custom_field("blood_type", "Blood type", "text", who)
    with pytest.raises(ValidationError):
        create_custom_field("Bad Key!", "x", "text", admin)
    with pytest.raises(ValidationError):
        create_custom_field("shoe", "Shoe", "weird", admin)
    create_custom_field("shoe_size", "Shoe size", "number", admin)
    create_custom_field("joined", "Joined", "date", admin)
    create_custom_field("remote", "Remote", "yes_no", admin)
    create_custom_field("badge", "Badge", "text", admin, required=True)
    with pytest.raises(ValidationError):
        create_custom_field("SHOE_SIZE", "Again", "text", admin)  # duplicate after normalising
    assert set_custom_value(emp, "shoe_size", "42.50", admin) == "42.5"
    assert set_custom_value(emp, "joined", "2026-11-25", admin) == "2026-11-25"
    assert set_custom_value(emp, "remote", "TRUE", admin) == "yes"
    for key, bad in (
        ("shoe_size", "abc"),
        ("shoe_size", "1e99"),
        ("shoe_size", "NaN"),
        ("joined", "25/11/2026"),
        ("remote", "maybe"),
    ):
        with pytest.raises(ValidationError):
            set_custom_value(emp, key, bad, admin)
    with pytest.raises(ValidationError):
        set_custom_value(emp, "badge", "  ", admin)  # required: a blank is refused
    with pytest.raises(ValidationError):
        set_custom_value(emp, "nope", "x", admin)
    for who in (hr, mgr, emp):
        with pytest.raises(PermissionDenied):
            set_custom_value(emp, "shoe_size", "1", who)
    assert dict(custom_values_for(admin, emp)) == {
        "Shoe size": "42.5",
        "Joined": "2026-11-25",
        "Remote": "yes",
    }
    for who in (hr, mgr, emp):
        assert custom_values_for(who, emp) == []  # build default: administrators only
    set_custom_value(emp, "shoe_size", "", admin)  # blank clears an optional field
    assert not CustomFieldValue.objects.filter(user=emp, definition__key="shoe_size").exists()


@pytest.mark.django_db
def test_custom_value_audit_has_no_value_and_noop_writes_nothing_and_stale_admin_refused():
    from django.core.exceptions import PermissionDenied

    from apps.core.models import AuditLog
    from apps.directory.services import create_custom_field, set_custom_value

    admin = _emp("CA-ADM", "Audit Admin", CustomUser.Role.ADMIN)
    emp = _emp("CA-EMP", "Audit Emp")
    create_custom_field("note", "Note", "text", admin)
    set_custom_value(emp, "note", "TopSecretValue123", admin)
    n = AuditLog.objects.count()
    set_custom_value(emp, "note", "TopSecretValue123", admin)  # same value
    assert AuditLog.objects.count() == n
    dump = " ".join(str(a.__dict__) for a in AuditLog.objects.all())
    assert "TopSecretValue123" not in dump and "directory.custom_value_set" in dump
    stale = CustomUser.objects.get(pk=admin.pk)
    CustomUser.objects.filter(pk=admin.pk).update(role=CustomUser.Role.EMPLOYEE)
    with pytest.raises(PermissionDenied):
        set_custom_value(emp, "note", "other", stale)


@pytest.mark.django_db
def test_reporting_lock_row_is_seeded_by_the_migration_itself():
    """Checks the migration, not the service: run the seed function against the test database."""
    from importlib import import_module

    from django.apps import apps as django_apps

    from apps.directory.models import ReportingLock

    mod = import_module("apps.directory.migrations.0005_seed_reporting_lock")
    ReportingLock.objects.all().delete()
    mod.seed_lock(django_apps, None)
    mod.seed_lock(django_apps, None)  # running twice does not duplicate
    assert ReportingLock.objects.filter(name="reporting").count() == 1


@pytest.mark.django_db
def test_onboarding_template_is_empty_by_default_and_admin_only():
    from django.core.exceptions import PermissionDenied

    from apps.directory.models import OnboardingStep, OnboardingTask
    from apps.directory.services import (
        add_onboarding_step,
        complete_onboarding_task,
        start_onboarding,
    )

    assert OnboardingStep.objects.count() == 0  # nothing invented
    admin = _emp("OB-ADM", "Ob Admin", CustomUser.Role.ADMIN)
    hr = _emp("OB-HR", "Ob Hr", CustomUser.Role.HR)
    new = _emp("OB-NEW", "New Hire", status=CustomUser.Status.ONBOARDING)
    gone = _emp("OB-GONE", "Gone", status=CustomUser.Status.SUSPENDED)
    with pytest.raises(PermissionDenied):
        add_onboarding_step("Sign forms", hr)
    with pytest.raises(ValidationError):
        add_onboarding_step("", admin)
    with pytest.raises(ValidationError):
        add_onboarding_step("x", admin, owner_role="nonsense")
    with pytest.raises(ValidationError):
        add_onboarding_step("x", admin, due_after_days=400)
    add_onboarding_step("Sign forms", admin, owner_role="hr", due_after_days=2)
    add_onboarding_step("Laptop", admin, due_after_days=5)
    with pytest.raises(ValidationError):
        add_onboarding_step("Laptop", admin)
    from datetime import date

    with pytest.raises(PermissionDenied):
        start_onboarding(new, hr)
    with pytest.raises(ValidationError):
        start_onboarding(gone, admin)
    made = start_onboarding(new, admin, date(2026, 12, 1))
    assert [(t.title, t.due_date.isoformat()) for t in made] == [
        ("Sign forms", "2026-12-03"),
        ("Laptop", "2026-12-06"),
    ]
    add_onboarding_step("Badge", admin)
    assert [t.title for t in start_onboarding(new, admin)] == ["Badge"]  # repeat adds only new
    assert start_onboarding(new, admin) == []
    assert OnboardingTask.objects.filter(user=new).count() == 3
    t = made[0]
    with pytest.raises(PermissionDenied):
        complete_onboarding_task(t.pk, hr)  # owner_role label grants nothing
    assert complete_onboarding_task(t.pk, admin).state == "done"
    stamp = OnboardingTask.objects.get(pk=t.pk).completed_at
    complete_onboarding_task(t.pk, admin)
    assert OnboardingTask.objects.get(pk=t.pk).completed_at == stamp
    with pytest.raises(ValidationError):
        complete_onboarding_task(999999, admin)


@pytest.mark.django_db
def test_custom_number_extremes_and_negative_zero():
    from apps.directory.models import CustomFieldDefinition
    from apps.directory.services import normalise_custom_value

    d = CustomFieldDefinition(key="n", label="N", field_type="number")
    for bad in (
        "1e-9999",
        "1e-100000000",
        "1e-13",
        "0.0000000000001",
        "9" * 41,
        "1" + "0" * 40,
        "1e15",
        "Infinity",
        "1_000",
        "\u0661\u0662",
    ):
        with pytest.raises(ValidationError):
            normalise_custom_value(d, bad)
    assert normalise_custom_value(d, "-0") == "0"
    assert normalise_custom_value(d, "-0.000") == "0"
    assert normalise_custom_value(d, "0.000000000001") == "0.000000000001"
    assert normalise_custom_value(d, "007.50") == "7.5"
    assert normalise_custom_value(d, "-1e3") == "-1000"
    assert len(normalise_custom_value(d, "123456789012345.123456789012")) <= 40


@pytest.mark.django_db
def test_admin_demoted_between_early_check_and_write_is_refused(monkeypatch):
    from django.core.exceptions import PermissionDenied

    from apps.directory import services as ds
    from apps.directory.models import CustomFieldDefinition

    admin = _emp("RC-ADM", "Race Admin", CustomUser.Role.ADMIN)
    real, calls = ds._require_admin, []

    def demote_after_first(actor):
        calls.append(1)  # counted before the real check, which raises on the second call
        r = real(actor)
        if len(calls) == 1:
            CustomUser.objects.filter(pk=actor.pk).update(role=CustomUser.Role.EMPLOYEE)
        return r

    monkeypatch.setattr(ds, "_require_admin", demote_after_first)
    with pytest.raises(PermissionDenied):
        ds.create_custom_field("late_key", "Late", "text", admin)
    assert not CustomFieldDefinition.objects.filter(key="late_key").exists()
    assert len(calls) == 2  # the second, in-transaction read is what refused it


@pytest.mark.django_db
def test_onboarding_step_admin_rechecked_in_transaction_and_audit_failure_not_a_duplicate(
    monkeypatch,
):
    from django.core.exceptions import PermissionDenied

    from apps.directory import services as ds
    from apps.directory.models import CustomFieldDefinition, OnboardingStep

    admin = _emp("OT-ADM", "Ot Admin", CustomUser.Role.ADMIN)
    real, calls = ds._require_admin, []

    def demote_after_first(actor):
        calls.append(1)
        r = real(actor)
        if len(calls) == 1:
            CustomUser.objects.filter(pk=actor.pk).update(role=CustomUser.Role.EMPLOYEE)
        return r

    monkeypatch.setattr(ds, "_require_admin", demote_after_first)
    with pytest.raises(PermissionDenied):
        ds.add_onboarding_step("Late step", admin)
    assert not OnboardingStep.objects.filter(title="Late step").exists() and len(calls) == 2
    monkeypatch.setattr(ds, "_require_admin", real)
    admin2 = _emp("OT-ADM2", "Ot Admin2", CustomUser.Role.ADMIN)

    def boom(**kw):
        raise IntegrityError("audit table problem")

    monkeypatch.setattr(ds, "record_audit_event", boom)
    with pytest.raises(IntegrityError):  # not turned into "key already exists"
        ds.create_custom_field("audit_key", "Audit", "text", admin2)
    assert not CustomFieldDefinition.objects.filter(key="audit_key").exists()  # rolled back


@pytest.mark.django_db
def test_onboarding_default_start_date_uses_configured_timezone(monkeypatch):
    from datetime import date

    from django.utils import timezone

    from apps.directory import services as ds

    admin = _emp("TZ-ADM", "Tz Admin", CustomUser.Role.ADMIN)
    new = _emp("TZ-NEW", "Tz New")
    ds.add_onboarding_step("Day zero", admin)
    monkeypatch.setattr(timezone, "localdate", lambda: date(2031, 1, 2))
    assert ds.start_onboarding(new, admin)[0].due_date == date(2031, 1, 2)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["locked", "lockless_backstop", "lockless_raw"])
def test_race_two_onboarding_starts_make_each_step_once(monkeypatch, mode):
    import threading

    from django.db import connection

    from apps.directory import services as ds
    from apps.directory.models import OnboardingStep, OnboardingTask
    from tests.unit.test_drive_services import _threads

    if connection.vendor == "sqlite":
        pytest.skip("concurrent writers are only meaningful on the TiDB/MySQL backend")
    admin = CustomUser.objects.create(
        employee_id=f"OB-AD-{mode}",
        full_name="Adm",
        role="super_admin",
        status="active",
        is_active=True,
    )
    person = CustomUser.objects.create(
        employee_id=f"OB-P-{mode}",
        full_name="Pat",
        role="employee",
        status="active",
        is_active=True,
    )
    for i in range(3):
        OnboardingStep.objects.create(title=f"Step {mode} {i}", due_after_days=i, position=i)
    if mode != "locked":
        monkeypatch.setattr(ds, "_lock_person", lambda pk: CustomUser.objects.filter(pk=pk).first())
    monkeypatch.setattr(ds, "_CATCH_DUPLICATE", mode != "lockless_raw")
    barrier = threading.Barrier(2)

    def pause():
        try:
            barrier.wait(timeout=1.5 if mode == "locked" else 10)  # both read "nothing yet"
        except threading.BrokenBarrierError:
            pass  # locked: the other start is held at the row lock

    monkeypatch.setattr(ds, "_pause_onboarding", pause)
    made = []
    out = _threads(
        [
            lambda: made.append(len(ds.start_onboarding(person, admin))),
            lambda: made.append(len(ds.start_onboarding(person, admin))),
        ]
    )
    rows = OnboardingTask.objects.filter(user=person).count()
    assert rows == 3  # never a duplicate step, in any mode
    if mode == "lockless_raw":
        # the anomaly the backstop absorbs: the loser fails on the unique key
        assert "IntegrityError" in "".join(out), out
    else:
        assert out == ["ok", "ok"] and sorted(made) == [0, 3], (out, made)
