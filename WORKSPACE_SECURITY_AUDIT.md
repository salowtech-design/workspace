# WORKSPACE SECURITY & INTEGRATION AUDIT REPORT
**Project**: Salow Workspace & Identity Platform (IAM v6.0)  
**Organization**: Salow Tech  
**Baseline Commit**: `b520687` on `main`  
**Auditor**: Independent Forensic Investigation Specialist (Stage 0)  
**Deliverable**: Deliverable E — Detailed Workspace Security and Integration Assessment

---

## 1. Executive Summary

This audit assesses the security controls, boundary enforcement, and integration interfaces of the core Salow Workspace business modules (`attendance`, `posts`, `certificates`, `directory`) and their interaction with the central Identity and Access Management (IAM) authority.

### High-Level Findings
1. **Administrative Segregation**: **VERIFIED STRONG**. Workspace business administration (`/attendance/admin/`) and Central IAM (`/auth/admin/iam/`) maintain separate routes, permissions, and view boundaries.
2. **Permission Enforcement**: **VERIFIED STRONG**. Default-deny access control is enforced at both view and object levels via `apps/accounts/permissions.py`.
3. **Biometric UI Capture**: **PARTIALLY VERIFIED / FICTION**. Attendance check-in UI contains no `<video>` or `<canvas>` elements; backend check-in passes empty frames to a stub engine.
4. **Geolocation Enforcement**: **CONFLICTING**. `SecurityHeadersMiddleware` sets `Permissions-Policy: geolocation=()`, which instructs browsers to block all JavaScript geolocation requests.
5. **Client-Side Tab-Switch Redirect**: **ARCHITECTURAL RISK**. Tab switching for >30 seconds triggers client-side redirect to login, discarding unsaved form state despite valid 12-hour server sessions.

---

## 2. Module Authorization & Integration Boundaries

```
[ Central IAM Authority: apps/accounts/ ]
  │
  ├── Session Management (django_session, 12h absolute limit, rotation)
  ├── Permission Engine (apps/accounts/permissions.py)
  └── Sudo Step-Up Elevation (apps/accounts/auth/stepup.py)
        │
        ├──► [ apps/attendance/ ]   (Consumes user session & permissions)
        ├──► [ apps/posts/ ]        (Consumes user identity & team scoping)
        ├──► [ apps/certificates/ ] (Consumes admin permission & sudo mode)
        └──► [ apps/directory/ ]    (Consumes organizational roles)
```

### Module Verification Details

| Business Module | Consumed IAM Service | Authorization Enforcement | Audit Generation | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Attendance** (`apps/attendance`) | `request.user`, `UserSession` | Decorator `@require_permission("attendance.session.view_self")`, `@require_role` | Emits `attendance.check_in`, `attendance.correction` audit logs | **VERIFIED** |
| **Posts & Work Logs** (`apps/posts`) | `request.user`, Team Lead check | Decorator `@require_permission("posts.post.create")`, Team scoping | Auto-escapes user HTML, validates URLs | **VERIFIED** |
| **Certificates** (`apps/certificates`) | `request.user`, Sudo mode | Requires `is_sudo_active(request)` for issuance and revocation | Emits `cert.issued`, `cert.revoked`, logs verification requests | **VERIFIED** |
| **Directory** (`apps/directory`) | `request.user`, Department | Role-gated visibility; hierarchy validation prevents acyclic loops | Directory profile view auditing | **VERIFIED** |

---

## 3. Deep Dive into Workspace Security Mechanics

### 3.1 Biometric Attendance & Geolocation Constraints
- **Inspection of Check-In Interface** (`templates/attendance/panel.html:45-57`):
  - The UI presents a standard button labeled "Check In with Face".
  - The form contains only `{% csrf_token %}` and a submit button.
  - **No WebRTC camera access, `<video>` element, or client-side frame capture JavaScript exists in the template**.
- **Inspection of Check-In View** (`apps/attendance/views.py:60-81`):
  - When the form is submitted, `check_in_view` executes:
    ```python
    res = verifier.verify_face(
        user=request.user,
        frames=[],
        challenge_nonce="m0-dev-nonce",
    )
    ```
  - `frames` is hardcoded as an empty list `[]`, and `challenge_nonce` is hardcoded to `"m0-dev-nonce"`.
  - The check-in succeeds only because `StubVerifier(allowed=is_dev)` is instantiated with `is_dev=True` or `allowed=True`. If `is_dev=False`, `StubVerifier` rejects the verification with "Stub verification disabled in production".
- **Geolocation Contradiction**:
  - `apps/core/middleware.py:45` sets the HTTP header:
    `Permissions-Policy: camera=(self), microphone=(), geolocation=()`
  - The empty parenthesis `geolocation=()` tells all modern browsers to deny any JavaScript request to `navigator.geolocation.getCurrentPosition()`. Any future client-side geofencing will fail unless this header is amended.

### 3.2 Client-Side Resumption & Data Loss Risk
- **Source Code**: `static/session-resumption.js:5-18`.
- **Implementation**:
  ```javascript
  document.addEventListener('visibilitychange', function() {
      if (document.hidden) {
          hiddenTime = Date.now();
      } else {
          if (hiddenTime && (Date.now() - hiddenTime) > 30000) {
              window.location.href = '/auth/login/?flow=signin&next=' + encodeURIComponent(window.location.pathname);
          }
      }
  });
  ```
- **Finding**:
  - If an authenticated user minimizes the browser or switches to another tab for more than 30 seconds (e.g. while looking up notes for a daily post or attendance explanation), returning to the tab immediately redirects the browser to the login screen.
  - The user's server session in `django_session` remains completely valid (12-hour lifetime), but any unsaved text in input forms is discarded.
  - **Remediation Needed**: Implement client-side auto-save or an in-page modal re-authentication prompt that preserves form state.

### 3.3 Administrative Console Segregation
- **Workspace Admin (`/attendance/admin/`)**:
  - Enforces `@login_required` and role check for `SUPER_ADMIN`, `ADMIN`, `MANAGER`, or `HR`.
  - Restricts operations strictly to attendance correction review and manual fallback queue management.
  - Cannot access employee credentials, modify roles, or view encryption keys.
- **Central IAM Admin (`/auth/admin/iam/`)**:
  - Enforces `@login_required`, role check strictly for `SUPER_ADMIN` and `ADMIN`, and mandatory active sudo elevation (`is_sudo_active(request)`).
  - Segregates the 13 identity subsystems from day-to-day workspace operations.
- **Assessment**: **VERIFIED — FULLY SEGREGATED**.

### 3.4 PWA Offline Capabilities & Caching
- **Source Code**: `static/sw.js:3-36`.
- **Implementation**:
  ```javascript
  const PRECACHE_ASSETS = [
      '/static/css/tokens.css',
      '/static/css/components.css',
      '/static/img/logo.jpg'
  ];
  ```
- **Finding**:
  - Line 36 intercepts requests only if `url.includes('/static/')`.
  - The service worker caches static styles and images, but does NOT cache application HTML pages or dynamic APIs.
  - When offline, visiting `/attendance/` or `/posts/` produces standard browser network error pages.

---
*Deliverable E complete. Workspace security boundaries and operational realities thoroughly documented.*
