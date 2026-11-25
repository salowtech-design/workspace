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
                barrier.wait(timeout=1.5)  # both sides have passed the loop check
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
