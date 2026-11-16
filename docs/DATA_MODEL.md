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
- `ActivationCode`:
  - One-time Crockford Base32 10-char code, keyed HMAC-SHA256, 48h TTL, 5-attempt lock, single-live constraint.
- `TOTPCredential`:
  - AES-256-GCM encrypted secret at rest, step tracker anti-replay.
- `RecoveryCode`:
  - Keyed HMAC-SHA256 hashed recovery codes.
- `LoginAttempt`:
  - Concurrency-safe lockout counters.
- `UserSession`:
  - Server-side session tracking with MFA state.

### 2. `apps.core`
- `AuditLog`:
  - Immutable, append-only security and business audit trail with recursive secret redaction.
- `OutboxEvent`:
  - Transactional domain event store with at-least-once publishing guarantee.
- `WorkspaceSetting` & `FeatureFlag`:
  - Dynamic configuration and role-gated flags.

### 3. `apps.directory`
- `Department`: Organizational business unit.
- `Team`: Team within department; enforces at most one lead per team.
- `TeamMembership`: M:N employee membership within a team.
- `ReportingRelationship`: Acyclic direct reporting tree preventing self-management.
- `EmployeeProfile`: Contact and public directory information.

### 4. `apps.attendance`
- `WorkSession`:
  - Concurrency-safe work session with check-in/out timestamps and Asia/Kolkata business date.
- `AttendanceCorrection`:
  - Audit trail of manager/admin timestamp overrides.
- `AttendanceFallbackRequest`:
  - Manual approval queue for biometric failure fallback.

### 5. `apps.posts` (Milestone M3)
- `PostTemplate`: Pre-configured daily update and standup formats.
- `Post`:
  - Daily standup, blocker, milestone, general, and weekly summary logs.
  - Team scoping, manager review sign-off (`approved`, `flagged`), and notes.
- `PostComment`:
  - Discussion and feedback thread attached to posts.

### 6. `apps.certificates` (Milestone M4)
- `CertificateTemplate`:
  - Standard recognition templates (internship completion, excellence, appreciation).
- `Certificate`:
  - Official credential with serial number (`ST-CERT-YYYY-XXXXXX`), UUID, and cryptographic tamper-evident SHA-256 hash.
  - Revocation tracking (`is_revoked`, `revoked_at`, `revoked_by`, `revocation_reason`).
- `CertificateVerificationLog`:
  - Public lookup audit log tracking IP and user agent.
