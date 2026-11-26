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


@pytest.mark.django_db
def test_work_log_and_certificate_filters_treat_percent_and_underscore_literally():
    me = _u("CI-L1", "Lee Literal")
    ps.create_post(author=me, title="100% done", content="x")
    ps.create_post(author=me, title="Plain title", content="y")
    titles = lambda q: sorted(p.title for p in list_posts_for_user(me, search=q))
    assert titles("100%") == ["100% done"]
    assert titles("%") == ["100% done"]
    assert titles("_") == []
    assert [c.pk for c in list_all_certificates(search="%")] == []


@pytest.mark.django_db
def test_certificate_lookup_by_serial_uuid_or_hash_ignores_case():
    from apps.certificates.selectors import get_certificate_by_identifier
    from apps.certificates.services import verify_certificate

    admin = CustomUser.objects.create_superuser(employee_id="CI-ADM2", full_name="Ada Admin", password="SuperMasterAdminPassword2026!")
    rec = _u("CI-V1", "Vera Verify", role="intern")
    tpl = CertificateTemplate.objects.create(code="CI_V", title="Template", description_template="Done.")
    cert = issue_certificate(
        recipient=rec, title="Verified", description="d", issued_by=admin, template=tpl,
        recipient_role="Intern", issue_date=date(2026, 11, 16),
    )  # fmt: skip
    for ident in (cert.serial_number.lower(), cert.serial_number.upper(), str(cert.uuid).upper(),
                  cert.verification_hash.upper(), cert.verification_hash.lower()):
        found = get_certificate_by_identifier(ident)
        assert found is not None and found.pk == cert.pk, ident
        got, status = verify_certificate(ident)
        assert got is not None and got.pk == cert.pk and status != "not_found", ident
    assert get_certificate_by_identifier("no-such-serial") is None
    assert verify_certificate("no-such-serial")[1] == "not_found"


@pytest.mark.django_db
def test_certificate_lookup_never_picks_between_values_that_differ_only_by_case():
    from apps.certificates.models import Certificate
    from apps.certificates.selectors import get_certificate_by_identifier
    from apps.certificates.services import verify_certificate

    admin = CustomUser.objects.create_superuser(employee_id="CI-ADM3", full_name="Ada Admin", password="SuperMasterAdminPassword2026!")
    rec = _u("CI-V2", "Wes Collide", role="intern")
    tpl = CertificateTemplate.objects.create(code="CI_W", title="Template", description_template="Done.")
    base = dict(
        recipient=rec, title="T", description="d", issued_by=admin, template=tpl,
        recipient_name="Wes Collide", recipient_role="Intern", issue_date=date(2026, 11, 16),
    )  # fmt: skip
    # Rows written around the app (the unique index is case-sensitive here), differing only by letter case.
    a = Certificate.objects.create(serial_number="ST-CERT-2026-AB12CD", verification_hash="a" * 64, **base)
    b = Certificate.objects.create(serial_number="st-cert-2026-ab12cd", verification_hash="A" * 64, **base)
    assert get_certificate_by_identifier("ST-CERT-2026-AB12CD").pk == a.pk  # exact wins
    assert get_certificate_by_identifier("st-cert-2026-ab12cd").pk == b.pk
    assert get_certificate_by_identifier("St-Cert-2026-Ab12Cd") is None  # ambiguous: nothing picked
    assert verify_certificate("St-Cert-2026-Ab12Cd")[1] == "not_found"
    assert get_certificate_by_identifier("a" * 64).pk == a.pk
    assert get_certificate_by_identifier("A" * 64).pk == b.pk


@pytest.mark.django_db
def test_certificate_lookup_ambiguity_ends_the_lookup_for_hash_and_across_fields():
    from apps.certificates.models import Certificate
    from apps.certificates.selectors import get_certificate_by_identifier
    from apps.certificates.services import verify_certificate

    admin = CustomUser.objects.create_superuser(employee_id="CI-ADM4", full_name="Ada Admin", password="SuperMasterAdminPassword2026!")
    rec = _u("CI-V3", "Xia Cross", role="intern")
    tpl = CertificateTemplate.objects.create(code="CI_X", title="Template", description_template="Done.")
    base = dict(
        recipient=rec, title="T", description="d", issued_by=admin, template=tpl,
        recipient_name="Xia Cross", recipient_role="Intern", issue_date=date(2026, 11, 16),
    )  # fmt: skip
    # Mixed-case hash ambiguity: two hashes differ only by case, and a third certificate's serial is the mixed-case text,
    # so a fall-through from an ambiguous hash must not reach it.
    Certificate.objects.create(serial_number="S-ONE", verification_hash="b" * 64, **base)
    Certificate.objects.create(serial_number="S-TWO", verification_hash="B" * 64, **base)
    assert get_certificate_by_identifier("b" * 64).verification_hash == "b" * 64  # exact
    assert get_certificate_by_identifier(("b" * 63) + "B") is None
    assert verify_certificate(("b" * 63) + "B")[1] == "not_found"
    # Ambiguous serial does not fall through to a hash that equals the typed text.
    Certificate.objects.create(serial_number="amb-serial", verification_hash="c" * 64, **base)
    Certificate.objects.create(serial_number="AMB-SERIAL", verification_hash="d" * 64, **base)
    Certificate.objects.create(serial_number="S-THREE", verification_hash="Amb-Serial", **base)
    assert get_certificate_by_identifier("Amb-Serial") is None
    # Cross namespace: one value that is one certificate's serial and another's hash.
    Certificate.objects.create(serial_number="SHARED-VALUE-1", verification_hash="e" * 64, **base)
    Certificate.objects.create(serial_number="S-FOUR", verification_hash="SHARED-VALUE-1", **base)
    assert get_certificate_by_identifier("SHARED-VALUE-1") is None
    assert get_certificate_by_identifier("e" * 64).serial_number == "SHARED-VALUE-1"
