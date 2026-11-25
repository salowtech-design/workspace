from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Department(models.Model):
    """Organizational department grouping."""

    code = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Department"
        verbose_name_plural = "Departments"

    def __str__(self):
        return f"{self.name} ({self.code})"

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class Team(models.Model):
    """Team within a department. Enforces at most one lead per team."""

    code = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=128)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="teams")
    lead = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_team",
        help_text="Team lead. Strictly at most one team lead per team.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Team"
        verbose_name_plural = "Teams"

    def __str__(self):
        return f"{self.name} [{self.code}]"

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class TeamMembership(models.Model):
    """Membership of an employee in a team."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="team_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-joined_at"]
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="unique_team_user_membership")
        ]
        verbose_name = "Team Membership"
        verbose_name_plural = "Team Memberships"

    def __str__(self):
        return f"{self.user.employee_id} in {self.team.name}"


class ReportingRelationship(models.Model):
    """
    Direct reporting hierarchy.
    The database enforces one manager per person (unique). The no-self-reporting check
    constraint is enforced on SQLite; TiDB 8.5.1 accepted a self row in a test run, so there the
    save() method and the service are the only guards.
    Acyclic graphs are NOT a database invariant: they are kept by the service
    assign_reporting_manager (under the reporting lock). Rows written any other way, such as the
    admin site or bulk writes, skip that check.
    """

    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subordinate_relationships",
    )
    subordinate = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="manager_relationship",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["manager__employee_id"]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(manager=models.F("subordinate")),
                name="reporting_no_self_manager",
            )
        ]
        verbose_name = "Reporting Relationship"
        verbose_name_plural = "Reporting Relationships"

    def __str__(self):
        return f"{self.subordinate.employee_id} reports to {self.manager.employee_id}"

    def clean(self):
        super().clean()
        if self.manager_id == self.subordinate_id:
            raise ValidationError(
                "Self-management is prohibited: an employee cannot manage themselves."
            )

        # Cycle detection: check if subordinate is an ancestor of manager
        current = self.manager
        visited = {self.subordinate_id}
        while hasattr(current, "manager_relationship") and current.manager_relationship:
            mgr_id = current.manager_relationship.manager_id
            if mgr_id in visited:
                raise ValidationError(
                    "Reporting cycle detected: an employee cannot report to their subordinate."
                )
            visited.add(mgr_id)
            current = current.manager_relationship.manager

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class EmployeeProfile(models.Model):
    """Extended public and contact profile for company directory."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    phone = models.CharField(max_length=32, blank=True)
    emergency_contact = models.CharField(max_length=255, blank=True)
    designation = models.CharField(max_length=128, blank=True)
    bio = models.TextField(blank=True)
    location = models.CharField(max_length=128, blank=True, default="Bangalore, India")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Employee Profile"
        verbose_name_plural = "Employee Profiles"

    def __str__(self):
        return f"Profile of {self.user.employee_id}"


class ReportingLock(models.Model):
    """Single row that every reporting change locks first. Its own table, so the IAM lock
    release tool and the rate-limit purge job (which delete rate-limit rows) cannot remove it."""

    name = models.CharField(max_length=20, unique=True, default="reporting")

    class Meta:
        verbose_name = "Reporting lock"
