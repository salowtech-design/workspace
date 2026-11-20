import uuid
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
        SECURITY_ADMIN = "security_admin", "Security Admin"
        SECURITY_REVIEWER = "security_reviewer", "Security Reviewer"

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
    email_verified = models.BooleanField(default=False)
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
            original = CustomUser.objects.filter(pk=self.pk).values("employee_id").first()
            if original and original["employee_id"] != self.employee_id:
                if not getattr(self, "_allow_employee_id_change", False):
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
        EMAIL_VERIFICATION = "email_verification", "Email Verification"
        MFA_REPLACEMENT = "mfa_replacement", "MFA Replacement"

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
    session_digest = models.CharField(max_length=64, blank=True, default="", db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    mfa_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_activity"]
        verbose_name = "User Session"
        verbose_name_plural = "User Sessions"

    def save(self, *args, **kwargs):
        if self.session_key and not self.session_digest:
            from apps.accounts.auth.session_backend import hash_session_bearer
            self.session_digest = hash_session_bearer(self.session_key)
        super().save(*args, **kwargs)

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


class RecoveryRequest(models.Model):
    """
    Tracks administrative and privileged recovery requests.
    Supports Method 1 (Admin Password Reset) and Method 3 (Privileged Authenticator Replacement).
    Enforces dual-administrator approval for Super Admin accounts and distinct approvers.
    """

    class RequestType(models.TextChoices):
        ADMIN_PASSWORD_RESET = "admin_password_reset", "Admin Password Reset"
        MFA_REPLACEMENT = "mfa_replacement", "MFA Replacement"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        COMPLETED = "completed", "Completed"
        EXPIRED = "expired", "Expired"

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="recovery_requests")
    request_type = models.CharField(
        max_length=32, choices=RequestType.choices, default=RequestType.ADMIN_PASSWORD_RESET
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reason = models.TextField(blank=True, default="")
    rejection_reason = models.TextField(blank=True, default="")

    # Approvers (Dual-control support)
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_recoveries",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    second_approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="second_approved_recoveries",
    )
    second_approved_at = models.DateTimeField(null=True, blank=True)

    # Generated reset token verifier after approval (never store raw reset token)
    token_hash = models.CharField(max_length=128, blank=True, default="", db_index=True)
    reset_token = models.CharField(max_length=128, blank=True, default="", editable=False)
    token_expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Recovery Request"
        verbose_name_plural = "Recovery Requests"

    def __str__(self):
        return f"RecoveryRequest [{self.request_type}] for {self.user.employee_id} ({self.status})"


class ServiceIdentity(models.Model):
    """
    Machine-to-machine (M2M) API service identity.
    Used by background workers, daemons, and external integrations with scoped permissions.
    API token is stored as a keyed HMAC-SHA256 hash.
    """

    name = models.CharField(max_length=128)
    client_id = models.CharField(max_length=64, unique=True, db_index=True)
    secret_hash = models.CharField(max_length=64, db_index=True)
    scopes = models.JSONField(default=list, blank=True, help_text="Granted scope strings")
    is_active = models.BooleanField(default=True, db_index=True)
    is_non_interactive = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_service_identities",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Service Identity"
        verbose_name_plural = "Service Identities"

    def __str__(self):
        status = "Active" if self.is_active else "Revoked"
        return f"Service {self.name} [{self.client_id}] ({status})"


class DeviceApprovalRequest(models.Model):
    """
    Subsystem for secondary device authorization.
    Enforces 1-minute expiration, in-app approval on primary device,
    administrative fallback, email OTP verification, and biometric verification.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="device_approval_requests",
    )
    requesting_device_ip = models.GenericIPAddressField(null=True, blank=True)
    requesting_device_agent = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    # Approval decision
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_device_requests",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_type = models.CharField(max_length=32, default="primary_device")
    rejection_reason = models.CharField(max_length=255, blank=True, default="")

    # Email OTP & Face Verification
    number_challenge = models.PositiveSmallIntegerField(
        default=0,
        help_text="Transaction-bound 2-digit number challenge for visual matching verification",
    )
    requesting_device_id = models.CharField(max_length=64, blank=True, default="")
    approving_device_id = models.CharField(max_length=64, blank=True, default="")
    email_otp_hash = models.CharField(max_length=64, blank=True, default="")
    email_otp_verified = models.BooleanField(default=False)
    face_verified = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    session_key = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Device Approval Request"
        verbose_name_plural = "Device Approval Requests"

    def __str__(self):
        return f"DeviceApproval [{self.request_id}] for {self.user.employee_id} ({self.status})"


class SecurityAlert(models.Model):
    """
    Append-only security operations alert record.
    Generated upon critical events (repeated auth failures, account lockout,
    privileged role changes, MFA reset, recovery code consumption, device approvals, break-glass).
    Acknowledgement records actor, timestamp, and justification reason without altering the event.
    """

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Category(models.TextChoices):
        REPEATED_AUTH_FAILURES = "repeated_auth_failures", "Repeated authentication failures"
        ACCOUNT_LOCKOUT = "account_lockout", "Account lockout"
        PRIVILEGED_ROLE_CHANGE = "privileged_role_change", "New administrator or privileged role"
        MFA_RESET = "mfa_reset", "MFA reset"
        RECOVERY_CODE_USED = "recovery_code_used", "Recovery code use"
        NEW_DEVICE_APPROVAL = "new_device_approval", "New-device approval"
        ADMIN_FALLBACK = "admin_fallback", "Administrator fallback"
        BREAK_GLASS_ACCESS = "break_glass_access", "Break-glass access"
        AUDIT_INTEGRITY = "audit_integrity", "Audit integrity incident"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    category = models.CharField(max_length=64, choices=Category.choices, db_index=True)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.HIGH, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    employee_id = models.CharField(max_length=32, blank=True, db_index=True)
    network_address = models.GenericIPAddressField(null=True, blank=True)
    reference_id = models.CharField(max_length=64, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Triaged / Acknowledged
    acknowledged_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_alerts",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledgement_reason = models.TextField(blank=True, default="")

    resolved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_alerts",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Security Alert"
        verbose_name_plural = "Security Alerts"

    def __str__(self):
        return f"SecurityAlert [{self.severity.upper()}] {self.category} ({self.status})"


class IAMAdminIdentity(models.Model):
    """
    Dedicated IAM Administrator Identity.
    Strictly segregated from ordinary Workspace employee credentials and authorities.
    Enforces minimum 16-character password, independent TOTP secret, and explicit assurance levels.
    """

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        ADMIN = "admin", "Admin"
        SECURITY_ADMIN = "security_admin", "Security Admin"
        SECURITY_REVIEWER = "security_reviewer", "Security Reviewer"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        LOCKED = "locked", "Locked"

    admin_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Dedicated IAM identifier (e.g. IAM-SUPER-01, IAM-ADMIN-01)",
    )
    user = models.OneToOneField(
        "CustomUser",
        on_delete=models.CASCADE,
        related_name="iam_admin_profile",
        help_text="Authoritative link to real person/employee record for non-repudiation.",
    )
    password_hash = models.CharField(max_length=255)
    encrypted_totp_secret = models.BinaryField(null=True, blank=True)
    totp_nonce = models.BinaryField(null=True, blank=True)
    totp_key_id = models.CharField(max_length=32, blank=True, default="")
    totp_confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(default=0)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.ADMIN)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.ACTIVE)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "IAM Admin Identity"
        verbose_name_plural = "IAM Admin Identities"
        ordering = ["admin_id"]

    def __str__(self):
        return f"{self.admin_id} ({self.role}) [{self.status}]"


class IAMSession(models.Model):
    """
    Dedicated server-side session authority for IAM console on iam.salowtech.com.
    Maintains assurance level (L1 baseline, L2 step-up, L3 dual-control).
    """

    session_key = models.CharField(max_length=64, unique=True, db_index=True)
    session_digest = models.CharField(max_length=64, blank=True, default="", db_index=True)
    iam_admin = models.ForeignKey(
        IAMAdminIdentity,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    assurance_level = models.PositiveSmallIntegerField(default=1)  # 1 = L1, 2 = L2, 3 = L3
    stepup_action = models.CharField(max_length=128, blank=True, default="")
    stepup_expires_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = "IAM Session"
        verbose_name_plural = "IAM Sessions"
        ordering = ["-last_activity"]

    def save(self, *args, **kwargs):
        if self.session_key and not self.session_digest:
            from apps.accounts.auth.iam_auth import hash_session_bearer
            self.session_digest = hash_session_bearer(self.session_key)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"IAMSession {self.session_key[:8]}... [{self.iam_admin.admin_id}] (L{self.assurance_level})"


class AuditCheckpoint(models.Model):
    """
    Subsystem 1: Cryptographic hash chaining and signed checkpoints.
    Guarantees tamper evidence across append-only AuthEvents and AuditLogs.
    """

    checkpoint_id = models.CharField(max_length=64, primary_key=True)
    start_event_id = models.BigIntegerField(default=0)
    end_event_id = models.BigIntegerField(default=0)
    event_count = models.PositiveIntegerField(default=0)
    cumulative_hash = models.CharField(max_length=64, db_index=True)
    previous_checkpoint_hash = models.CharField(max_length=64, default="")
    key_id = models.CharField(max_length=32, default="k1", db_index=True)
    signature = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, default="valid", db_index=True)  # valid, tampered

    class Meta:
        verbose_name = "Audit Checkpoint"
        verbose_name_plural = "Audit Checkpoints"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Checkpoint {self.checkpoint_id} ({self.status}) - {self.event_count} events"


class SecurityIncident(models.Model):
    """
    Subsystem 12: Incident response, severity tracking, containment, and PIR.
    """

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        INVESTIGATING = "investigating", "Investigating"
        CONTAINED = "contained", "Contained"
        REMEDIATED = "remediated", "Remediated"
        CLOSED = "closed", "Closed"

    incident_id = models.CharField(max_length=64, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.HIGH)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INVESTIGATING)
    lead_responder = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_incidents",
    )
    description = models.TextField(blank=True, default="")
    containment_steps = models.TextField(blank=True, default="")
    recovery_steps = models.TextField(blank=True, default="")
    lessons_learned = models.TextField(blank=True, default="")
    evidence_references = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    contained_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Security Incident"
        verbose_name_plural = "Security Incidents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Incident [{self.incident_id}] {self.title} ({self.status})"


class AccessReviewCampaign(models.Model):
    """
    Subsystem 8: Quarterly access reviews and dormant-account detection.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"

    name = models.CharField(max_length=128)
    quarter = models.CharField(max_length=16, db_index=True)  # e.g. "2026-Q1"
    reviewer = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_reviews",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Access Review Campaign"
        verbose_name_plural = "Access Review Campaigns"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Campaign {self.name} [{self.quarter}] ({self.status})"


class AccessReviewItem(models.Model):
    """Individual account assessment within an Access Review Campaign."""

    class Decision(models.TextChoices):
        PENDING = "pending", "Pending Decision"
        CERTIFIED = "certified", "Access Certified"
        REVOKED = "revoked", "Access Revoked"
        FLAGGED_DORMANT = "flagged_dormant", "Flagged Dormant"

    campaign = models.ForeignKey(
        AccessReviewCampaign,
        on_delete=models.CASCADE,
        related_name="items",
    )
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="access_reviews",
    )
    decision = models.CharField(max_length=32, choices=Decision.choices, default=Decision.PENDING)
    reason = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_review_items",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Access Review Item"
        verbose_name_plural = "Access Review Items"
        unique_together = [["campaign", "user"]]

    def __str__(self):
        return f"Review for {self.user.employee_id} in {self.campaign.quarter}: {self.decision}"


class WorkspaceSecurityPolicy(models.Model):
    """
    Section 11: Workspace Security Administration Policy.
    Controls workspace-specific policies delegated from Central IAM.
    """

    policy_key = models.CharField(max_length=64, unique=True, db_index=True)
    policy_value = models.JSONField(default=dict)
    justification = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workspace_policy_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Workspace Security Policy"
        verbose_name_plural = "Workspace Security Policies"

    def __str__(self):
        return f"WorkspacePolicy: {self.policy_key}"


class BiometricAssertion(models.Model):
    """
    Cryptographic, transaction-bound biometric verification assertion.
    Replaces caller-supplied boolean flags (such as face_verified=True).
    Strictly bound to:
    - Employee identity (user)
    - Requesting device / IP / User-Agent (device_id)
    - Specific approval request or purpose reference (approval_request)
    - Active challenge nonce (challenge_nonce)
    - Single-use cryptographic token (single_use_nonce)
    - Verification outcome (outcome)
    - Strict expiration window (expires_at)
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="biometric_assertions",
        db_index=True,
    )
    device_id = models.CharField(max_length=128, blank=True, default="")
    approval_request = models.ForeignKey(
        "accounts.DeviceApprovalRequest",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="biometric_assertions",
    )
    purpose = models.CharField(max_length=64, default="device_approval")
    challenge_nonce = models.CharField(max_length=64)
    outcome = models.CharField(max_length=32, default="verified")  # verified, failed, rejected
    confidence_score = models.FloatField(default=0.0)
    liveness_score = models.FloatField(default=0.0)
    single_use_nonce = models.CharField(max_length=64, unique=True, db_index=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    is_consumed = models.BooleanField(default=False, db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Biometric Assertion"
        verbose_name_plural = "Biometric Assertions"
        ordering = ["-issued_at"]

    def __str__(self):
        return f"BiometricAssertion {self.id} for {self.user.employee_id} ({self.outcome}, consumed={self.is_consumed})"


class IAMDualApprovalRequest(models.Model):
    """
    Level 3 Independent Dual Approval Request (IAM Section 9).
    Enforces that high-assurance operations (break-glass, super admin promotion, key rotation)
    require requester Level 2 AND independent second administrator Level 2 step-up
    bound to the identical target, action, and business justification.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Second Approval"
        APPROVED = "approved", "Approved by Second Admin"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        CONSUMED = "consumed", "Consumed & Executed"

    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=128, db_index=True)
    reason = models.TextField()
    requester = models.ForeignKey(
        IAMAdminIdentity,
        on_delete=models.CASCADE,
        related_name="requested_dual_approvals",
    )
    requester_session = models.ForeignKey(
        IAMSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requester_dual_approvals",
    )
    requester_stepup_verified_at = models.DateTimeField()
    approver = models.ForeignKey(
        IAMAdminIdentity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_dual_approvals",
    )
    approver_session = models.ForeignKey(
        IAMSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approver_dual_approvals",
    )
    approver_stepup_verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    rejection_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "IAM Dual Approval Request"
        verbose_name_plural = "IAM Dual Approval Requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"DualApproval {self.request_id} [{self.action} on {self.target_id}] ({self.status})"

