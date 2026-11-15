# Salow Workspace

**Salow Workspace** is the internal company operating system developed by and for **Salow Tech**. It provides internal identity, face-verified attendance, daily work logging, and certificate management for employees, interns, managers, and administrators.

---

## Architecture Principles

1. **Modular Monolith**: Clean Django applications (`apps/core/`, `apps/accounts/`, `apps/directory/`, `apps/attendance/`, `apps/face/`, `apps/posts/`, `apps/certificates/`) with explicit service and selector boundaries.
2. **First-Party Authentication**: Salow-owned credentials, sessions, and MFA. Keycloak and external identity SaaS are strictly prohibited.
3. **TiDB Cloud Storage**: MySQL-compatible distributed SQL database backed by TiKV transactional storage.
4. **Pure Python Face Engine**: In-memory CPU inference pipeline (OpenCV YuNet + SFace + MiniFASNet) with AES-256-GCM encrypted templates.
5. **Modern Server-Driven UI**: Django Templates + HTMX + Alpine.js + hand-written CSS design tokens. No React, no complex Node.js build pipeline.

---

## Prerequisites

- **uv** (v0.10+): Fast Python package and environment manager.
- **Python 3.12**: Managed automatically via `uv`.
- **Git**: Version 2.40+.
- **TiDB Cloud Starter Instance**: Database `salow_workspace` provisioned.

---

## Quickstart

### 1. Environment Setup
```bash
# Clone and enter directory
cd Workspace.SalowTech

# Pin Python 3.12
uv python pin 3.12

# Install dependencies into virtual environment
uv sync --extra dev
```

### 2. Configure Environment Secrets
Copy the template and configure your connection details:
```bash
cp .env.example .env
```
Edit `.env` and fill in your `TIDB_HOST`, `TIDB_USER`, and `TIDB_PASSWORD`.

### 3. Verification & Checks
```bash
# Code formatting and linting
uv run ruff check .
uv run ruff format --check .

# Run test suite
uv run pytest
```

---

## Repository Contract

- `config/`: Split Django settings (`base`, `dev`, `test`, `prod`), URL routing, WSGI/ASGI handlers.
- `apps/`: Business modules following the service/selector/policy/model contract.
- `docs/`: System documentation, architecture decision records (ADRs), permissions, and build reports.
- `tests/`: Multi-tiered test pyramid (`unit`, `integration`, `security`, `e2e`).
