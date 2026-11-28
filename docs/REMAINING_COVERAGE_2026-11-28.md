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

## Update, 28 Nov 2026 (afternoon, IST) (one run each, headless Chromium 390x844 emulated, TiDB 8.5.1 unistore, one machine, executor-watched)
Recorded at code head 6c24e89 (or code-equivalent for these modules; later commits touch only this doc):
- IAM console tabs (20 steps): page loading and active-tab highlight only. Not shown: any create, change, revoke, reset or approval form, IAM host sign-in and recovery, Super Admin step-up, live event stream, Shield, backups, activation codes, service tokens.
- Admin panel (11): approve one fallback request and reject another; manager-only view, HR role, check-out requests, self-review refusal, already-reviewed error and review notes not shown.
- Calendar series (10): re-recorded with the corrected closing caption. No guests invited in the clip. Un-cancel and single-occurrence edit are not built.
- Directory team detail (6): profile to team to member profile. No-team profile and role without team access not shown. No org chart tour exists.
- Tasks project trash and restore (6), project rename (6), milestone edit (6): owner's view only; member and other-person views not shown.
Still without a clip at the current head: Sheets and Slides main tours, Work Log filter and date tours, IAM step-up, forms and exit-session tours, Drive device-file, move and find, org chart. Real two-device and Cloud/TiKV qualification: scope not yet grounded from the original PDFs and messages; not recorded as a user decision.
These are scoped test observations, not a statement that any module is ready.

## Update, 28 Nov 2026 (3:30 PM, IST) (one run each, headless Chromium 390x844 emulated, TiDB 8.5.1 unistore, one machine, executor-watched)
App code head for all of these clips is 952e9f5 (executor-reported; the git record from 952e9f5 to this commit changes only this doc and tests/browser/tours.py, so no app code changed after it). The tour text commits 60bf936 and d46c46a touch only tour text and one tour step. Drive find was re-run after its step edit (d46c46a). Drive move was recorded before 60bf936; its closing caption was corrected on the existing recording by overlay and it was not re-recorded. Drive confirm was run once at the same app code head.
- Sheets cells (13) and Sheets tools B (13): closing notes name columns A to Z and rows to 1000. The limits and the import behaviour (CSV import leaves cells outside Z1000 out and counts them) are a source reading of formula.js MAX_ROW and MAX_COL and model.js CSV_ROWS; the import was not recorded. Not shown: formulas beyond the listed tours, cell comments.
- Slides main (22 steps): the other Slides tool tours listed below are not covered by this clip (the stack tour was delivered earlier).
- Work Log filter (5): filter only. The date and team-refusal tours are not re-recorded.
- Drive device files (11): real IndexedDB, bytes stay in the browser, the server holds name, size and checksum. The storage failure is injected, not a real quota. The two-namespace check uses two store names in one browser, not two accounts. Not shown: real quota exhaustion, sign-in expiry, another person's device or account, no-network reload. Nothing is sent to another person; that is not built.
- Drive move (6): move a folder into another and see it there. Not shown: move to top level, a refused move, moving a file, a Viewer's refusal.
- Drive find (7): the first run failed one step because the tour was stale (the 50-folder note is inside a collapsed note); the step now opens it and keeps the same check. Ends on the Confirm move page. Not shown: pressing Move and share, Cancel, no-match search, more than 50 matches.
- Drive confirm (7): moving a private folder into a shared one opens Confirm move, names Second Synthetic as viewer, and Move and share completes it; the folder is then listed inside. Not shown: Cancel, the list of people who lose access, a non-owner's count-only view.
- Drive nested note (7): collapsed sharing note opens and closes; moving Project box into Team box (shared) opens Confirm move and says 1 item inside is shared directly and that does not change. Ends before Move and share; the move was not completed. Not shown: Move and share, a folder with several nested shares.
- Drive share and trash (7): a wrong employee ID is refused and the folder stays unshared; sharing lists Second Synthetic as Editor; the folder goes to Trash and is listed with Restore. Not shown: pressing Restore, the second person's own view, what Trash does with contents beyond the list.
- Drive editor (9): an Editor creates and renames a folder inside a folder shared with them; the tour observed Share folder and Remove access absent and Move to trash present for an Editor; then shares an own folder as Editor. One signed-in user, a fixture colleague made by script. The tour does not prove the server refuses sharing by an Editor. No closing caption lists the not-shown items.
- IAM step-up (12): observed on the test administrator account only (ST-LIVE-1, a fixture, basic level). Suspend, reactivate and role change on a test target redirect to the Assurance Level 2 step-up page and work after a step-up done off camera (the code and password are not filmed); in this run, add to team went through with no step-up page for that account (a basic-level action as the tour records it; not a statement about other roles). No revoked-session case and no step-up expiry case is shown. Not shown: the step-up entry, Super Admin changes, MFA reset, recovery approve or reject, break-glass, service identities, outbox retry, session revoke.
Still without a clip at the current head: Slides tools tours (more, layout, find, paste, align, bg, keys, pointer, order recovery, underline), Drive two-session and link tours, IAM forms and exit-session, Work Log date and team refusal, org chart (no tour). Two-device and Cloud/TiKV qualification scope is not yet grounded.
These are scoped test observations, not a statement that any module is ready.
