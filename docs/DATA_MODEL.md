# Salow Workspace — Data Model Specification

## Domain Models Overview

### 1. `apps.accounts`
- `CustomUser`:
  - `employee_id` (CharField, unique, immutable, primary login identifier).
  - `email` (EmailField, optional/unique).
  - `full_name` (CharField).
  - `role` (CharField: `super_admin`, `admin`, `manager`, `employee`, `intern`).
  - `status` (CharField: `invited`, `onboarding`, `active`, `suspended`, `exited`).
  - `attendance_mode` (CharField: `face_preferred`, `manual_only`).
  - Standard Django password hash, `is_active`, `is_staff`, `is_superuser`, `date_joined`.

### 2. `apps.core`
- `AuditLog`:
  - `id` (BigAutoField).
  - `actor` (ForeignKey to User, null=True for system actions).
  - `action` (CharField).
  - `target_type` (CharField).
  - `target_id` (CharField).
  - `payload_summary` (JSONField, redacted).
  - `timestamp` (DateTimeField, auto_now_add=True, UTC).
  - `request_id` (CharField).

### 3. `apps.attendance`
- `WorkSession`:
  - `id` (BigAutoField).
  - `user` (ForeignKey to CustomUser).
  - `work_date` (DateField, Asia/Kolkata date of check-in).
  - `check_in_at` (DateTimeField, UTC).
  - `check_out_at` (DateTimeField, null=True, UTC).
  - `verification_method` (CharField: `face`, `manual_approved`, `admin_entry`, `dev_stub`).
  - `state` (CharField: `open`, `completed`, `needs_review`).
