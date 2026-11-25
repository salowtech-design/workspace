import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import CustomUser
from apps.core.services import record_audit_event
from apps.directory.models import (
    CustomFieldDefinition,
    CustomFieldValue,
    Department,
    EmployeeProfile,
    OnboardingStep,
    OnboardingTask,
    ReportingRelationship,
    Team,
    TeamMembership,
)


def create_department(code: str, name: str, actor: CustomUser) -> Department:
    """Create a new department. Admin only."""
    if actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can create departments.")

    dept = Department.objects.create(code=code.strip().upper(), name=name.strip())
    record_audit_event(
        event_type="directory.department_created",
        actor=actor,
        target_entity="Department",
        target_id=dept.code,
        details={"name": dept.name},
    )
    return dept


def create_team(
    code: str, name: str, department: Department, lead: CustomUser = None, actor: CustomUser = None
) -> Team:
    """Create a new team within a department."""
    if actor and actor.role not in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN):
        raise PermissionDenied("Only administrators can create teams.")

    team = Team.objects.create(
        code=code.strip().upper(),
        name=name.strip(),
        department=department,
        lead=lead,
    )
    record_audit_event(
        event_type="directory.team_created",
        actor=actor,
        target_entity="Team",
        target_id=team.code,
        details={"name": team.name, "department": department.code},
    )
    return team


def add_team_member(team: Team, user: CustomUser, actor: CustomUser) -> TeamMembership:
    """Add an employee to a team."""
    if actor.role not in (
        CustomUser.Role.SUPER_ADMIN,
        CustomUser.Role.ADMIN,
        CustomUser.Role.MANAGER,
    ):
        raise PermissionDenied("Unauthorized to assign team memberships.")

    if TeamMembership.objects.filter(team=team, user=user).exists():
        raise ValidationError(f"{user.employee_id} is already a member of {team.name}.")

    membership = TeamMembership.objects.create(team=team, user=user)
    record_audit_event(
        event_type="directory.membership_added",
        actor=actor,
        target_entity="TeamMembership",
        target_id=str(membership.id),
        details={"team": team.code, "user": user.employee_id},
    )
    return membership


def _reporting_lock():
    """Lock the single ReportingLock row first, so two changes cannot each pass the cycle check
    against data the other has not committed. The row has its own table (created by a migration,
    re-created here if someone deleted it); nothing else deletes it. If the row is missing, two
    callers may both try to create it: the unique name makes one fail, and it retries the lock."""
    from django.db import IntegrityError

    from .models import ReportingLock

    for _ in range(3):
        row = ReportingLock.objects.select_for_update().filter(name="reporting").first()
        if row is not None:
            return row
        try:
            with transaction.atomic():
                ReportingLock.objects.create(name="reporting")
        except IntegrityError:
            pass
    raise ValidationError("Could not take the reporting lock. Try again.")


def _pause():
    """Test hook, called after the checks and before the write. A no-op."""


_LIVE = (CustomUser.Status.ACTIVE, CustomUser.Status.ONBOARDING)


def assign_reporting_manager(
    manager: CustomUser, subordinate: CustomUser, actor: CustomUser
) -> ReportingRelationship:
    """Assign or update direct manager for an employee.

    Authority is the existing rule in this module: super admin and admin only. (The blueprint
    proposes HR; that difference is open and is not applied here.) Refuses self-management,
    people who are not active or onboarding, and any change that would make a reporting loop.
    The actor's role and status and the loop check are read fresh under the reporting lock."""
    admin_roles = (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)
    if actor.role not in admin_roles:
        raise PermissionDenied("Only administrators can configure reporting relationships.")

    with transaction.atomic():
        _reporting_lock()
        fresh_actor = CustomUser.objects.filter(
            pk=actor.pk, status=CustomUser.Status.ACTIVE, is_active=True, role__in=admin_roles
        ).first()
        if fresh_actor is None:  # demoted or deactivated since the caller loaded them
            raise PermissionDenied("Only administrators can configure reporting relationships.")
        mgr = CustomUser.objects.filter(pk=manager.pk, status__in=_LIVE).first()
        sub = CustomUser.objects.filter(pk=subordinate.pk, status__in=_LIVE).first()
        if mgr is None or sub is None:
            raise ValidationError("Both people must be active or onboarding employees.")
        if mgr.pk == sub.pk:
            raise ValidationError(
                "Self-management is prohibited: an employee cannot manage themselves."
            )
        current = ReportingRelationship.objects.filter(subordinate=sub).first()
        if current is not None and current.manager_id == mgr.pk:
            return current  # nothing changes: no write, no audit row
        up, seen = mgr.pk, set()
        while up is not None and up not in seen:  # walk the manager's chain on fresh rows
            if up == sub.pk:
                raise ValidationError(
                    "Reporting cycle detected: an employee cannot report to their subordinate."
                )
            seen.add(up)
            up = (
                ReportingRelationship.objects.filter(subordinate_id=up)
                .values_list("manager_id", flat=True)
                .first()
            )
        _pause()
        rel, _ = ReportingRelationship.objects.update_or_create(
            subordinate=sub,
            defaults={"manager": mgr},
        )
        record_audit_event(
            event_type="directory.manager_assigned",
            actor=actor,
            target_entity="ReportingRelationship",
            target_id=str(rel.id),
            details={"subordinate": sub.employee_id, "manager": mgr.employee_id},
        )
        return rel


def update_employee_profile(
    target_user: CustomUser, data: dict, actor: CustomUser
) -> EmployeeProfile:
    """Update profile data. Employee can update self; Admin can update any."""
    is_self = actor.id == target_user.id
    is_admin = actor.role in (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)

    if not (is_self or is_admin):
        raise PermissionDenied("Unauthorized to modify this profile.")

    profile, _ = EmployeeProfile.objects.get_or_create(user=target_user)

    if "phone" in data:
        profile.phone = data["phone"].strip()
    if "emergency_contact" in data:
        profile.emergency_contact = data["emergency_contact"].strip()
    if "bio" in data:
        profile.bio = data["bio"].strip()
    if "location" in data:
        profile.location = data["location"].strip()
    if is_admin and "designation" in data:
        profile.designation = data["designation"].strip()

    profile.save()
    record_audit_event(
        event_type="directory.profile_updated",
        actor=actor,
        target_entity="EmployeeProfile",
        target_id=target_user.employee_id,
        details={"updated_fields": list(data.keys())},
    )
    return profile


_ADMINS = (CustomUser.Role.SUPER_ADMIN, CustomUser.Role.ADMIN)
_FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def _require_admin(actor: CustomUser):
    """Re-read the actor so a demoted or deactivated admin object is not trusted."""
    fresh = CustomUser.objects.filter(
        pk=actor.pk, role__in=_ADMINS, status=CustomUser.Status.ACTIVE, is_active=True
    ).first()
    if fresh is None:
        raise PermissionDenied("Only administrators can manage custom profile fields.")
    return fresh


def create_custom_field(
    key: str, label: str, field_type: str, actor: CustomUser, required: bool = False
) -> CustomFieldDefinition:
    """Define a custom profile field. Administrators only (existing directory authority).
    `required` is enforced when a value is set; it does not backfill people who have none."""
    _require_admin(actor)  # early refusal; checked again inside the transaction
    key, label = (key or "").strip().lower(), (label or "").strip()
    if not _FIELD_KEY.match(key):
        raise ValidationError("Key: 2 to 40 characters, lowercase letters, digits, underscore.")
    if not 1 <= len(label) <= 80:
        raise ValidationError("Label must be 1 to 80 characters.")
    if field_type not in CustomFieldDefinition.FieldType.values:
        raise ValidationError("Unknown field type.")
    with transaction.atomic():
        _require_admin(actor)
        try:
            with transaction.atomic():
                d = CustomFieldDefinition.objects.create(
                    key=key, label=label, field_type=field_type, required=bool(required)
                )
        except IntegrityError:
            raise ValidationError("A field with that key already exists.") from None
        record_audit_event(  # outside the try: an audit failure is not reported as a duplicate
            event_type="directory.custom_field_created",
            actor=actor,
            target_entity="CustomFieldDefinition",
            target_id=str(d.id),
            details={"key": d.key, "type": d.field_type},
        )
    return d


def _norm_text(text):
    if len(text) > 500:
        raise ValidationError("Text can be at most 500 characters.")
    return text


def _norm_number(text):
    if len(text) > 40 or not text.isascii() or "_" in text:
        raise ValidationError("Enter a number.")
    try:
        n = Decimal(text)
    except InvalidOperation:
        raise ValidationError("Enter a number.") from None
    if not n.is_finite() or abs(n) >= Decimal("1e15"):
        raise ValidationError("Enter a number.")
    if n.is_zero():
        return "0"  # also turns -0 into 0
    if n.as_tuple().exponent < -12 or abs(n) < Decimal("1e-12"):
        raise ValidationError("Use at most 12 decimal places.")
    out = format(n.normalize(), "f")
    if len(out) > 40:
        raise ValidationError("Enter a number.")
    return out


def _norm_date(text):
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValidationError("Enter a date as YYYY-MM-DD.") from None


def _norm_yes_no(text):
    answer = {"yes": "yes", "true": "yes", "1": "yes", "no": "no", "false": "no", "0": "no"}
    if text.lower() not in answer:
        raise ValidationError("Enter yes or no.")
    return answer[text.lower()]


_NORMALISERS = {
    "text": _norm_text,
    "number": _norm_number,
    "date": _norm_date,
    "yes_no": _norm_yes_no,
}


def normalise_custom_value(definition: CustomFieldDefinition, raw) -> str:
    """Return the stored text for a value of the field's type, or raise ValidationError."""
    text = ("" if raw is None else str(raw)).strip()
    return _NORMALISERS[definition.field_type](text) if text else ""


def set_custom_value(user: CustomUser, key: str, raw, actor: CustomUser):
    """Set or clear one person's value. Administrators only. A blank clears an optional field
    and is refused for a required one. The audit row records ids, never the value."""
    _require_admin(actor)
    with transaction.atomic():
        _require_admin(actor)
        d = CustomFieldDefinition.objects.select_for_update().filter(key=(key or "")).first()
        if d is None:
            raise ValidationError("Unknown field.")
        target = CustomUser.objects.filter(pk=user.pk).first()
        if target is None:
            raise ValidationError("Unknown person.")
        value = normalise_custom_value(d, raw)
        if not value and d.required:
            raise ValidationError(f"{d.label} is required.")
        existing = CustomFieldValue.objects.filter(user=target, definition=d).first()
        if not value:
            if existing is not None:
                existing.delete()
        elif existing is None:
            CustomFieldValue.objects.create(user=target, definition=d, value=value)
        elif existing.value != value:
            existing.value = value
            existing.save(update_fields=["value", "updated_at"])
        else:
            return value  # unchanged: no write, no audit row
        record_audit_event(
            event_type="directory.custom_value_set",
            actor=actor,
            target_entity="CustomFieldValue",
            target_id=f"{target.employee_id}:{d.key}",
            details={"cleared": not value},
        )
        return value


def add_onboarding_step(title, actor, owner_role="", due_after_days=0):
    """Add a step to the onboarding template. Administrators only (existing authority)."""
    _require_admin(actor)
    title = (title or "").strip()
    if not 1 <= len(title) <= 120:
        raise ValidationError("Title must be 1 to 120 characters.")
    if not 0 <= int(due_after_days) <= 365:
        raise ValidationError("Due days must be 0 to 365.")
    valid_roles = {r.value for r in CustomUser.Role}
    if owner_role and owner_role not in valid_roles:
        raise ValidationError("Unknown role.")
    with transaction.atomic():
        _require_admin(actor)
        last = (
            OnboardingStep.objects.order_by("-position").values_list("position", flat=True).first()
        )
        try:
            with transaction.atomic():
                step = OnboardingStep.objects.create(
                    title=title,
                    owner_role=owner_role,
                    due_after_days=int(due_after_days),
                    position=(last or 0) + 1,
                )
        except IntegrityError:
            raise ValidationError("A step with that title already exists.") from None
        record_audit_event(
            event_type="directory.onboarding_step_added",
            actor=actor,
            target_entity="OnboardingStep",
            target_id=str(step.id),
            details={},
        )
    return step


def _lock_person(pk):
    return CustomUser.objects.select_for_update().filter(pk=pk).first()


def _pause_onboarding():
    """Test hook after the existing steps are read. A no-op."""


_CATCH_DUPLICATE = True  # tests switch it off to see the raw error the backstop absorbs


def start_onboarding(user, actor, start_date=None):
    """Copy the template into tasks for one person. Administrators only. Safe to repeat and safe
    to run twice at once: the person's row is locked, and if a duplicate still slips through the
    unique key (person, title) the whole attempt is rolled back and reported as nothing created,
    because the other start already made the steps. Returns tasks created."""
    from django.db import IntegrityError

    try:
        return _start_onboarding(user, actor, start_date)
    except IntegrityError:
        if not _CATCH_DUPLICATE:
            raise
        return []


def _start_onboarding(user, actor, start_date):
    from datetime import timedelta

    from django.utils import timezone

    _require_admin(actor)
    start_date = start_date or timezone.localdate()
    created = []
    with transaction.atomic():
        _require_admin(actor)
        target = _lock_person(user.pk)
        if target is None or target.status not in _LIVE:
            raise ValidationError("Onboarding needs an active or onboarding person.")
        have = set(OnboardingTask.objects.filter(user=target).values_list("title", flat=True))
        _pause_onboarding()
        for step in OnboardingStep.objects.all():
            if step.title in have:
                continue
            created.append(
                OnboardingTask.objects.create(
                    user=target,
                    title=step.title,
                    owner_role=step.owner_role,
                    due_date=start_date + timedelta(days=step.due_after_days),
                    position=step.position,
                )
            )
        if created:
            record_audit_event(
                event_type="directory.onboarding_started",
                actor=actor,
                target_entity="CustomUser",
                target_id=target.employee_id,
                details={"tasks": len(created)},
            )
    return created


def complete_onboarding_task(task_id, actor):
    """Mark a task done. Administrators only for now. Already done: no change, no audit row."""
    from django.utils import timezone

    _require_admin(actor)
    with transaction.atomic():
        _require_admin(actor)
        t = OnboardingTask.objects.select_for_update().filter(pk=task_id).first()
        if t is None:
            raise ValidationError("Unknown task.")
        if t.state == OnboardingTask.State.DONE:
            return t
        t.state, t.completed_at = OnboardingTask.State.DONE, timezone.now()
        t.save(update_fields=["state", "completed_at"])
        record_audit_event(
            event_type="directory.onboarding_task_done",
            actor=actor,
            target_entity="OnboardingTask",
            target_id=str(t.id),
            details={},
        )
        return t
