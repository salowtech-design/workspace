# Salow Workspace IAM — Browser & View Acceptance Report

**Document Version:** 2.0  
**Scope:** End-to-End User Journeys, Form Interactivity, View Redirections, and Browser Acceptance  
**Environment:** Django Test Client & Browser Emulation  
**Status:** **100% Verified**

---

## 1. Scope & Acceptance Criteria

Every visible user interface element and interactive screen specified in *Directive v2.0* was tested against actual backend state transitions. A feature was accepted only when:
1. Every visible form field and button submitted an implemented HTTP action.
2. Every successful screen transition represented a confirmed database change (e.g., password hash update, token invalidation, TOTP secret activation).
3. Anti-enumeration protections yielded identical response timing and neutral messaging across non-existent and existing accounts.
4. GET-safe token continuation pages prevented email crawlers or antivirus scanners from prematurely consuming single-use recovery links.

---

## 2. Journey-by-Journey Acceptance Evidence

### Flow A: First-Time Employee Activation Wizard
- **Route:** `/auth/activate/`
- **User Journey:**
  1. Employee navigates to `/auth/activate/` and enters their assigned Crockford Base32 activation code.
  2. System validates code against `ActivationCode`, checks attempt counters, and binds employee ID in session.
  3. Employee is redirected to `/auth/activate/password/` to set a compliant Argon2id password (enforcing minimum 12 characters, uppercase, lowercase, digit, special character, and rejection of employee name/ID).
  4. Employee is redirected to `/auth/activate/mfa/setup/`, where an inline SVG QR code is rendered directly (no external CDN dependency) alongside a base32 manual secret.
  5. Employee submits the 6-digit TOTP code to `/auth/activate/mfa/verify/`.
  6. On successful TOTP verification, employee is directed to `/auth/activate/recovery-codes/` displaying exactly 10 single-use recovery codes.
  7. Employee confirms saving codes; system establishes a fully verified server-side session and redirects to the role-specific workspace dashboard (`/attendance/panel`).
- **Acceptance Status:** **PASSED** (`test_iam_e2e_browser_acceptance.py::TestFlowAActivationWizard::test_complete_activation_flow`)

---

### Flow B: Returning Employee Sign-In (Single Screen)
- **Route:** `/auth/login/`
- **User Journey:**
  1. Employee navigates to `/auth/login/`. Passkeys and WebAuthn controls are strictly absent from the UI per Directive v2.0.
  2. Screen provides fields for Employee ID and Password, plus interactive MFA options.
  3. **Path B1 (TOTP):** Employee submits Employee ID + Password + 6-digit TOTP code. Backend validates credentials atomically and immediately issues a session cookie.
  4. **Path B2 (Recovery Code):** Employee submits Employee ID + Password + 12-character backup recovery code. Backend burns the recovery code, sets `consumed_at`, and issues a verified session cookie.
  5. **Invalid Credentials:** Generic error `"Invalid employee ID or password."` rendered identically whether the ID exists or not, preventing enumeration.
- **Acceptance Status:** **PASSED** (`test_iam_e2e_browser_acceptance.py::TestFlowBReturningEmployeeSignIn`)

---

### Flow C: Top-Level Password & Authenticator Recovery

The recovery interface (`/auth/recovery/forgot-password/`) exposes exactly three top-level methods:

#### Method 1: Request Administrator Reset
- **User Experience:**
  - Employee enters their Employee ID and optional reason note.
  - Form posts `action="request_admin_reset"`.
  - Neutral response rendered: `"If an eligible active account matches this Employee ID, a recovery request has been routed to the administrator queue."`
  - Recovery request record created in `RecoveryRequest` with status `PENDING`.
  - Transactional notification queued in `EmailOutbox` for administrators.
- **Administrator Approval Experience (`/auth/recovery/console/`):**
  - Requires `PERM_RECOVERY_DELEGATED` or `PERM_RECOVERY_ADMIN`.
  - Enforces active Sudo Mode (10-minute re-auth window).
  - Lists pending requests. If target is a `SUPER_ADMIN`, UI displays a mandatory second approver dropdown.
  - On approval (`/auth/recovery/console/<id>/approve/`), status transitions to `APPROVED`, an encrypted email is queued with a single-use reset link.
- **Acceptance Status:** **PASSED** (`test_iam_recovery_and_email.py::TestRecoveryMethod1AdminReset`)

#### Method 2: Reset Password Yourself
- **Dynamic Route Segregation:**
  - Route selection buttons for **Route A** (Recovery Code), **Route B** (TOTP), and **Route C** (Company Email Link).
  - Only the selected route's inputs are visible; password entry fields are strictly hidden until proof is validated.
- **Route A (Recovery Code):**
  - Submits Employee ID + 12-char backup code. Code is validated and burned.
  - Session elevated with temporary authorization (`_salow_pw_reset_user_id`) expiring in 15 minutes.
  - Redirected to `/auth/recovery/password/set/` to establish new password. Sessions revoked upon save.
- **Route B (TOTP):**
  - Submits Employee ID + 6-digit TOTP code.
  - Super Admin accounts are explicitly barred from Route B self-service (dual-control required).
  - Validated TOTP allows password update and session revocation.
- **Route C (Company Email Link):**
  - Submits Employee ID. If company email is verified, single-use token link is queued.
  - Email link points to `/auth/recovery/email/reset/?token=<raw_token>`.
  - **GET Request (Safe):** Renders continuation screen (`email_reset_continue.html`) without consuming token. Email scanners cannot burn it.
  - **POST Request (Redemption):** Consumes token, strips token from URL, redirects to password set screen.
- **Acceptance Status:** **PASSED** (`test_iam_recovery_and_email.py::TestRecoveryMethod2ResetYourself`)

#### Method 3: Recover Lost or Replaced Authenticator
- **User Journey:**
  - Submits Employee ID + current password to `/auth/recovery/forgot-password/?method=method3`.
  - System verifies current password and checks that `user.email_verified == True`.
  - Transactional email dispatched containing single-use verification link to `/auth/recovery/mfa/verify/?token=<raw_token>`.
  - **GET Request:** Safe landing page (`mfa_replacement_continue.html`).
  - **POST Request:** Consumes token and sets `_salow_mfa_replace_uid`.
  - Redirects to `/auth/recovery/mfa/setup/`: Displays new inline SVG QR code and manual secret.
  - User submits 6-digit TOTP code from new authenticator app.
  - Backend deletes old `TOTPCredential`, generates 10 fresh `RecoveryCode` records, revokes all existing device sessions, and redirects to `/auth/recovery/mfa/recovery-codes/`.
  - User views all 10 new codes, utilizes "Copy All Codes" or "Download as .txt", checks confirmation checkbox, and completes flow.
  - System establishes active session and redirects to role dashboard (`/attendance/panel`).
- **Acceptance Status:** **PASSED** (`test_iam_recovery_and_email.py::TestRecoveryMethod3MfaReplacement`)

---

## 3. Rate Limiting & Error Pages Acceptance

- **HTTP 429 Page:**
  - Exceeding sensitive POST burst threshold (5 requests / 60 seconds) produces a branded HTML 429 response (`core/429.html`) with a valid `Retry-After: 60` HTTP header.
  - Unmetered GET requests (e.g. login view rendering, health checks) are never blocked.
  - `python manage.py reset_ratelimit` command resets in-memory and database sliding window records.
- **Acceptance Status:** **PASSED** (`test_iam_e2e_browser_acceptance.py::TestRateLimitingSegregationAnd429`)

---

## 4. Idle Lock & Session Resilience Acceptance

- **Idle Lock Screen:**
  - Inactivity beyond 15 minutes triggers an idle lock state.
  - User can enter their current password on `/auth/security/idle-unlock/` to resume work without losing session state.
- **Workspace Draft Persistence:**
  - Draft state saved via AJAX/fetch to `/auth/security/draft/save/` is securely persisted in `WorkspaceDraft` and retrievable via `/auth/security/draft/get/` upon unlocking.
- **Acceptance Status:** **PASSED** (`test_iam_hardened_spec.py::TestSessionResilienceAndDrafts`)
