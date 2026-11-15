# Architecture Decision Records (ADRs)

## ADR-0001: Architecture Baseline and Superseded Specifications
- **Date**: 2026-11-15
- **Status**: Approved
- **Context**: Earlier drafts of Phase 1 referenced Keycloak for authentication and PostgreSQL for relational persistence.
- **Decision**:
  1. Keycloak is strictly prohibited. Salow Tech builds, owns, and operates first-party authentication within `apps/accounts/` using Django primitives, Argon2id hashing, and RFC 6238 TOTP.
  2. PostgreSQL is replaced with TiDB Cloud Starter (MySQL wire protocol backed by TiKV transactional storage).
  3. No PostgreSQL server, MySQL server, or SQLite database will be used for application integration tests.
  4. Hosting provider selection remains undecided; deployment assets must remain portable.

---

## ADR-0002: TiDB Cloud Starter & Driver Integration
- **Date**: 2026-11-15
- **Status**: Approved
- **Context**: TiDB Cloud Starter enforces TLS 1.2/1.3 and uses distributed Snapshot Isolation (SI). PostgreSQL-specific constructs (`JSONB`, `SKIP LOCKED`, partial indexes, advisory locks) are unsupported.
- **Decision**:
  1. Use PingCAP's official `django-tidb` database backend paired with the native `mysqlclient` C driver.
  2. Enforce TLS verification via `OPTIONS['ssl'] = {'ca': ...}` and `ssl_mode = 'VERIFY_IDENTITY'`.
  3. Enforce transactional invariants and concurrency safety at the service layer using explicit transactions compatible with TiDB's SI semantics.

---

## ADR-0003: Python 3.12 Runtime Pinning via uv
- **Date**: 2026-11-15
- **Status**: Approved
- **Context**: The host machine has Python 3.14.7 installed. However, essential native C-extension libraries required by the project (`mysqlclient`, `opencv-python`, `onnxruntime`) do not provide pre-compiled binary wheels for Windows on Python 3.14, which would require a complex local Visual C++ build environment.
- **Decision**:
  1. Pin Python 3.12 in `pyproject.toml` (`requires-python = ">=3.12,<3.13"`).
  2. Use `uv python pin 3.12` to allow `uv` to automatically download and manage an isolated Python 3.12 runtime.
  3. All local development, linting, tests, and database probes execute under Python 3.12.
