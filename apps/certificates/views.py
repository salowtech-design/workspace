from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.auth.stepup import is_sudo_active
from apps.accounts.permissions import (
    PERM_CERTIFICATE_ISSUE,
    PERM_CERTIFICATE_REVOKE,
    has_permission,
)
from apps.certificates.forms import CertificateIssueForm, CertificateRevokeForm
from apps.certificates.models import Certificate
from apps.certificates.selectors import (
    list_all_certificates,
    list_certificates_for_recipient,
)
from apps.certificates.services import (
    generate_certificate_svg,
    issue_certificate,
    revoke_certificate,
    verify_certificate,
)


@login_required
def certificate_list_view(request):
    """
    Credential dashboard.
    Employees view their own credentials; Admins view and manage all issued credentials.
    """
    can_issue = has_permission(request.user, PERM_CERTIFICATE_ISSUE)

    search = request.GET.get("q", "")
    status = request.GET.get("status", "")

    if can_issue:
        certificates = list_all_certificates(search=search, status=status)
    else:
        certificates = list_certificates_for_recipient(request.user)

    context = {
        "certificates": certificates,
        "can_issue": can_issue,
        "search_query": search,
        "selected_status": status,
    }
    return render(request, "certificates/list.html", context)


@login_required
def certificate_issue_view(request):
    """Admin workflow to issue a new cryptographically secured certificate."""
    if not has_permission(request.user, PERM_CERTIFICATE_ISSUE):
        raise PermissionDenied("You do not have permission to issue certificates.")

    old_uuid = request.POST.get("supersedes") or request.GET.get("reissue") or ""
    old = None
    if old_uuid:
        try:
            old = Certificate.objects.filter(uuid=old_uuid, is_revoked=True).first()
        except (ValidationError, ValueError):
            old = None

    if old_uuid and old is None:
        messages.error(
            request, "That certificate cannot be reissued: it was not found or is not revoked."
        )
        return redirect("certificates:list")

    if request.method == "POST":
        form = CertificateIssueForm(request.POST)
        if form.is_valid():
            try:
                cert = issue_certificate(
                    supersedes=old,
                    recipient=form.cleaned_data["recipient"],
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    issued_by=request.user,
                    template=form.cleaned_data.get("template"),
                    recipient_role=form.cleaned_data.get("recipient_role", ""),
                    issue_date=form.cleaned_data.get("issue_date"),
                    expiry_date=form.cleaned_data.get("expiry_date"),
                )
                messages.success(
                    request,
                    f"Certificate {cert.serial_number} issued to {cert.recipient_name} successfully.",
                )
                return redirect("certificates:view", cert_uuid=cert.uuid)
            except ValidationError as err:
                messages.error(request, str(err))
    else:
        initial = {}
        if old:
            initial = {
                "recipient": old.recipient_id,
                "template": old.template_id,
                "title": old.title,
                "recipient_role": old.recipient_role,
                "description": old.description,
                "expiry_date": old.expiry_date,
            }
        form = CertificateIssueForm(initial=initial)

    return render(request, "certificates/issue.html", {"form": form, "reissue_of": old})


@login_required
@require_POST
def certificate_revoke_view(request, cert_uuid):
    """Revoke an issued certificate with mandatory reason tracking."""
    if not has_permission(request.user, PERM_CERTIFICATE_REVOKE):
        raise PermissionDenied("You do not have permission to revoke certificates.")

    cert = get_object_or_404(Certificate, uuid=cert_uuid)
    if not is_sudo_active(request):
        # Revocation is irreversible: it needs a fresh password re-entry (sudo).
        messages.error(request, "Re-enter your password to revoke a certificate.")
        return redirect(f"{reverse('accounts:sudo')}?next={reverse('certificates:list')}")
    form = CertificateRevokeForm(request.POST)
    if form.is_valid():
        try:
            revoke_certificate(
                certificate=cert,
                revoked_by=request.user,
                reason=form.cleaned_data["reason"],
            )
            messages.success(request, f"Certificate {cert.serial_number} has been revoked.")
        except (ValidationError, PermissionDenied) as err:
            messages.error(request, str(err))
    else:
        messages.error(request, "A revocation reason is required.")

    return redirect("certificates:list")


def _verify_qr_svg(request, cert) -> str:
    """Inline SVG QR code pointing at the public verification page for this certificate."""
    import qrcode
    import qrcode.image.svg

    url = request.build_absolute_uri(reverse("public_verify_id", args=[cert.serial_number]))
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    return img.to_string(encoding="unicode")


@login_required
def certificate_view(request, cert_uuid):
    """Display printable A4 certificate presentation view."""
    cert = get_object_or_404(Certificate, uuid=cert_uuid)

    # Permission check: recipient, admin, or issuer
    is_recipient = cert.recipient_id == request.user.id
    can_manage = has_permission(request.user, PERM_CERTIFICATE_ISSUE)
    if not is_recipient and not can_manage:
        raise PermissionDenied("You do not have permission to view this certificate.")

    revoke_form = CertificateRevokeForm() if can_manage and not cert.is_revoked else None

    context = {
        "cert": cert,
        "revoke_form": revoke_form,
        "can_manage": can_manage,
        "qr_svg": _verify_qr_svg(request, cert),
    }
    return render(request, "certificates/view.html", context)


@login_required
def certificate_svg_view(request, cert_uuid):
    """Direct SVG delivery for print/export. Scoped to recipient and administrators."""
    cert = get_object_or_404(Certificate, uuid=cert_uuid)

    is_recipient = cert.recipient_id == request.user.id
    can_manage = has_permission(request.user, PERM_CERTIFICATE_ISSUE)
    if not is_recipient and not can_manage:
        raise PermissionDenied("You do not have permission to access this certificate asset.")

    svg_content = generate_certificate_svg(cert)
    response = HttpResponse(svg_content, content_type="image/svg+xml")
    response["Content-Disposition"] = f'inline; filename="{cert.serial_number}.svg"'
    return response


def public_verify_view(request, identifier=None):
    """
    Public verification endpoint (/verify/ and /verify/<identifier>/).
    No authentication required. Strict noindex header applied.
    """
    target_id = identifier or request.GET.get("id") or request.POST.get("id", "")
    target_id = target_id.strip() if target_id else ""

    cert = None
    status = None

    if target_id:
        ip = request.META.get("REMOTE_ADDR")
        ua = request.META.get("HTTP_USER_AGENT", "")
        cert, status = verify_certificate(target_id, ip_address=ip, user_agent=ua)

    context = {
        "searched_id": target_id,
        "cert": cert,
        "status": status,
    }
    response = render(request, "certificates/verify.html", context)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
