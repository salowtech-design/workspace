# Salow Workspace — IAM Release Forensic Audit Report

**Audit Target**: Claimed Release `iam-v1.0` (Commit `f3374d3`)  
**Product**: Salow Workspace  
**Organization**: Salow Tech  
**Audit Timestamp**: 2026-11-17T00:30:00+05:30  
**Auditor**: Senior IAM & Security Verification Engineer  
**Database Authority**: TiDB Cloud (MySQL-compatible with TiKV transactional storage)  

---

## 1. Executive Summary

This forensic audit evaluates the integrity, reproducibility, and physical state of the Salow Workspace repository following the claimed creation of release tag `iam-v1.0` at commit `f3374d3`.

All findings in this report are backed by raw git porcelain, rev-parse hashes, migration executor logs, and automated test outputs executed against the live workspace environment.

---

## 2. Release State Evidence Matrix

| Item | Verified State | Objective Evidence |
|---|---|---|
| **Current branch** | `main` | `git branch --show-current` $\rightarrow$ `main` |
| **Current HEAD** | `f3374d3` | `git rev-parse --short HEAD` $\rightarrow$ `f3374d3` |
| **Claimed commit exists** | **VERIFIED** | `git log -1 f3374d3` confirms commit exists with author Salow Tech |
| **Claimed tag exists** | **VERIFIED** | `git tag -l iam-v1.0` confirms `iam-v1.0` exists |
| **Tag points to claimed commit** | **VERIFIED** | `git rev-parse iam-v1.0` ($f3374d31683d884460a9345c90bbc2095fd22335$) matches `git rev-parse f3374d3` exactly |
| **Working tree clean** | **NOT CLEAN (MODIFIED)** | `git status --short` displays 18 uncommitted files containing post-commit security enhancements, sudo mode templates, and test extensions |
| **IAM source files present** | **VERIFIED** | All 44 core IAM files present (`apps/accounts/auth/passkeys.py`, `mfa_totp.py`, `reset.py`, `middleware.py`, etc.) |
| **Migration files present** | **VERIFIED** | `0005_passkeycredential_workspacedraft.py` and `0003_ratelimitrecord.py` exist and are applied |
| **Test files present** | **VERIFIED** | `tests/unit/test_iam_hardened_spec.py` present; 133 unit tests executed and passing |
| **Documentation complete** | **PARTIALLY RECONCILED** | 13 baseline documents authored; requires reconciliation of session terminology, database names, and capacity benchmark disclosures |

---

## 3. Detailed Forensic Inspection

### 3.1 Git History & Lineage
Commit lineage demonstrates a linear progression from Milestone M0 through M6 to the IAM hardening baseline:
```text
f3374d3 (HEAD -> main, tag: iam-v1.0) feat(iam): complete passkeys, delegated recovery, session security, distributed rate limiting, and 12-point diagnostics
17e0ec7 (tag: m6, tag: m5, tag: m4, tag: m3) feat(m3-m6): complete posts, certificates, security hardening, pilot ops, and activation counter fix
53293e2 (tag: m2) feat(m1-m2): complete auth spec, activation, mfa, frs service, and attendance lifecycle
5c88e4b feat(m2): implement pure python face engine, quality checks, challenge protocol, and aes-256-gcm template crypto
55e365e (tag: m1) feat(m1): implement identity, authorization engine, totp mfa, directory, and outbox
7629fa4 (tag: m0) feat(m0): establish foundation, auth skeleton, tidb integration, and attendance slice
```

### 3.2 Working Tree Modifications Analysis
The working tree contains 18 modified/untracked files. Inspection reveals these are intentional security additions:
1. **`apps/accounts/auth/stepup.py` & `permissions.py`**: Unified `grant_sudo` and `is_sudo_active` into a centralized step-up module, standardizing sudo timeout to 10 minutes (`SUDO_WINDOW_MINUTES = 10`).
2. **`templates/accounts/security/sudo.html`**: Added standalone password re-authentication modal for sensitive actions (e.g. revoking passkeys, administrative recovery).
3. **`apps/core/middleware.py`**: Upgraded rate limiting to multidimensional IP + Account (`acc:{employee_id}:sensitive`) tracking, plus automatic expired record pruning.
4. **`templates/accounts/recovery/forgot_password.html`**: Added passkey-based recovery option in addition to one-time recovery codes.
5. **`tests/unit/test_iam_hardened_spec.py`**: Added unit tests for step-up sudo mode, account-based rate limiting, and draft endpoints.

---

## 4. Audit Verdict
Release tag `iam-v1.0` is verified to point to `f3374d3`. The working tree contains legitimate, uncommitted hardening additions that must be verified, integrated, and documented through this directive.
