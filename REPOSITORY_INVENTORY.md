# REPOSITORY INVENTORY & FILE STRUCTURE FORENSIC REPORT
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable A — Complete Repository, Directory and File Inventory

---

## 1. Repository Identity & Working Environment

- **Current Working Directory**: `c:\Users\Dr Arif\Workspace.SalowTech`
- **Host Environment**: Windows 11 Enterprise (x86_64, Windows PowerShell)
- **Runtime Environment**: Python `3.12.15` in virtualenv at `.venv`, managed via Astral `uv`
- **Git Repository Root**: `c:\Users\Dr Arif\Workspace.SalowTech`
- **Remote Origin URL**: `https://github.com/salowtech-design/workspace.git`
- **Current Branch**: `main`
- **Current HEAD Commit**: `b520687` (`docs: finalize master build and verification report for Phase 1 + IAM v6.0`)
- **Parent Commit**: `7289a38` (`feat(phase1): complete IAM syntax validation, second-device biometrics, secops alerts, and session resumption`)
- **Git Submodules & Nested Repositories**: **NONE** (confirmed via `git submodule status` and recursive `.git` directory search).
- **Working Tree State**: Clean relative to source files; contains 10 untracked Stage 0 deliverable markdown reports in repository root.

---

## 2. Quantitative File Inventory

| Category | File Count | Primary Extensions | Active Role |
| :--- | :---: | :--- | :--- |
| **Tracked in Git** | **282** | `.py`, `.html`, `.md`, `.js`, `.css`, `.yaml`, `.toml`, `.json` | Full application codebase |
| — Python Source | 170 | `.py` | Models, views, services, middleware, migrations, tests |
| — HTML Templates | 50 | `.html` | Server-rendered UI views and sub-components |
| — Markdown Documentation | 42 | `.md` | Architectural specs (32 in `docs/`, 10 in root) |
| — Client JavaScript | 4 | `.js` | `session-resumption.js`, `iam-monitor.js`, `sw-register.js`, `sw.js` |
| — Hand-Crafted CSS | 2 | `.css` | `static/css/tokens.css`, `static/css/components.css` |
| — CI/CD & Automation | 3 | `.yaml`, `.yml` | `.github/workflows/ci.yaml`, `scripts/ci.yaml` |
| — Build & Dependencies | 2 | `.toml`, `.lock` | `pyproject.toml`, `uv.lock` |
| — Images & Manifests | 2 | `.jpg`, `.json` | `static/img/logo.jpg`, `static/manifest.json` |
| — Test Results | 1 | `.xml` | `junit-report.xml` |
| — Config & Git | 6 | `.gitignore`, etc. | `.gitignore`, `.python-version`, `.env.example`, `Makefile` |
| **Untracked / Local Disk** | **281** | `.pyc`, `.sqlite3`, `.env`, `.coverage` | Local build artifacts and environment files |
| **Total Physical Files** | **563** | All | Total on disk excluding `.git/` and `.venv/` |

---

## 3. Directory Structure & Module Inventory

```
c:\Users\Dr Arif\Workspace.SalowTech
├── .github/workflows/         # Continuous integration pipelines
│   └── ci.yaml                # Automated test runner and quality checks
├── apps/                      # Django Modular Monolith applications
│   ├── accounts/              # Central IAM, authentication, recovery, RBAC, sessions
│   │   ├── auth/              # Cryptographic core, lockout, MFA, employee ID parser
│   │   ├── migrations/        # Migrations 0001_initial through 0008
│   │   ├── views/             # Authentication, activation, device approval, SecOps
│   │   └── models.py          # CustomUser, TOTPCredential, RecoveryCode, DeviceApprovalRequest
│   ├── attendance/            # Work sessions, check-in/out, manual fallbacks
│   │   ├── migrations/        # 0001_initial, 0002_add_attendance_correction
│   │   ├── views.py           # Work session check-in/out, correction admin
│   │   └── models.py          # WorkSession, AttendanceCorrection, AttendanceFallbackRequest
│   ├── certificates/          # Achievement credentials, HMAC-SHA256 signatures, public portal
│   │   ├── migrations/        # 0001_initial
│   │   ├── views.py           # Certificate issuance, PDF rendering, verification
│   │   └── models.py          # Certificate, CertificateTemplate, VerificationLog
│   ├── core/                  # Shared foundations, audit logging, outbox, rate limiting
│   │   ├── management/        # CLI management commands (process_email_outbox, auth_doctor)
│   │   ├── migrations/        # 0001 through 0004
│   │   ├── middleware.py      # SecurityHeadersMiddleware, RateLimitMiddleware
│   │   ├── outbox.py          # Transactional domain event outbox
│   │   ├── workday.py         # Asia/Kolkata business date calculation utilities
│   │   └── models.py          # AuditLog, OutboxEvent, EmailOutbox, WorkspaceDraft
│   ├── directory/             # Organizational hierarchy, departments, teams, profiles
│   │   ├── migrations/        # 0001_initial
│   │   └── models.py          # Department, Team, TeamMembership, ReportingRelationship
│   ├── face/                  # Biometric face verification protocol and encrypted templates
│   │   ├── engine/            # Pure Python face engine boundary (crypto, protocol, stub)
│   │   ├── migrations/        # 0001_initial, 0002_add_verification_log
│   │   └── models.py          # FaceTemplate, FaceVerificationLog, ConsentAcknowledgement
│   └── posts/                 # Daily work logs, standups, blockers, discussion feeds
│       ├── migrations/        # 0001_initial
│       └── models.py          # Post, PostTemplate, PostComment
├── config/                    # Django configuration package
│   ├── settings/              # Multi-environment configuration
│   │   ├── base.py            # Shared settings, installed apps, middleware, password validators
│   │   ├── dev.py             # Local development settings
│   │   ├── prod.py            # Production settings (hardened cookies, CSP, TiDB Cloud)
│   │   └── test.py            # Automated test configuration (SQLite in-memory)
│   ├── urls.py                # Root URL dispatcher
│   ├── asgi.py                # ASGI entry point
│   └── wsgi.py                # WSGI entry point (Gunicorn)
├── docs/                      # Authoritative specifications and architectural manuals
│   ├── ARCHITECTURE.md        # Monolith architecture and boundary rules
│   ├── DATA_MODEL.md          # Database entity schema and relationships
│   ├── PERMISSIONS.md         # Matrix of default-deny permissions
│   ├── SECURITY_OPERATIONS.md # Incident handling, audit policies, SecOps
│   ├── SESSION_SECURITY.md    # Session lifecycle and resumption rules
│   └── RUNBOOK.md             # Operational procedures and backup recovery
├── scripts/                   # Verification and benchmark automation
│   ├── load_test_1000_users.py# 1,000-user capacity simulation script
│   ├── verify_e2e_journeys.py # 7-step user journey verification
│   ├── verify_http_endpoints.py# HTTP header and forensic view verification
│   └── verify_tidb_concurrency.py# Distributed TiDB concurrency and locking test
├── static/                    # Hand-crafted static assets
│   ├── css/                   # tokens.css (#AD8B73 / #FFFBE9), components.css
│   ├── js/                    # session-resumption.js, iam-monitor.js, sw.js
│   ├── img/                   # logo.jpg
│   └── manifest.json          # PWA web app manifest
├── templates/                 # Hand-crafted server-rendered templates (50 files)
│   ├── base.html              # Base layout with navigation and security headers
│   ├── accounts/              # Login, activation, MFA, device approval, IAM dashboard
│   ├── attendance/            # Attendance check-in panel, admin review
│   ├── certificates/          # Certificate issue, SVG preview, verification
│   └── posts/                 # Work log feeds and submission forms
└── tests/                     # Automated test suites (243 tests collected)
    ├── security/              # Penetration tests, OWASP top 10, security headers
    └── unit/                  # Unit and subsystem integration tests (23 files)
```

---

## 4. Source File Classification & Status Assessment

| Relative Path | Type | Actively Referenced? | Implementation Status | Security / Architectural Relevance |
| :--- | :---: | :---: | :---: | :--- |
| `apps/accounts/auth/employee_id.py` | Python | Yes | **VERIFIED** | Section 6 employee ID syntax parser (`name-role-number`); validates role marker against database. |
| `apps/accounts/auth/crypto.py` | Python | Yes | **VERIFIED** | AES-256-GCM authenticated encryption/decryption with key identifiers. |
| `apps/accounts/auth/mfa_totp.py` | Python | Yes | **VERIFIED** | RFC 6238 TOTP verification, replay prevention (`last_used_step`), clock skew absorption. |
| `apps/accounts/auth/stepup.py` | Python | Yes | **VERIFIED** | Sudo mode elevation; stores active timestamp in `request.session`. |
| `apps/accounts/auth/device_approval.py`| Python | Yes | **VERIFIED** | Secondary device workflow, 60s request expiration, email OTP challenge. |
| `apps/accounts/auth/lockout.py` | Python | Yes | **VERIFIED** | Account-level attempt counters (5 for activation, 3 for returning sign-in). |
| `apps/accounts/views/admin_iam.py` | Python | Yes | **VERIFIED** | Central IAM 13-tile management grid, live event stream generator, security shield. |
| `apps/attendance/views.py` | Python | Yes | **PARTIALLY VERIFIED**| Check-in/out logic works, but check-in relies on `StubVerifier` with hardcoded dev frames. |
| `apps/core/middleware.py` | Python | Yes | **PARTIALLY VERIFIED**| Enforces CSP/HSTS and sliding rate limits; leftmost IP parsing is vulnerable to spoofing. |
| `apps/core/workday.py` | Python | Yes | **VERIFIED** | Asia/Kolkata business date calculation; preserves historical records across midnight. |
| `static/session-resumption.js` | JS | Yes | **VERIFIED** | Client visibility listener; triggers re-authentication if window is hidden > 30 seconds. |
| `static/sw.js` | JS | Yes | **PARTIALLY VERIFIED**| Service worker precaches CSS/logo; does NOT cache HTML routes or provide offline fallback. |

---
*Deliverable A complete. All 282 tracked files and 563 physical files inventoried with zero modifications to source files.*
