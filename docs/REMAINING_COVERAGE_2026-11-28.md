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

## Update, 28 Nov 2026 (afternoon, IST), Slides tools (one run each, headless Chromium 390x844 emulated, TiDB 8.5.1 unistore, one machine, executor-watched)
App code head for these clips is 952e9f5 (executor-reported). Basis in the commit record: the changes from 952e9f5 to the doc's parent commit touch only docs and tests/browser/tours.py, so no app code changed after it. Device-local deck for all of them. Frame sheets and last frames were viewed by the executor; where a claim rests on a passed step assertion or a tour readback (deck versions, stored keys, hashes, input boxes) and is not legible in the frames, it is marked "readback".
- Slides layout (11): Title layout, linked and unlinked slides, a local size override, layout edit propagating to the linked slide only, undo and redo of the layout edit, unlink, reload. Stored versions and keys are readback. The tour's source and JSON metadata list "undo of a layout edit" as not shown, but a step does undo and redo it (both captions are on screen in the clip). That stale list is not burned into the video: the on-screen captions are the step captions and the clip has no closing not-shown caption. The stale list is only in tours.py and the run JSON. Not shown: export and import of a linked deck, the PDF of a linked slide (node only).
- Slides find (8): find, match case, replace in all slides and on this slide only, undo, no-match message, replace with an item selected. The tour records no not-shown list; its note describes the feature as plain-text find and replace over the slides' own items (tour reading, not a source check).
- Slides paste (12): copy, cut and paste between slides, undo, bold style kept, a picture pasted with the same stored hash. The refusal wording for a picture whose file is not in this browser was not readable in the frames; the 10 MB and 48 million pixel refusals use synthetic sizes, not real files.
- Slides align (9): align left, right, centre, top, bottom, middle on a box, undo. The x and y values (0, 660, 330, 0, 490, 245) are readback.
- Slides background (8): the dark backgrounds listed as not selectable, the pale-on-pale refusal and the question on switching to Dark were not readable in the frame sheet; they rest on passed step assertions. Pale yellow shows in the editor and in Present. The PDF fill is checked in node, not in this clip. Not shown: dismissing the theme question, master items, a hand-edited unreadable import.
- Slides underline (6): off until an item is selected; underline on, in Present, after reload, off, undo. Deck versions (12, then 1) and the u key are readback.
- Slides text speed (23): visible on screen: the speed select with Normal, Slow and Fast per slide, reload keeping them, None removing the speed, and in Present the Slow and Fast fade frames (frozen by the tour; the Slow frame is visibly faint and the Fast frame almost fully shown). Numeric readbacks, not legible on screen: the Present durations (400 ms Normal, 150 ms frozen for Slow and Fast, read from the browser), the deck versions (3, 10, 11) and the stored speed values. Reduced motion was emulated in headless Chromium for both kinds of animation; no real-device setting was tried. The closing caption on screen names the timing readback, the emulated reduced motion, real devices untried, and the PDF and editor as not shown (they do not animate by design).
- Slides mouse drag and animation order (6): mouse drag and corner drag (box values 80 to 255 and 200 to 305), animation order earlier and later. Mouse pointer, not a finger. Not shown: finger drag on a real touch screen, unreadable-deck recovery.
- Slides editing and export (33_slides_more, 17): add, move, resize, colour, italic and centre, picture add, send backward, delete, fade in, master footer, master off for a slide, Export (.json), Export PDF, Clear deck, Import back. The exported files were checked off video by download assertions; their contents were not visually inspected. On slide 1 the master footer text visibly overlaps the Roadmap text item at the end of the clip (tour-chosen positions; no overlap handling is claimed). Not shown: finger drag, arrow-key move, Export with pictures, unreadable-deck recovery, Move down.
- Slides keys and order (65, 6): Right arrow and Shift+Right on a selected item (box readback), Move up, Move down, Delete key. The order after Move up is read from the list, not legible in the clip. No closing not-shown caption is on screen. Not shown: finger drag, unreadable-deck recovery, animation click earlier or later.
- Slides order recovery (85, 11): Appear on click order, Present steps, an unreadable stored deck (a test-written string, not real corruption) pauses editing without changing the data, download of the data and start a new deck that keeps a backup. That the download equals the stored string is an assertion on the saved file. Not shown: a real touch screen, fade timing, a damaged deck from a newer app version.
## Update, 28 Nov 2026 (4:40 PM, IST), Drive, IAM and Work Log (one run each, headless Chromium 390x844 emulated, TiDB 8.5.1 unistore, one machine, executor-watched)
Same app code basis as above. Each line separates what is visible in the clip from what rests on step assertions or readback. These are scoped test observations on the test account and fixtures.
- Drive link: the final caption claims both files, but the last frame does not show the file list. Hash, size and values are assertions. The step where the device copy is gone is shown by caption only.
- Drive link_fail: the status messages are below the fold, so the statuses and "Drive has no record" are assertions. The quota failure is simulated. The damaged copy is test-written bytes.
- Drive link_stale: the two warnings are visible and legible. "Sheet keeps its edit" is an assertion. The Drive-side changes are scripted off screen.
- Drive version_fail: all status messages are offscreen. Drive versions and content are assertions. The failure is injected.
- Drive link_slides_retry: the refusal (last frame), the lost-answer status and "Saved to Drive" (tiles) are visible. The checksum and "one version 1" are assertions.
- Drive two_sessions: the account banners, "Shared with ST-LIVE-2", the Editor row, Shared with me and "Folder created" are visible. The final "Made by second" listing is below the fold, so it is an assertion. The second sign-in is off camera. The tour's JSON not-shown list (not on screen) names both accounts open at the same moment.
- IAM forms (21): the refusals and the step-up page are visible, the password field is masked. The successful create is done off camera and checked by script only. The on-screen closing caption omits Super Admin creation, which the JSON lists.
- IAM exit_session (91): the Revoke buttons, the step-up page and the "Session ba3d6ce0... terminated." banner are visible. The final session list is not visible in the last frame, so it is an assertion. JSON not-shown list (not on screen): a Super Admin target, revoking another admin's session at level 3, recovery and level 3 dual approvals.
- Work Log date (36): the prefilled date 11/28/2026 is visible. Scope is the prefilled date only.
- Work Log team refusal (69): the filters and the styled refusal page are visible. Status 403 is an assertion. The old step caption said "plain 403 Forbidden", which did not match the screen; the clip was caption-corrected on the existing recording and the tour text fixed. Not shown: other roles' edit rules and moderator edit.
Still without a clip at the current head: org chart (no tour exists). Below-the-fold statuses listed above are assertions until focused captures are made. Two-device and Cloud/TiKV qualification scope is not yet grounded.
These are scoped test observations, not a statement that any module is ready.
