from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models


class UserManager(BaseUserManager):
    """Custom user manager for Employee ID authentication."""

    def create_user(self, employee_id, full_name, password=None, **extra_fields):
        if not employee_id:
            raise ValueError("The Employee ID must be set.")
        if not full_name:
            raise ValueError("The full name must be set.")

        normalized_id = employee_id.strip().upper()
        user = self.model(
            employee_id=normalized_id,
            full_name=full_name.strip(),
            **extra_fields,
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, employee_id, full_name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", CustomUser.Role.SUPER_ADMIN)
        extra_fields.setdefault("status", CustomUser.Status.ACTIVE)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(employee_id, full_name, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Core account and user entity.
    Authenticates via immutable employee_id.
    """

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        EMPLOYEE = "employee", "Employee"
        INTERN = "intern", "Intern"

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ONBOARDING = "onboarding", "Onboarding"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        EXITED = "exited", "Exited"

    class AttendanceMode(models.TextChoices):
        FACE_PREFERRED = "face_preferred", "Face Preferred"
        MANUAL_ONLY = "manual_only", "Manual Only"

    employee_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Unique company identifier (e.g., EMP001). Immutable once created.",
    )
    email = models.EmailField(unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=128)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    attendance_mode = models.CharField(
        max_length=20,
        choices=AttendanceMode.choices,
        default=AttendanceMode.FACE_PREFERRED,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "employee_id"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "User Account"
        verbose_name_plural = "User Accounts"
        ordering = ["employee_id"]

    def __str__(self):
        return f"{self.employee_id} - {self.full_name}"

    def clean(self):
        super().clean()
        if self.employee_id:
            self.employee_id = self.employee_id.strip().upper()

    def save(self, *args, **kwargs):
        self.clean()
        if self.pk:
            original = CustomUser.objects.get(pk=self.pk)
            if original.employee_id != self.employee_id:
                raise ValidationError(
                    "Employee ID is immutable and cannot be changed after creation."
                )
        super().save(*args, **kwargs)
