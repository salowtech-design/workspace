# Salow Workspace — Master Build & Verification Report

## 1. Initial Starting Condition

- **Date**: 2026-11-15
- **Local Workspace**: `C:\Users\Dr Arif\Workspace.SalowTech`
- **Initial Repository State**: Empty repository. No application code, no migrations, no dependencies installed.
- **Git Connection**: Initialized on branch `main` with remote:
  `origin https://github.com/salowtech-design/workspace.git`
- **Host Specifications**:
  - Operating System: Windows (x86_64)
  - Python: `3.14.7` (Host executable)
  - uv: `0.13.1`
  - Git: `2.55.0.windows.3`
  - Docker: Not installed (Direct TiDB Cloud connection architecture approved)
  - Memory: 3.98 GB Total / ~180 MB Free (Low-spec profile)
  - Disk: 41.16 GB Free on C:

---

## 2. Milestone Execution Log

### Work Package M0.1: Repository & Environment Initialization
- **Status**: COMPLETED
- **Requirement**: PDF Section 6 / Directive M0.1
- **Files Created**:
  - `.gitignore`: Comprehensive ignore rules for Python, virtualenvs, Django, secrets (`.env`), and caches.
  - `.env.example`: Safe configuration template with TiDB Cloud connection keys and security defaults.
  - `pyproject.toml`: Configured for Python 3.12, Django 5.2 (LTS), `django-tidb` 5.2, `mysqlclient`, Ruff, and pytest.
  - `.pre-commit-config.yaml`: Pre-commit hook definitions for code quality.
  - `README.md`: Quickstart, architecture rules, and testing guide.
  - `Makefile`: Standard developer automation commands.
  - `docs/`: Core architectural documentation (`ARCHITECTURE.md`, `DECISIONS.md`, `VERSIONS.md`, `LICENSES.md`, `BACKLOG.md`, `PERMISSIONS.md`).
- **Tests & Verification**:
  - `uv python pin 3.12` executed.
  - `uv sync --extra dev` executed to establish locked virtual environment.
  - `uv run ruff check .` passed with 0 errors.
  - `uv run ruff format .` passed with 0 errors.

### Work Package M0.2 & M0.3: Core Skeleton & Initial Model Migrations
- **Status**: COMPLETED (Local Schema & Migrations Ready for TiDB Cloud Apply)
- **Files Created / Configured**:
  - `manage.py`: Django entrypoint with environment loader.
  - `config/settings/`: Split settings (`base.py`, `dev.py`, `test.py`, `prod.py`).
  - `config/urls.py`: URL dispatch with `/health`, `/ready`, `/auth/`, `/attendance/`, `/styleguide/`.
  - `apps/core/`: `AuditLog` model (immutable, append-only, overrides `.save()` and `.delete()`), `record_audit_event()` with secret/biometric redaction, `/health` (process liveness), `/ready` (database probe).
  - `apps/accounts/`: `CustomUser` model (immutable `employee_id`, Argon2id hashing, role/status choices), `LoginForm` with dummy hash timing-attack protection, `login_user` with session rotation, `logout_user` (POST-only).
  - `apps/attendance/`: `WorkSession` model with `check_out_after_check_in` constraint, `attendance.services.check_in` and `check_out` with atomic transactions, `select_for_update()` concurrency guards, Asia/Kolkata business date calculation.
  - `apps/face/engine/`: Pure Python `FaceVerifier` protocol, `VerificationResult` dataclass, and `StubVerifier` (strictly gated to `DEV_VERIFIER=True`).
  - `apps/accounts/management/commands/setup_initial_data.py`: Administrative seeding command for initial Super Admin and Demo Employee.
  - `scripts/probe_tidb.py`: Automated connection probe script verifying TLS handshake, TiDB version, TiKV ACID transactions, rollback, and unique constraints.
  - `migrations/`:
    - `apps/accounts/migrations/0001_initial.py`
    - `apps/attendance/migrations/0001_initial.py`
    - `apps/core/migrations/0001_initial.py`
- **Tests & Verification**:
  - `uv run pytest tests/unit -v`: 11 passed (100% pass rate).
  - `uv run pip-audit`: 0 known vulnerabilities against Django 5.2.19 (LTS).

### Work Package M1: Authentication, Activation, MFA & Directory
- **Status**: COMPLETED & VERIFIED
- **Requirement**: "Salowtech Workspace: Authentication Design and Build Instructions" (v1.0) & Phase 1 Baseline
- **Subsystems Implemented**:
  - **Employee Activation**: Crockford Base32 10-char codes (`XXXXX-XXXXX`, keyed HMAC-SHA256, 48h TTL, 5-attempt lock, single-live constraint for TiDB).
  - **Password Security Policy**: Min 12 chars, Argon2id hasher, common dictionary blacklist, employee ID and name rejection.
  - **MFA TOTP**: Pure Python SVG QR generator (`SvgPathImage`), AES-256-GCM encrypted secrets at rest (`k1` key ring), anti-replay step tracker.
  - **Recovery Codes**: 10 single-use codes (keyed HMAC-SHA256, atomic burn on redemption).
  - **Lockout Protection**: Database-backed `LoginAttempt` with 15-minute cool-off and thread/process concurrency safety.
  - **Admin Bootstrap**: `python manage.py bootstrap_admin` (reads password via `getpass`, no cleartext printing in logs/chat).
  - **System Diagnostics**: `python manage.py auth_doctor` (8-point automated sanity check across all crypto, DB, and auth subsystems).
  - **Directory**: Departments, teams, reporting relationships, hierarchy validation, privacy search.
- **Migrations**:
  - `apps/accounts/migrations/0003_authevent_loginattempt_remove_usertotpdevice_user_and_more.py`
  - `apps/accounts/migrations/0004_alter_customuser_status.py`
  - `apps/directory/migrations/0001_initial.py`
- **Verification**:
  - `python manage.py auth_doctor --settings=config.settings.test`: ALL 8 SUBSYSTEMS PASSED.
  - `tests/unit/test_auth_spec.py`: 12 test cases, 100% pass rate.
  - `tests/unit/test_directory.py`: 5 test cases, 100% pass rate.

### Work Package M2: Face Recognition System & Attendance Lifecycle
- **Status**: COMPLETED & VERIFIED
- **Requirement**: Section 5 & 8 of Authentication Specification & Attendance Architecture
- **Subsystems Implemented**:
  - **Biometric Notice & Consent**: Informed consent audit logging (`BiometricNoticeAcknowledgement`) mandatory before template enrollment.
  - **Face Template Encryption**: AES-256-GCM encryption of 128-dimensional embedding vectors at rest; raw images/vectors NEVER saved.
  - **Admin-Assisted Enrollment**: Restricts template enrollment strictly to administrators with vector validation.
  - **FRS Service Boundary**: Standard interface `verify_face(user, purpose)` supporting attendance check-in, check-out, enrollment, and step-up (disabled by default with fallback to authenticator code).
  - **Attendance Lifecycle**: WorkSession tracking with Asia/Kolkata business date calculation, atomic concurrency guards (`select_for_update`).
  - **Manual Fallback Exception Queue**: Employee fallback request creation upon biometric failure; manager/admin approval workflow automatically generating `MANUAL_APPROVED` sessions.
  - **Attendance Corrections**: Manager/admin session timestamp correction preserving original timestamps in `AttendanceCorrection` audit log.
  - **Geolocation & Network**: Haversine distance geofencing and workplace subnet validation.
  - **Reporting**: Aggregated attendance reports (total sessions, completed, total hours worked, manual exceptions).
- **Migrations**:
  - `apps/face/migrations/0002_biometricnoticeacknowledgement_facetemplate_and_more.py`
  - `apps/attendance/migrations/0002_attendancecorrection_attendancefallbackrequest.py`
- **Verification**:
  - `tests/unit/test_face_spec.py`: 8 test cases, 100% pass rate.
  - `tests/unit/test_attendance_spec.py`: 15 test cases, 100% pass rate.
  - `tests/unit/test_face_engine.py`: 5 test cases, 100% pass rate.

---

## 3. Comprehensive Verification Summary

- **Total Test Cases**: 67 unit & integration tests
- **Pass Rate**: 100% (67 passed in 17.6s)
- **Code Quality**:
  - `ruff check .`: All checks passed (0 errors, 0 warnings).
  - `ruff format --check .`: 102 files cleanly formatted.
- **Diagnostics Check**:
  - `python manage.py auth_doctor --settings=config.settings.test`:
    - [PASS] Database Connection
    - [PASS] Database Migrations
    - [PASS] Session Storage
    - [PASS] AES-256-GCM Key Ring
    - [PASS] Server Clock
    - [PASS] Password Hashing (Argon2id)
    - [PASS] TOTP & SVG QR Generator
    - [PASS] Lockout Database Counters
