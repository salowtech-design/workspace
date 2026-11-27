import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser
from apps.core.models import AuditLog
from apps.directory.models import ReportingRelationship
from tests.unit.test_directory import _emp

HOST = "app.salowtech.com"


def _c(u):
    c = Client(HTTP_HOST=HOST)
    c.force_login(u)
    return c


def _rel(sub):
    r = ReportingRelationship.objects.filter(subordinate=sub).first()
    return r.manager.employee_id if r else None


@pytest.fixture
def people(db):
    admin = _emp("MG-ADM", "Admin Person", CustomUser.Role.ADMIN)
    boss = _emp("MG-BOSS", "Boss Person", CustomUser.Role.MANAGER)
    emp = _emp("MG-EMP", "Worker Person")
    gone = _emp("MG-GONE", "Gone Person", status=CustomUser.Status.SUSPENDED)
    return admin, boss, emp, gone


def _url(u):
    return reverse("directory:assign_manager", args=[u.employee_id])


def test_admin_sets_and_changes_manager_and_the_profile_shows_it(people):
    admin, boss, emp, gone = people
    page_url = reverse("directory:profile", args=[emp.employee_id])
    first = _c(admin).get(page_url).content.decode()
    assert "No manager set" in first and '<option value="">Choose a person</option>' in first  # no one is preselected
    r = _c(admin).post(_url(emp), {"manager": ""}, follow=True)
    assert b"Choose a person from the list." in r.content and _rel(emp) is None  # pressing Set manager untouched changes nothing
    r = _c(admin).post(_url(emp), {"manager": "mg-boss"}, follow=True)
    assert b"Worker Person now reports to Boss Person." in r.content and _rel(emp) == "MG-BOSS"
    page = _c(admin).get(page_url).content.decode()
    assert "Boss Person (MG-BOSS)" in page and "Set manager" in page
    r = _c(admin).post(_url(emp), {"manager": "MG-ADM"}, follow=True)
    assert _rel(emp) == "MG-ADM" and ReportingRelationship.objects.filter(subordinate=emp).count() == 1
    assert AuditLog.objects.filter(action="directory.manager_assigned").count() == 2


def test_non_admin_is_refused_and_sees_no_form_but_sees_the_manager(people):
    admin, boss, emp, gone = people
    ReportingRelationship.objects.create(manager=boss, subordinate=emp)
    r = _c(boss).post(_url(emp), {"manager": "MG-ADM"}, follow=True)
    assert b"Only administrators can change who a person reports to." in r.content and _rel(emp) == "MG-BOSS"
    r = _c(emp).post(_url(emp), {"manager": "MG-EMP"}, follow=True)
    assert _rel(emp) == "MG-BOSS" and AuditLog.objects.filter(action="directory.manager_assigned").count() == 0
    page = _c(boss).get(reverse("directory:profile", args=[emp.employee_id])).content.decode()
    assert "Set manager" not in page and "Boss Person (MG-BOSS)" in page


def test_self_loop_inactive_unknown_and_get_are_refused(people):
    admin, boss, emp, gone = people
    ReportingRelationship.objects.create(manager=emp, subordinate=boss)
    for who, msg in (("MG-EMP", b"Self-management is prohibited"), ("MG-BOSS", b"Reporting cycle detected"), ("MG-GONE", b"active or onboarding"), ("NOPE", b"Choose a person")):
        r = _c(admin).post(_url(emp), {"manager": who}, follow=True)
        assert msg in r.content, who
    assert _rel(emp) is None and _rel(boss) == "MG-EMP"
    assert _c(admin).get(_url(emp)).status_code == 405
    assert _c(admin).post(reverse("directory:assign_manager", args=["NOBODY"]), {"manager": "MG-ADM"}).status_code == 404


def test_a_suspended_person_profile_does_not_reveal_their_manager(people):
    admin, boss, emp, gone = people
    ReportingRelationship.objects.create(manager=boss, subordinate=gone)
    page = _c(admin).get(reverse("directory:profile", args=[gone.employee_id])).content.decode()
    assert "Boss Person (MG-BOSS)</a>" not in page and "No manager set" in page  # (the name still appears as a choice in the admin's list) the org chart does not list a suspended person either
    from apps.directory.selectors import org_tree

    def names(nodes):
        return [n["user"].employee_id for n in nodes] + [x for n in nodes for x in names(n["reports"])]

    assert "MG-GONE" not in names(org_tree(admin)[0])


def test_a_suspended_manager_is_not_shown_as_the_manager(people):
    admin, boss, emp, gone = people
    ReportingRelationship.objects.create(manager=gone, subordinate=emp)
    assert "No manager set" in _c(admin).get(reverse("directory:profile", args=[emp.employee_id])).content.decode()
