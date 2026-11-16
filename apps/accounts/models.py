from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
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
        HR = "hr", "Human Resources"
        MANAGER = "manager", "Manager"
        EMPLOYEE = "employee", "Employee"
        INTERN = "intern", "Intern"

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVATING = "activating", "Activating"
        ACTIVE = "active", "Active"
        LOCKED = "locked", "Locked"
        DISABLED = "disabled", "Disabled"
        ONBOARDING = "onboarding", "Onboarding"
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
        help_text="Unique company identifier (e.g., ST-EMP-0001). Immutable once created.",
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


class ActivationCode(models.Model):
    """
    One-time administrator-issued passcodes.
    Stored as keyed HMAC-SHA256 hashes.
    Uses nullable marker 'is_live' for TiDB index uniqueness.
    """

    class Purpose(models.TextChoices):
        ACTIVATION = "activation", "Activation"
        PASSWORD_RESET = "password_reset", "Password Reset"
        MFA_RESET = "mfa_reset", "MFA Reset"

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="activation_codes")
    purpose = models.CharField(max_length=32, choices=Purpose.choices, default=Purpose.ACTIVATION)
    keyed_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="issued_codes"
    )
    is_live = models.PositiveSmallIntegerField(
        null=True, blank=True, default=1, help_text="Set to 1 for active code, NULL otherwise."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "purpose", "is_live"],
                name="unique_live_code_per_user_and_purpose",
            )
        ]
        verbose_name = "Activation Code"
        verbose_name_plural = "Activation Codes"

    def __str__(self):
        return f"Code for {self.user.employee_id} [{self.purpose}] (Live={self.is_live == 1})"


class TOTPCredential(models.Model):
    """
    Encrypted RFC 6238 TOTP credential.
    Secret is encrypted at rest using AES-256-GCM.
    """

    user = models.OneToOneField(
        CustomUser, on_delete=models.CASCADE, related_name="totp_credential"
    )
    encrypted_secret = models.BinaryField()
    nonce = models.BinaryField()
    key_id = models.CharField(max_length=32)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "TOTP Credential"
        verbose_name_plural = "TOTP Credentials"

    def __str__(self):
        status = "Confirmed" if self.confirmed_at else "Pending"
        return f"TOTP [{status}] for {self.user.employee_id}"


class RecoveryCode(models.Model):
    """Single-use recovery codes stored as keyed HMAC-SHA256."""

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="recovery_codes")
    keyed_hash = models.CharField(max_length=64, db_index=True, default="")
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Recovery Code"
        verbose_name_plural = "Recovery Codes"

    def __str__(self):
        status = "Used" if self.used_at else "Available"
        return f"RecoveryCode for {self.user.employee_id} [{status}]"


class LoginAttempt(models.Model):
    """Database-backed counter for lockout enforcement across multiple worker processes."""

    key = models.CharField(max_length=128, unique=True, db_index=True)
    counter = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Login Attempt Tracker"
        verbose_name_plural = "Login Attempt Trackers"

    def __str__(self):
        return f"Attempts for {self.key}: {self.counter}"


class AuthEvent(models.Model):
    """
    Append-only structured diagnostic and security audit log.
    Captures reference IDs and sanitized error context. Never secrets or passwords.
    """

    time = models.DateTimeField(auto_now_add=True, db_index=True)
    event = models.CharField(max_length=64, db_index=True)
    code = models.CharField(max_length=32, db_index=True)  # e.g., AUTH-ACT-001
    step = models.CharField(max_length=64)
    employee_id = models.CharField(max_length=32, db_index=True, blank=True)
    reference_id = models.CharField(max_length=32, db_index=True)
    network_address = models.GenericIPAddressField(null=True, blank=True)
    exception_class = models.CharField(max_length=128, blank=True)
    message = models.TextField(blank=True)
    duration_ms = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-time"]
        verbose_name = "Auth Event"
        verbose_name_plural = "Auth Events"

    def __str__(self):
        return f"[{self.time}] {self.code} ({self.reference_id}) - {self.event}"


class UserSession(models.Model):
    """Active server-side tracking of user sessions."""

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="active_sessions")
    session_key = models.CharField(max_length=40, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    mfa_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_activity"]
        verbose_name = "User Session"
        verbose_name_plural = "User Sessions"

    def __str__(self):
        emp_id = self.user.employee_id
        return f"Session {self.session_key[:8]}... for {emp_id} (MFA={self.mfa_verified})"


class PasskeyCredential(models.Model):
    """
    W3C WebAuthn / FIDO2 Passkey Credential.
    Stores public key, credential ID, signature counter, and authenticator metadata.
    Private keys remain securely within the client's authenticator.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="passkeys")
    credential_id = models.CharField(max_length=255, unique=True, db_index=True)
    public_key = models.BinaryField()
    sign_count = models.BigIntegerField(default=0)
    display_name = models.CharField(max_length=128, default="Passkey")
    aaguid = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Passkey Credential"
        verbose_name_plural = "Passkey Credentials"

    def __str__(self):
        return f"{self.display_name} ({self.credential_id[:16]}...) for {self.user.employee_id}"


class WorkspaceDraft(models.Model):
    """
    Server-side draft persistence for work-in-progress across session lock/resume.
    Safely stores unsaved client-side work across idle expirations and re-authentications.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="workspace_drafts")
    path = models.CharField(max_length=255, db_index=True)
    draft_data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Workspace Draft"
        verbose_name_plural = "Workspace Drafts"
        constraints = [
            models.UniqueConstraint(fields=["user", "path"], name="unique_user_path_draft")
        ]

    def __str__(self):
        return f"Draft for {self.user.employee_id} at {self.path}"
