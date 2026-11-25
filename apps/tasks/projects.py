"""Projects, stage 2 first step: create, see, read the member list, read the board.
Member add/remove, project tasks and milestones are NOT built: what membership reveals and who
may add people is an open policy choice. Nothing here notifies anyone."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404

from apps.core.foundation.audit import audit
from apps.tasks.models import Column, Project, ProjectMember

DEFAULT_COLUMNS = ("To do", "Doing", "Done")  # build default, not policy


def _membership(user, project):
    return ProjectMember.objects.filter(
        project=project, person=user, removed_at__isnull=True, person__is_active=True
    ).first()


def can_view(user, project):
    return project.deleted_at is None and _membership(user, project) is not None


@transaction.atomic
def create_project(user, name):
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationError("A project needs a name of up to 120 characters.")
    p = Project.objects.create(name=name, created_by=user)
    ProjectMember.objects.create(project=p, person=user, role=ProjectMember.LEAD, added_by=user)
    Column.objects.bulk_create(
        [Column(project=p, name=n, position=i) for i, n in enumerate(DEFAULT_COLUMNS)]
    )
    audit(user, "tasks.project.create", "project", p.pk)
    return p


def projects_for(user):
    return Project.objects.filter(
        deleted_at__isnull=True,
        members__person=user,
        members__removed_at__isnull=True,
    ).order_by("name", "id")


def get_project(user, public_id):
    """Anyone who is not a current member gets the same not-found as a missing project."""
    p = Project.objects.filter(public_id=public_id).first()
    if p is None or not can_view(user, p):
        raise Http404
    return p


def members_of(project):
    return (
        ProjectMember.objects.filter(project=project, removed_at__isnull=True)
        .select_related("person")
        .order_by("-role", "person__full_name", "id")
    )
