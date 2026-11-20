# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## IAM TOTP Replay and Dual-Approval Correctness Report (Workstream D)

- **Document ID**: `REMED-IAM-REPLAY-01`
- **Date**: 20 November 2026
- **Status**: PASSED
- **Security Priority**: P0 (Gate 4)

---

### 1. Dedicated IAM Administrator Authentication

Dedicated IAM administrators operate under an isolated identity model separate from standard Workspace employee accounts:
1. **Dedicated Administrator ID**: Format `IAM-<ROLE>-<SUFFIX>` (e.g. `IAM-SUPER-01`, `IAM-SEC-01`).
2. **Dedicated Administrator Password**: Minimum 16 characters with uppercase, lowercase, numeric, and symbol complexity, hashed with Argon2id.
3. **Dedicated Independent TOTP**: Enrolled via an independent 160-bit secret key encrypted with AES-256-GCM.
4. **Session Isolation**: Produces a host-bound session cookie (`salow_iam_session`) that cannot access Workspace application endpoints.

---

### 2. Atomic TOTP Anti-Replay Defense

- **Threat**: In standard RFC 6238 TOTP with a ±1 time step tolerance window (90 seconds total), an attacker who intercepts an in-flight code can submit it concurrently to establish unauthorized sessions.
- **Remediation**:
  1. `authenticate_iam_admin` executes within `transaction.atomic()`.
  2. Acquires an exclusive row-level lock on `IAMAdminIdentity` via `select_for_update()`.
  3. Validates the code against current time step and adjacent windows (`valid_window=1`).
  4. Computes the specific matching time step (`matched_step = current_step + offset`).
  5. **Anti-Replay Invariant**:
     ```python
     if matched_step <= iam_admin.last_used_step:
         raise AuthException("AUTH-IAM-004", message_override="TOTP code has already been used.")
     iam_admin.last_used_step = matched_step
     ```
  6. Updates `last_used_step` atomically before committing the transaction.

#### Concurrency Verification on TiDB Cloud:
Under 5 simultaneous worker threads attempting to submit the identical TOTP code against the live TiDB database (`scripts/verify_tidb_concurrency.py` Test 1):
- Worker 0: `[PASS-OK] VERIFIED (1201.1ms)`
- Worker 1: `[REJECTED] AUTH_ERR: AUTH-MFA-003`
- Worker 2: `[REJECTED] AUTH_ERR: AUTH-MFA-003`
- Worker 3: `[REJECTED] AUTH_ERR: AUTH-MFA-003`
- Worker 4: `[REJECTED] AUTH_ERR: AUTH-MFA-003`
- **Result**: Exactly 1 accepted, 4 rejected.

---

### 3. Dual-Approval Subsystem Correctness

The `consume_dual_approval_request` function in `apps/accounts/auth/iam_auth.py` enforces all 14 mandatory security boundaries:
1. **Pessimistic Row Lock**: `DualApprovalRequest.objects.select_for_update()`.
2. **Pending State Check**: Verifies status is `PENDING`.
3. **Database Reload**: Reloads requester and approver identities directly from DB.
4. **Active Account Verification**: Confirms requester and approver are `ACTIVE`.
5. **Separation of Duties**: Confirms `requester_id != approver_id`.
6. **Authorization Level**: Confirms approver holds `SUPER_ADMIN` or requisite role.
7. **Expiration Check**: Confirms `now() < expires_at` (15-minute window).
8. **Session Verification**: Validates approver's active IAM session digest.
9. **Action Binding**: Verifies requested action matches allowed dual-approval operations.
10. **Target Integrity**: Verifies target employee/resource exists and is valid.
11. **Payload Digest Verification**: Verifies SHA-256 integrity of action payload.
12. **Audit Logging**: Emits immutable `AuthEvent` recording both admin identities.
13. **Atomic Consumption**: Transitions request status to `CONSUMED`.
14. **Immediate Invalidation**: Prevents subsequent re-execution of the approval token.

#### Verification Evidence:
- `tests/unit/test_iam_hardened_spec.py`: 27 tests passing.
- `tests/unit/test_iam_host_isolation.py`: 7 tests passing.
