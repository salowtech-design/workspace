# Salow Workspace — Architecture Specification

## 1. High-Level Architecture

Salow Workspace is an internal modular monolith built with Python and Django.

```
Browser (HTMX + Alpine.js + CSS Tokens)
   │  HTTPS / Same-Origin / CSRF
   ▼
Django Modular Monolith (Policy Enforcement Point)
├── apps/core/          (Policy, AuditLog, Outbox, Settings, Health)
├── apps/accounts/      (Custom User, Argon2id, Sessions, TOTP MFA)
├── apps/directory/     (Employees, Teams, Reporting hierarchy)
├── apps/face/          (Pure Python CPU pipeline, encrypted templates)
├── apps/attendance/    (Work sessions, manual exceptions, corrections)
├── apps/posts/         (Versioned templates, daily work posts, comments)
└── apps/certificates/  (Templates, A4 PDF generation, QR, public verify)
   │
   ├─► TiDB Cloud Starter (Distributed MySQL-compatible SQL endpoint)
   │     └─► TiKV Transactional Engine (Raft consensus)
   └─► Private Object Storage (Encrypted at rest)
```

## 2. Module Contract

Every Django application under `apps/<module>/` strictly adheres to this contract:

- `models.py`: Owned database entities and table definitions.
- `services.py`: The **only** supported interface for cross-module state-changing writes.
- `selectors.py`: Supported query and read interfaces.
- `policies.py`: Default-deny authorization predicates (`require(user, permission_code)`).
- `events.py`: Domain event definitions published to the outbox.
- `module.py`: Manifest declaring namespace, permissions, navigation, and settings schemas.
- `views/` & `forms.py`: Thin presentation handlers delegating to services/selectors.
- `tests/`: Module-specific unit and integration tests.

### Boundary Rules
1. No direct cross-module model writes. Module A must call `apps.B.services.some_action()`.
2. `apps/face/engine/` is pure Python: no Django, ORM, request, or template imports.
3. Views never perform raw database mutations directly; they validate forms and call services.

## 3. Trust Boundaries & Security Model

- **Browser**: Untrusted. Presentation and sensor capture only. All decisions (attendance, identity, permissions) are server-side.
- **Database**: Authoritative source of business truth. Uses TiDB constraints and concurrency-safe transactions.
- **Face Pipeline**: Sensitive in-memory boundary. Raw camera frames and embeddings are never written to disk, database, logs, or error reports. Templates are encrypted with AES-256-GCM before storage.
- **Public Endpoints**: Narrowly constrained anonymous routes (`/health`, `/verify/<token>/`). `/verify/` exposes only approved metadata with `X-Robots-Tag: noindex` and strict rate limiting.
