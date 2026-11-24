"""Admin Backups page: queue a backup, see status, run an online integrity check.

No restore action exists here by design. Restores are custodian-authorised and offline.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.auth.backup_jobs import check_archive_integrity, queue_backup, storage_dir
from apps.accounts.auth.stepup import is_sudo_active
from apps.accounts.models import BackupRun
from apps.accounts.permissions import PERM_BACKUP_MANAGE, has_permission


def _guard(request):
    if not has_permission(request.user, PERM_BACKUP_MANAGE):
        raise PermissionDenied("You do not have permission to manage backups.")
    if not is_sudo_active(request):
        return redirect(f"{reverse('accounts:sudo')}?next={reverse('accounts:admin_backups')}")
    return None


@login_required
@require_http_methods(["GET"])
def admin_backups_view(request):
    denied = _guard(request)
    if denied:
        return denied
    return render(
        request,
        "accounts/admin/backups.html",
        {
            "actor": request.user,
            "runs": BackupRun.objects.all()[:25],
            "storage_dir": str(storage_dir()),
        },
    )


@login_required
@require_POST
def admin_backup_run_view(request):
    denied = _guard(request)
    if denied:
        return denied
    run, created = queue_backup(actor=request.user)
    if created:
        log_auth_event(
            event="backup_requested",
            code="INFO",
            step="backup_queue",
            employee_id=request.user.employee_id,
            reference_id=str(run.pk),
            message="Database backup queued",
        )
        messages.success(request, "Backup queued. The backup worker will pick it up.")
    else:
        messages.info(request, f"Backup run {run.pk} is already {run.status}. No new run queued.")
    return redirect("accounts:admin_backups")


@login_required
@require_POST
def admin_backup_check_view(request, run_id: int):
    denied = _guard(request)
    if denied:
        return denied
    run = get_object_or_404(BackupRun, pk=run_id)
    ok, detail = check_archive_integrity(run)
    log_auth_event(
        event="backup_integrity_checked",
        code="INFO" if ok else "WARNING",
        step="backup_check",
        employee_id=request.user.employee_id,
        reference_id=str(run.pk),
        message=detail[:200],
    )
    (messages.success if ok else messages.error)(request, f"Run {run.pk}: {detail}")
    return redirect("accounts:admin_backups")
