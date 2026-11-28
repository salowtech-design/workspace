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

## Fresh proof at head 3fdb229 and d608151 (28 Nov 2026, 14:20 to 14:38 IST)
One run each, headless Chromium 390x844 emulated, TiDB 8.5.1 unistore, one machine, frames watched by the executor:
Chat main (17 steps), Chat mentions (5), Chat search (9), Calendar main (14), Tasks lists (17), Tasks projects (8),
Drive main (15). All 0 failed, no page errors, no outside requests. Closing captions of Chat, Drive, Projects and
Calendar series said "not built" for things that exist (for Calendar only the guest notice; un-cancel and single-occurrence edit are still not built); fixed in tests/browser/tours.py (Drive and Projects clips were
corrected by overlay on the existing recording, not re-recorded).

Existing tours that were run on earlier heads and not re-run at this head: project_restore, project_rename,
milestone_edit, and the rest of the older tours. Not assumed to pass now.

## Not yet re-recorded at this head (existing tours, no new feature)
Sheets cells and tools, Slides, PDF, Attendance and review, Work Logs and Feed, Certificates, Directory, Automation
pages, IAM and Admin consoles. Newer Sheets and Slides tools (dedup, trim, case, blank rows, two-key sort, stack,
tab move) were delivered earlier on 28 Nov with their own proof.

## Items listed as blocked, with what is known
These are decisions or qualifications recorded as open in memory, not research results. The standing sources are
the user's storage rule (24 and 25 Nov), the open questions in the workstream file, and the 8133 qualification note.
Two-device and real-device testing and real TiKV or Cloud qualification are not shown to be a user choice: they
need research of the original scope before being called blocked.

## Update, 28 Nov 2026 14:55 IST (one run each, same environment as above, executor-watched)
Re-recorded on 28 Nov, with these recorded code heads (the later commits touch only tour captions, tour assertions and this doc, not these modules): PDF (15 steps, run at 9479ea3), Attendance (9, bbda1d0), Certificates (19, ca0c620; employee refusal asserts HTTP 403, the styled page and no issue form), Work Logs and Feed (15, bbda1d0), Directory by department (4, ca0c620), Automation pages (20, 876cdf7; runs execute in the web request, the background runner was not used).
Uncovered by these clips (controls exist or are unbuilt, not exercised here): PDF text mark, signature, flatten, split, reorder, merge, forms, password files; Attendance real face matching and camera; Certificates Print/PDF, Raw SVG, reissue, expiry, filters; Work Logs manager review and filters (older tours, not re-run); Directory team detail, org chart, a person in several departments.
This is not a statement that these modules are ready.

## Update, 28 Nov 2026 15:12 IST (one run each, headless Chromium 390x844 emulated, TiDB 8.5.1 unistore, one machine, executor-watched)
Recorded at code head 6c24e89 (or code-equivalent for these modules; later commits touch only this doc):
- IAM console tabs (20 steps): page loading and active-tab highlight only. Not shown: any create, change, revoke, reset or approval form, IAM host sign-in and recovery, Super Admin step-up, live event stream, Shield, backups, activation codes, service tokens.
- Admin panel (11): approve one fallback request and reject another; manager-only view, HR role, check-out requests, self-review refusal, already-reviewed error and review notes not shown.
- Calendar series (10): re-recorded with the corrected closing caption. No guests invited in the clip. Un-cancel and single-occurrence edit are not built.
- Directory team detail (6): profile to team to member profile. No-team profile and role without team access not shown. No org chart tour exists.
- Tasks project trash and restore (6), project rename (6), milestone edit (6): owner's view only; member and other-person views not shown.
Still without a clip at the current head: Sheets and Slides main tours, Work Log filter and date tours, IAM step-up, forms and exit-session tours, Drive device-file, move and find, Chat attachments-free flows already covered earlier, org chart. Real two-device and Cloud/TiKV qualification: scope not yet grounded from the original PDFs and messages; not recorded as a user decision.
These are scoped test observations, not a statement that any module is ready.
