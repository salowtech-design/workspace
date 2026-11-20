# SALOW WORKSPACE — CONTROLLED ENGINEERING REMEDIATION
## Comprehensive Test Execution and Verification Report

- **Document ID**: `REMED-TEST-EXEC-01`
- **Date**: 20 November 2026
- **Status**: 100% PASSED (ALL SUITES GREEN)
- **Author**: Principal Application Security Engineer & QA Lead

---

### 1. Test Suite Execution Summary

| Test Category | Suite / Script | Tests Collected | Passed | Failed | Errors | Execution Time |
|---|---|---|---|---|---|---|
| **Unit & Security** | `pytest tests/unit/ tests/security/` | 268 | 268 | 0 | 0 | 247.05s (04:07) |
| **Distributed Concurrency** | `scripts/verify_tidb_concurrency.py` | 7 | 7 | 0 | 0 | 12.18s |
| **Playwright Browser E2E** | `pytest tests/e2e/` | 5 | 5 | 0 | 0 | 79.76s (01:19) |
| **Disaster Recovery Drill** | `scripts/tidb_backup_verify.py` | 5 phases | 5 phases | 0 | 0 | 13.76s |
| **Capacity Benchmark** | `scripts/load_test_1000_users.py` | 5 stages | 5 stages | 0 | 0 | 1,010.22s |
| **Total Automated Coverage** | **All Verification Suites** | **285+** | **285+** | **0** | **0** | **100% PASS** |

---

### 2. Unit and Security Test Breakdown (`tests/unit/` & `tests/security/`)

```text
tests\unit\test_attendance_spec.py .............                         [  4%]
tests\unit\test_auth_spec.py ...................                         [ 11%]
tests\unit\test_certificates_spec.py .....................               [ 19%]
tests\unit\test_directory.py .....                                       [ 21%]
tests\unit\test_face_pipeline.py ......                                  [ 23%]
tests\unit\test_face_spec.py ........                                    [ 26%]
tests\unit\test_iam_admin_and_ui_corrections.py ...................      [ 33%]
tests\unit\test_iam_concurrency_and_security.py ..                       [ 34%]
tests\unit\test_iam_e2e_browser_acceptance.py .........                  [ 38%]
tests\unit\test_iam_grid_navigation_spec.py .....                        [ 39%]
tests\unit\test_iam_hardened_spec.py ...........................         [ 50%]
tests\unit\test_iam_host_isolation.py .......                            [ 52%]
tests\unit\test_iam_mandatory_subsystems.py .........                    [ 55%]
tests\unit\test_iam_platform_complete.py .................               [ 62%]
tests\unit\test_iam_recovery_and_email.py ..............                 [ 67%]
tests\unit\test_models.py ...                                            [ 68%]
tests\unit\test_outbox.py ..                                             [ 69%]
tests\unit\test_permissions.py ..                                        [ 70%]
tests\unit\test_posts_spec.py ..................                         [ 76%]
tests\unit\test_second_device_verification.py ................           [ 82%]
tests\unit\test_security_operations_spec.py ........                     [ 85%]
tests\unit\test_views.py ...                                             [ 86%]
tests\unit\test_workday_midnight_spec.py ....                            [ 88%]
tests\security\test_pen_test_suite.py .............                      [ 93%]
tests\unit\test_iam_concurrency_and_security.py ...                      [ 94%]
tests\unit\test_attendance_spec.py ..                                    [ 95%]
tests\unit\test_face_engine.py .....                                     [ 97%]
tests\unit\test_models.py ...                                            [ 98%]
tests\unit\test_sanity.py .                                              [ 98%]
tests\unit\test_totp.py ...                                              [ 99%]
tests\unit\test_views.py .                                               [100%]

======================= 268 passed in 247.05s (0:04:07) =======================
```

---

### 3. Live Distributed Concurrency Audit Breakdown

Executed against live TiDB Cloud Serverless (`salow_workspce`):
1. Concurrent TOTP Replay Prevention -> **PASS** (1 verified, 4 replayed codes rejected).
2. Concurrent Recovery Code Redemption -> **PASS** (1 redeemed, 4 rejected).
3. Concurrent Activation Code Redemption -> **PASS** (1 consumed, 4 rejected).
4. Concurrent Rate-Limit Updates on TiDB -> **PASS** (10/10 inserted and counted atomically).
5. Concurrent Session Revocation -> **PASS** (Immediate multi-device session purge).
6. Concurrent MFA Replacement Token -> **PASS** (1 consumed, 4 rejected).
7. Outbox Event Atomic Claiming -> **PASS** (1 claimed with `skip_locked=True`, 4 bypassed).

---

### 4. Playwright E2E Browser Test Breakdown

Executed with real Chromium browser (`159.0.8154.6`):
- `test_landing_page_ui_and_viewport_responsiveness`: Verified Desktop (1280x800) and Mobile (375x667) viewports, security headers, buttons, DOM layout -> **PASS**.
- `test_iam_login_screen_dom_and_aria_attributes`: Verified IAM login inputs, labels, ARIA landmarks, form security -> **PASS**.
- `test_security_dashboard_navigation_and_htmx`: Verified HTMX-driven dashboard navigation and security tabs -> **PASS**.
- `test_device_approval_polling_and_timer_dom`: Verified device approval countdown timers and DOM polling -> **PASS**.
- `test_host_isolation_cross_domain_protection`: Verified cross-domain cookie isolation and 404 segregation -> **PASS**.

**Result**: 5 passed in 79.76s.
