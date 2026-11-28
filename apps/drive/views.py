import uuid
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F
from django.db.models.functions import Upper
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import CustomUser
from apps.core.foundation.permissions import can
from apps.drive import services as svc
from apps.drive.models import DriveFile, FileVersion, Folder, Share


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


def _with_hashes(files):
    """Attach the CURRENT version's SHA-256 to each file with one query filtered in the database (older versions are never
    read). Version 0 has none: no bytes recorded."""
    ids = [f.pk for f in files if f.current_version]
    have = {}
    if ids:
        rows = FileVersion.objects.filter(file_id__in=ids, number=F("file__current_version")).values("file_id", "sha256")
        have = {r["file_id"]: r["sha256"] for r in rows}
    for f in files:
        f.device_sha = have.get(f.pk, "") if f.current_version else ""
    return files


@login_required
def drive_home(request):
    folders, files = svc.list_folder(request.user, None)
    _with_hashes(files)
    return render(
        request,
        "drive/browse.html",
        {"folder": None, "folders": folders, "files": files, "crumbs": [], "can_edit": True, **_move_ctx(request.user)},
    )


@login_required
def folder_view(request, folder_id):
    folder = _visible_or_404(request.user, get_object_or_404(Folder, public_id=folder_id))
    folders, files = svc.list_folder(request.user, folder)
    _with_hashes(files)
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
            "shared_root": bool(
                crumbs and (crumbs[0].parent_id or crumbs[0].owner_id != request.user.pk)
            ),
            "can_edit": can(request.user, "edit", "drive.folder", folder),
            "is_owner": owner,
            "shares": shares,
            **(_move_ctx(request.user) if owner else {"move_targets": [], "move_more": False}),
        },
    )


@login_required
def shared_view(request):
    items = [
        (item, role, isinstance(item, Folder)) for item, role in svc.shared_with_me(request.user)
    ]
    # directly shared FILES get the same on-device controls as a folder page, from the file's own grant: nothing here links to or
    # names the parent folder, and no folder access is implied. Edit rights are the server's own answer for this person.
    shared_files = [i for i, _r, is_folder in items if not is_folder]
    _with_hashes(shared_files)
    for f in shared_files:
        f.device_edit = can(request.user, "edit", "drive.file", f)
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


MOVE_TARGET_MAX = 50  # options per Move list; the list repeats on each row, so it is capped


def _move_targets(user, query=""):
    """The owner's own live folders, at most MOVE_TARGET_MAX by name, each marked `shared` when it or
    a folder above it is shared (moving there widens the audience). The service still checks every move."""
    rows = list(
        Folder.objects.filter(owner=user, trashed_at__isnull=True).values_list(
            "pk", "parent_id", "name", "public_id"
        )
    )
    parents = {pk: parent for pk, parent, _, _ in rows}
    direct = set(Share.objects.filter(folder_id__in=list(parents)).values_list("folder_id", flat=True))

    def shared(pk):
        n = 0
        while pk is not None and n <= 30:
            if pk in direct:
                return True
            pk, n = parents.get(pk), n + 1
        return False

    rows.sort(key=lambda r: (r[2].lower(), r[0]))
    shown = [r for r in rows if query.lower() in r[2].lower()] if query else rows
    out = [
        SimpleNamespace(pk=pk, name=name, public_id=pid, shared=shared(pk))
        for pk, _, name, pid in shown[:MOVE_TARGET_MAX]
    ]
    return out, len(shown) > MOVE_TARGET_MAX


def _move_ctx(user):
    targets, more = _move_targets(user)
    return {"move_targets": targets, "move_more": more}


@login_required
def move_find(request, kind, item_id):
    """Read-only: search the owner's folders by name, so a folder beyond the first 50 in the Move list can be
    chosen. Choosing one posts to move_item, which runs the usual checks and the audience confirmation."""
    item = _visible_or_404(request.user, _item(kind, item_id))
    q = (request.GET.get("q") or "").strip()[:100]
    targets, more = _move_targets(request.user, q) if q else ([], False)
    targets = [t for t in targets if not (isinstance(item, Folder) and t.pk == item.pk)]
    return render(
        request,
        "drive/move_find.html",
        {"item": item, "kind": kind, "q": q, "targets": targets, "more": more, "back": _back(item).url},
    )


@login_required
@require_POST
def move_item(request, kind, item_id):
    item = _visible_or_404(request.user, _item(kind, item_id))
    raw = (request.POST.get("destination") or "").strip()
    destination = None
    if raw:
        try:
            destination = Folder.objects.filter(public_id=uuid.UUID(raw)).first()
        except ValueError:
            destination = None
        if destination is None or not can(request.user, "view", "drive.folder", destination):
            messages.error(request, "That destination folder was not found.")
            return _back(item)
    try:  # refuse an invalid move before asking anyone to confirm it
        svc.check_move(request.user, item, destination)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _back(item)
    except PermissionDenied:
        messages.error(request, "You do not have permission to do that.")
        return _back(item)
    gain, lose = svc.move_audience(item, destination)
    if gain:  # widening: the confirmation must match the sharing as it is right now
        digest = svc.audience_digest(item, destination, gain)
        if request.POST.get("confirm") != "1" or request.POST.get("digest") != digest:
            owner = item.owner_id == request.user.pk
            return render(
                request,
                "drive/confirm_move.html",
                {
                    "item": item,
                    "kind": kind,
                    "destination": destination,
                    "owner": owner,  # names only for the owner; others see counts
                    "gain": gain,
                    "lose": lose,
                    "digest": digest,
                    "nested": svc.nested_share_count(item) if owner else 0,  # a count of other people's shares is the owner's to see
                    "changed": request.POST.get("confirm") == "1",  # a stale or missing digest
                    "back": _back(item).url,
                },
            )
    _run(request, svc.move, request.user, item, destination, ok="Moved.")
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


SHARE_LIMIT = 30  # share/unshare actions per person per window
SHARE_WINDOW_SECONDS = 60


def _share_throttled(user):
    from apps.core.foundation.throttle import throttled

    return throttled(
        user, "drive-share", SHARE_LIMIT, SHARE_WINDOW_SECONDS
    )  # limits read at call time


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
    if _share_throttled(request.user):
        return HttpResponse("Too many sharing changes. Try again in a minute.", status=429)
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
    if _share_throttled(request.user):
        return HttpResponse("Too many sharing changes. Try again in a minute.", status=429)
    wanted = (request.POST.get("employee_id") or "").strip().upper()
    person = (
        CustomUser.objects.annotate(_eid=Upper("employee_id")).filter(_eid=wanted).first()
        if wanted
        else None
    )
    if person is not None:
        _run(request, svc.unshare, request.user, item, person, ok="Access removed.")
    else:
        messages.success(request, "Access removed.")  # same outcome as a person who had no share
    return _back(item) if kind == "file" else redirect("drive:folder", folder_id=item.public_id)


# ---- file records (metadata only) --------------------------------------------------------------
# The file bytes never reach the server: the browser keeps them on the person's device and sends only
# the name, size and SHA-256. These endpoints say nothing about whether any device holds the bytes.

def _json_error(exc):
    if isinstance(exc, PermissionDenied):
        return JsonResponse({"ok": False, "error": "You do not have permission to do that."}, status=403)
    return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=400)


def _file_json(f):
    return {
        "id": str(f.public_id),
        "name": f.name,
        "version": f.current_version,
        "size": f.size,
        "content_type": f.content_type,
    }


@login_required
@require_GET
@never_cache
def own_files(request):
    """The person's OWN files at the top of My Drive that have a saved version: id, name, size, version and the checksum of the current
    version. Records only, never bytes. Nothing shared with them and nothing inside a folder is listed, so this opens no new audience.
    Used by Sheets and Slides to offer 'Open from Drive' for files that are on this device."""
    _folders, files = svc.list_folder(request.user, None)
    files = _with_hashes([f for f in files if f.current_version])
    files.sort(key=lambda f: (f.name.lower(), f.pk))
    return JsonResponse(
        {"ok": True, "files": [{"id": str(f.public_id), "name": f.name, "size": f.size, "version": f.current_version, "sha256": f.device_sha} for f in files[:200]], "truncated": len(files) > 200}
    )


@login_required
@require_POST
def file_new(request):
    """Create a file RECORD (version 0, no bytes recorded) in a folder the person may edit, or in My Drive."""
    folder = None
    fid = request.POST.get("folder")
    if fid:
        try:
            folder = Folder.objects.filter(public_id=uuid.UUID(fid)).first()
        except ValueError:
            folder = None
        if folder is None:
            raise Http404
        _visible_or_404(request.user, folder)
    try:
        f = svc.create_file(request.user, request.POST.get("name", ""), folder, request.POST.get("content_type", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _json_error(exc)
    return JsonResponse({"ok": True, "file": _file_json(f)}, status=201)


@login_required
@require_POST
def file_version(request, item_id):
    """Record that a new version (size and SHA-256 the browser computed) exists. 409 when someone saved first."""
    f = _visible_or_404(request.user, get_object_or_404(DriveFile, public_id=item_id))
    try:
        base = int(request.POST.get("base_version", ""))
        size = int(request.POST.get("size", ""))
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid version or size."}, status=400)
    try:
        v = svc.add_version(request.user, f, base, size, request.POST.get("sha256", ""))
    except svc.VersionConflict as exc:
        f.refresh_from_db()
        return JsonResponse({"ok": False, "error": " ".join(exc.messages), "current_version": f.current_version}, status=409)
    except (ValidationError, PermissionDenied) as exc:
        return _json_error(exc)
    # report exactly what THIS call committed (another writer may already have saved a newer version)
    out = _file_json(f)
    out.update(version=v.number, size=v.size)
    return JsonResponse({"ok": True, "file": out})
