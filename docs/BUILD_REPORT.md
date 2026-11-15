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
