import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.functions import Upper
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import CustomUser
from apps.core.foundation.permissions import can
from apps.drive import services as svc
from apps.drive.models import DriveFile, Folder


def _item(kind, item_id):
    model = {"folder": Folder, "file": DriveFile}.get(kind)
    if model is None:
        raise Http404
    return get_object_or_404(model, public_id=item_id)


def _rtype(item):
    return "drive.folder" if isinstance(item, Folder) else "drive.file"


def _visible_or_404(user, item, action="view"):
    """Someone with no access learns nothing: unknown and forbidden look the same."""
    if not can(user, action, _rtype(item), item):
        raise Http404
    return item


def _crumbs(user, folder):
    """Trail up to the highest ancestor the viewer can open; never names an ancestor they cannot."""
    out = []
    while folder is not None and len(out) < 25:
        if not can(user, "view", "drive.folder", folder):
            break
        out.append(folder)
        folder = folder.parent
    return list(reversed(out))


def _back(item):
    parent = item.parent if isinstance(item, Folder) else item.folder
    return (
        redirect("drive:folder", folder_id=parent.public_id) if parent else redirect("drive:home")
    )


def _run(request, fn, *args, ok=""):
    try:
        fn(*args)
        if ok:
            messages.success(request, ok)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except PermissionDenied:
        messages.error(request, "You do not have permission to do that.")


@login_required
def drive_home(request):
    folders, files = svc.list_folder(request.user, None)
    return render(
        request,
        "drive/browse.html",
        {"folder": None, "folders": folders, "files": files, "crumbs": [], "can_edit": True},
    )


@login_required
def folder_view(request, folder_id):
    folder = _visible_or_404(request.user, get_object_or_404(Folder, public_id=folder_id))
    folders, files = svc.list_folder(request.user, folder)
    owner = folder.owner_id == request.user.pk
    shares = list(folder.shares.select_related("user")) if owner else []
    return render(
        request,
        "drive/browse.html",
        {
            "folder": folder,
            "folders": folders,
            "files": files,
            "crumbs": (crumbs := _crumbs(request.user, folder)),
            "shared_root": bool(crumbs and crumbs[0].parent_id),
            "can_edit": can(request.user, "edit", "drive.folder", folder),
            "is_owner": owner,
            "shares": shares,
        },
    )


@login_required
def shared_view(request):
    items = [
        (item, role, isinstance(item, Folder)) for item, role in svc.shared_with_me(request.user)
    ]
    return render(request, "drive/shared.html", {"items": items})


@login_required
def trash_view(request):
    folders, files = svc.trash_items(request.user)
    return render(request, "drive/trash.html", {"folders": folders, "files": files})


@login_required
@require_POST
def new_folder(request):
    parent = None
    pid = request.POST.get("parent")
    if pid:
        try:
            parent = Folder.objects.filter(public_id=uuid.UUID(pid)).first()
        except ValueError:
            parent = None
        if parent is None:
            raise Http404
        _visible_or_404(request.user, parent)
    _run(
        request,
        svc.create_folder,
        request.user,
        request.POST.get("name", ""),
        parent,
        ok="Folder created.",
    )
    return (
        redirect("drive:folder", folder_id=parent.public_id) if parent else redirect("drive:home")
    )


@login_required
@require_POST
def rename_item(request, kind, item_id):
    item = _visible_or_404(request.user, _item(kind, item_id))
    _run(request, svc.rename, request.user, item, request.POST.get("name", ""), ok="Renamed.")
    return _back(item)


@login_required
@require_POST
def trash_item(request, kind, item_id):
    item = _visible_or_404(request.user, _item(kind, item_id))
    _run(request, svc.trash, request.user, item, ok="Moved to trash. You can restore it.")
    return _back(item)


@login_required
@require_POST
def restore_item(request, kind, item_id):
    item = _item(kind, item_id)
    _visible_or_404(request.user, item)
    _run(request, svc.restore, request.user, item, ok="Restored.")
    return redirect("drive:trash")


def _person(employee_id):
    # Upper() compares the same on every backend; __iexact is case-sensitive on TiDB's binary collation.
    wanted = (employee_id or "").strip().upper()
    if not wanted:
        return None
    return (
        CustomUser.objects.annotate(_eid=Upper("employee_id"))
        .filter(_eid=wanted, is_active=True, status="active")
        .first()
    )


@login_required
@require_POST
def share_item(request, kind, item_id):
    item = _visible_or_404(request.user, _item(kind, item_id))
    person = _person(request.POST.get("employee_id"))
    if person is None:
        messages.error(request, "No employee with that ID.")
    else:
        _run(
            request,
            svc.share,
            request.user,
            item,
            person,
            request.POST.get("role", ""),
            ok=f"Shared with {person.employee_id}.",
        )
    return _back(item) if kind == "file" else redirect("drive:folder", folder_id=item.public_id)


@login_required
@require_POST
def unshare_item(request, kind, item_id):
    item = _visible_or_404(request.user, _item(kind, item_id))
    try:
        person = get_object_or_404(CustomUser, pk=int(request.POST.get("user_id") or 0))
    except ValueError:
        raise Http404 from None
    _run(request, svc.unshare, request.user, item, person, ok="Access removed.")
    return _back(item) if kind == "file" else redirect("drive:folder", folder_id=item.public_id)
