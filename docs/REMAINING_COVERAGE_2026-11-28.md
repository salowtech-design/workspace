# Remaining coverage, read from source on 28 Nov 2026

Source: Implementation Blueprint v2.0 (25 Nov) section 9 against apps/, templates/ and tests/ at this commit.
Not a readiness claim. Sheets and Slides keep their data on the device only (the user's storage rule), so
sharing, server storage and cross-user use of them are not built and not claimed.

The number of tour files does not equal current coverage: videos record the head they were made on, and many predate later changes.

## Built and browser-toured (local TiDB 8.5.1 unistore, emulated phone, one machine)
Chat (spaces, mentions, reactions, search, badges), Drive (folders, share, trash, move, device files),
Calendar (events, series, guests, time zones), Tasks and Projects, People directory, Attendance (dev bypass
only), Work Logs and Feed, Certificates, PDF viewer, Search (page and palette across Drive, Tasks, Calendar,
People, Work Logs, Chat), IAM and Admin consoles, Sheets and Slides (device-local).

## Unblocked engineering gaps
- Automation background runner: code and unit tests only. It is a management command, not a running service (no compose or deploy change). No UI proof, no multi-process race test, no real TiKV. Owners who are not active are never run, and old queued runs have no expiry (a policy question, left open).
- Automation schedule trigger and approval step: need a timezone and approver rule, so a product choice.
  Not started.

## Blocked on a decision that is not ours
- Cross-user file bytes in Drive and Chat (holder question), Automation creation roles, activation-state
  behaviour, break-glass, project-member add or remove, Chat member and retention rules, real face matching,
  Meet, Forms, Add-ons (not requested in detail), email and push delivery, production load and real TiKV.
