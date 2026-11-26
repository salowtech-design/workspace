"""Keyword filters in Work Logs, People and Certificates must ignore case on TiDB's binary collation."""

from datetime import date

import pytest

from apps.accounts.models import CustomUser
from apps.certificates.models import CertificateTemplate
from apps.certificates.selectors import list_all_certificates
from apps.certificates.services import issue_certificate
from apps.directory.selectors import search_directory
from apps.posts import services as ps
from apps.posts.selectors import list_posts_for_user


def _u(eid, name, role="employee"):
    return CustomUser.objects.create(
        employee_id=eid, full_name=name, email=f"{eid.lower()}@salowtech.com",
        role=role, status="active", is_active=True,
    )  # fmt: skip


@pytest.mark.django_db
def test_work_log_keyword_filter_ignores_case_in_title_content_and_author():
    me = _u("CI-P1", "Casey Poster")
    ps.create_post(author=me, title="Alpha Release Notes", content="Shipped The Gizmo")
    ps.create_post(author=me, title="Beta planning", content="nothing")
    titles = lambda q: sorted(p.title for p in list_posts_for_user(me, search=q))
    assert titles("alpha release") == ["Alpha Release Notes"]
    assert titles("ALPHA") == ["Alpha Release Notes"]
    assert titles("the gizmo") == ["Alpha Release Notes"]  # content
    assert titles("casey poster") == ["Alpha Release Notes", "Beta planning"]  # author name
    assert titles("ci-p1") == ["Alpha Release Notes", "Beta planning"]  # author id
    assert titles("zzz") == []


@pytest.mark.django_db
def test_people_keyword_search_ignores_case_in_name_id_and_email():
    me = _u("CI-D1", "Dana Directory")
    _u("CI-D2", "Evan Example")
    ids = lambda q: sorted(u.employee_id for u in search_directory(me, query=q))
    assert ids("dana dir") == ["CI-D1"]
    assert ids("EVAN") == ["CI-D2"]
    assert ids("ci-d2") == ["CI-D2"]
    assert ids("CI-D1@SALOWTECH") == ["CI-D1"]


@pytest.mark.django_db
def test_certificate_keyword_filter_ignores_case():
    admin = CustomUser.objects.create_superuser(employee_id="CI-ADM", full_name="Ada Admin", password="SuperMasterAdminPassword2026!")
    rec = _u("CI-C1", "Grace Hopper", role="intern")
    tpl = CertificateTemplate.objects.create(code="CI_T", title="Template", description_template="Done.")
    cert = issue_certificate(
        recipient=rec, title="Internship Excellence Award", description="For work.",
        issued_by=admin, template=tpl, recipient_role="Intern", issue_date=date(2026, 11, 16),
    )  # fmt: skip
    found = lambda q: [c.pk for c in list_all_certificates(search=q)]
    assert found("excellence award") == [cert.pk]
    assert found("GRACE HOPPER") == [cert.pk]
    assert found("ci-c1") == [cert.pk]
    assert found(cert.serial_number.lower()) == [cert.pk]
    assert found("nomatch") == []
