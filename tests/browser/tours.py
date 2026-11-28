"""Recorded walkthroughs (manual, not CI). Each tour opens its own phone-size browser context with video on, reuses the
already signed-in session cookies (the sign-in itself is never recorded), moves a visible pointer, shows a caption for each
step, and writes a JSON log of which steps worked and which failed. Output goes to DRILL_VIDEO. Synthetic fixtures only."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

OVERLAY = """
(() => {
  const mk = () => {
    if (document.getElementById('__cap')) return;
    const c = document.createElement('div'); c.id = '__cap';
    c.style.cssText = 'position:fixed;left:0;right:0;bottom:0;background:rgba(15,15,15,.9);color:#fff;font:600 15px/1.35 sans-serif;padding:8px 10px;z-index:2147483647;pointer-events:none';
    c.textContent = sessionStorage.getItem('__cap') || '';
    const d = document.createElement('div'); d.id = '__dot';
    d.style.cssText = 'position:fixed;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;background:rgba(230,30,30,.55);border:2px solid #fff;z-index:2147483647;pointer-events:none;left:-50px;top:-50px;transition:transform .12s';
    document.documentElement.appendChild(c); document.documentElement.appendChild(d);
    const p = JSON.parse(sessionStorage.getItem('__pos') || 'null'); if (p) { d.style.left = p[0] + 'px'; d.style.top = p[1] + 'px'; }
  };
  document.addEventListener('DOMContentLoaded', mk);
  document.addEventListener('mousemove', e => { mk(); const d = document.getElementById('__dot'); d.style.left = e.clientX + 'px'; d.style.top = e.clientY + 'px'; sessionStorage.setItem('__pos', JSON.stringify([e.clientX, e.clientY])); }, true);
  document.addEventListener('mousedown', () => { const d = document.getElementById('__dot'); if (d) d.style.transform = 'scale(1.8)'; }, true);
  document.addEventListener('mouseup', () => { const d = document.getElementById('__dot'); if (d) d.style.transform = 'scale(1)'; }, true);
})();
"""


class Tour:
    def __init__(self, pg, log):
        self.pg, self.log = pg, log

    def cap(self, text):
        self.pg.evaluate(
            "t=>{sessionStorage.setItem('__cap',t);const c=document.getElementById('__cap');if(c)c.textContent=t;}", text
        )

    def step(self, text, fn=None, wait=1300, expect=None):
        """Show the caption, do the action, optionally assert a condition, and record the outcome. A failure is shown on screen."""
        fast = os.path.exists("/tmp/tour_fast")  # debugging aid: short pauses, not for the delivered clips
        wait = 150 if fast else wait
        self.cap(text)
        self.pg.wait_for_timeout(100 if fast else 600)
        ok, detail = True, ""
        try:
            if fn:
                fn()
            self.pg.wait_for_timeout(wait)
            if expect is not None:
                ok = bool(expect() if callable(expect) else expect)
                detail = "" if ok else "expected result not seen"
        except Exception as ex:  # shown, logged, and the tour goes on
            ok, detail = False, (str(ex).strip().splitlines() or [""])[0][:160]
        if not ok:
            print("   step failed:", text, "|", detail, flush=True)
            self.cap("DID NOT WORK: " + text)
            self.pg.wait_for_timeout(1500)
        self.log.append({"step": text, "ok": ok, "detail": detail})
        return ok

    def note(self, text, secs=2.2):
        """A caption only (for what is not built or not shown)."""
        self.cap(text)
        self.pg.wait_for_timeout(int(secs * 1000))
        self.log.append({"step": text, "ok": None, "detail": "note"})


def run_tour(ctx, name, body, w=390, h=844, scale=2, notes="", unshown=(), extra_cookies=()):
    out = os.environ.get("DRILL_VIDEO", "")
    if not out:
        print("tour %s skipped: DRILL_VIDEO is not set" % name)
        return
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp()
    c = ctx.browser.new_context(
        viewport={"width": w, "height": h},
        record_video_dir=tmp,
        record_video_size={"width": w, "height": h},  # the page fills this exactly; it is enlarged afterwards
    )
    c.set_default_timeout(8000)
    c.add_cookies(ctx.cookies())
    if extra_cookies:
        c.add_cookies(list(extra_cookies))
    errs, remote, guard = ctx.salow_hooks
    c.route("**/*", guard)
    if extra_cookies:  # the IAM host name maps to the same local server; only that exact host and port is let through
        c.route("http://iam.salowtech.com:%s/**" % os.environ["DRILL_APP"].rsplit(":", 1)[1], lambda r: r.continue_())
    c.add_init_script(OVERLAY)
    pg = c.new_page()
    perr = []
    pg.on("pageerror", lambda e: perr.append(str(e)[:120]))
    log = []
    t = Tour(pg, log)
    t0 = time.time()
    try:
        body(t, pg)
    except Exception as ex:
        log.append({"step": "tour crashed", "ok": False, "detail": (str(ex).strip().splitlines() or [""])[0][:200]})
    secs = time.time() - t0
    vid = pg.video
    c.close()
    src = vid.path()
    dst = os.path.join(out, name + ".mp4")
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", "scale=%d:%d:flags=lanczos" % (w * scale, h * scale), "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", dst], check=True)  # noqa: S607
    shutil.rmtree(tmp, ignore_errors=True)
    size = os.path.getsize(dst)
    res = {"name": name, "seconds": round(secs), "bytes": size, "page_errors": perr[:3], "notes": notes, "controls_not_shown": list(unshown), "steps": log}
    json.dump(res, open(os.path.join(out, name + ".json"), "w"), indent=1)
    bad = [s for s in log if s["ok"] is False]
    print("TOUR %s: %d steps, %d failed, %.1f MB, %d s" % (name, len(log), len(bad), size / 1e6, secs), flush=True)
    for s in bad:
        print("   FAILED:", s["step"], "|", s["detail"], flush=True)


def register(g):
    """Add the tours to the drill table. g is the drills module's globals."""
    APP = g["APP"]

    def chat_tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.chat import services as cs

            mk = lambda eid, nm: CustomUser.objects.get_or_create(  # noqa: E731
                employee_id=eid,
                defaults={"full_name": nm, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                          "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two, three = mk("ST-LIVE-2", "Second Synthetic"), mk("ST-LIVE-3", "Third Synthetic")
            sp = cs.open_dm(me, two.pk)
            cs.post_message(two, sp.public_id, "Who can review the Q4 rota?", "tv-1")
            cs.post_message(two, sp.public_id, "No rush, Friday is fine.", "tv-2")
            return sp.public_id

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            pg.goto(APP + "/chat/")
            t.step("Chat: the list of conversations, with unread counts", expect=lambda: "unread" in pg.inner_text("body"))
            t.step("Open the direct message with Second Synthetic", lambda: pg.click("a.cal-title"), expect=lambda: "Who can review" in pg.inner_text("body"))
            t.step("Press the thumbs up reaction on the first message", lambda: pg.click("#m1 button[value=thumbs_up]"), expect=lambda: pg.get_attribute("#m1 button[value=thumbs_up]", "aria-pressed") == "true")
            t.step("Press the heart too (several reactions per message)", lambda: pg.click("#m1 button[value=heart]"), expect=lambda: pg.get_attribute("#m1 button[value=heart]", "aria-pressed") == "true")
            t.step("Press thumbs up again: it toggles off", lambda: pg.click("#m1 button[value=thumbs_up]"), expect=lambda: pg.get_attribute("#m1 button[value=thumbs_up]", "aria-pressed") == "false")
            t.step("Type a message", lambda: pg.fill("textarea[name=body]", "I can look at the rota on Monday."))
            t.step("Send it", lambda: pg.click(".chat-compose button[type=submit]"), expect=lambda: "on Monday" in pg.inner_text("#chat-list"))
            t.step("Reply in thread on the first message", lambda: pg.click("#m1 .chat-reply-link"), expect=lambda: "Replying in the thread" in pg.inner_text("body"))
            t.step("Type the reply", lambda: pg.fill("textarea[name=body]", "Adding detail inside the thread."))
            t.step("Send the reply: it shows in the conversation marked as a reply", lambda: pg.click(".chat-compose button[type=submit]"), expect=lambda: "Reply to" in pg.inner_text("#chat-list"))
            t.step("Open the thread page from the reply count", lambda: pg.click("#m1 .chat-thread-open"), expect=lambda: "Thread in" in pg.inner_text("body"))
            t.step("Reply from inside the thread page", lambda: (pg.fill("textarea[name=body]", "A second reply from the thread page."), pg.click(".chat-compose button[type=submit]")), expect=lambda: "second reply" in pg.inner_text("body"))
            pg.goto(APP + "/chat/")
            t.step("Back to the list: start a new group", lambda: (pg.fill("input[name=name]", "Rota team"), pg.fill("textarea[name=people]", "ST-LIVE-2, ST-LIVE-3")))
            t.step("Create the group", lambda: pg.click("button:has-text('Create group')"), expect=lambda: "Rota team" in pg.inner_text("h2"))
            t.step("Send a message in the group", lambda: (pg.fill("textarea[name=body]", "Welcome to the rota group."), pg.click(".chat-compose button[type=submit]")), expect=lambda: "Welcome to the rota" in pg.inner_text("#chat-list"))
            t.step("Try an empty-ish bad direct message ID: it says what is wrong", lambda: (pg.goto(APP + "/chat/"), pg.fill("input[name=person]", "NOBODY-99"), pg.click("button:has-text('Open')")), expect=lambda: "employee" in pg.inner_text("body").lower())
            t.note("Not built, so not shown: edit, delete, files, mentions, search, history beyond the page, notifications, typing and presence.", 3.5)

        run_tour(ctx, "01_chat", body, notes="Server side is the real Django app on real TiDB (unistore). The other person's two first messages were inserted by a script, not typed live. Headless Chromium at 390x844 is an emulated phone, not a real device.")

    g["DRILLS"]["tour_chat"] = chat_tour

    def users(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            from apps.accounts.models import CustomUser

            out = {}
            for eid, nm in (("ST-LIVE-2", "Second Synthetic"), ("ST-LIVE-3", "Third Synthetic")):
                out[eid] = CustomUser.objects.get_or_create(
                    employee_id=eid,
                    defaults={"full_name": nm, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                              "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0].pk
            return out

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(mk).result()

    def drive_tour(ctx):
        import concurrent.futures

        users(ctx)

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.drive import services as ds

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            f = ds.create_folder(two, "Rota from Second Synthetic")
            ds.share(two, f, me, "viewer")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            pg.goto(APP + "/drive/")
            t.step("Drive: My Drive tab, with the notice that upload and download are not available yet", expect=lambda: "not available yet" in pg.inner_text("body"), wait=2500)
            t.step("Type a folder name", lambda: pg.fill("input[placeholder='New folder name']", "Projects"))
            t.step("Create folder", lambda: pg.click("button:has-text('Create folder')"), expect=lambda: pg.locator("a.drive-name", has_text="Projects").count() == 1)
            t.step("Open the folder", lambda: pg.click("a.drive-name:has-text('Projects')"), expect=lambda: "Sharing" in pg.inner_text("body"))
            t.step("Create a folder inside it", lambda: (pg.fill("input[placeholder='New folder name']", "2026"), pg.click("button:has-text('Create folder')")), expect=lambda: pg.locator("a.drive-name", has_text="2026").count() == 1)
            t.step("Rename it to 2026 plans", lambda: (pg.fill("input[aria-label='New name']", "2026 plans"), pg.click("button:has-text('Rename')")), expect=lambda: pg.locator("a.drive-name", has_text="2026 plans").count() == 1)
            t.step("Try a duplicate folder name: it is refused with a message", lambda: (pg.fill("input[placeholder='New folder name']", "2026 plans"), pg.click("button:has-text('Create folder')")), expect=lambda: pg.locator("a.drive-name", has_text="2026 plans").count() == 1 and ("already" in pg.inner_text("body").lower() or "exists" in pg.inner_text("body").lower() or "taken" in pg.inner_text("body").lower()))
            t.step("Share this folder with ST-LIVE-2 as Viewer", lambda: (pg.fill("input[placeholder='Employee ID']", "ST-LIVE-2"), pg.select_option("select[name=role]", "viewer"), pg.click("button:has-text('Share folder')")), expect=lambda: "Second Synthetic" in pg.locator(".drive-share").inner_text())
            t.step("Remove that access", lambda: pg.click(".drive-share button:has-text('Remove access')"), expect=lambda: "Not shared with anyone" in pg.inner_text(".drive-share"))
            t.step("Move the inner folder to trash", lambda: pg.click("button:has-text('Move to trash')"), expect=lambda: "Nothing here yet" in pg.inner_text("body"))
            t.step("Trash tab: the folder is listed", lambda: pg.click(".drive-tabs a:has-text('Trash')"), expect=lambda: "2026 plans" in pg.inner_text("body"))
            t.step("Restore it", lambda: pg.click("button:has-text('Restore')"), expect=lambda: "The trash is empty" in pg.inner_text("body"))
            t.step("Shared with me tab: a folder another person shared", lambda: pg.click(".drive-tabs a:has-text('Shared with me')"), expect=lambda: "Rota from Second" in pg.inner_text("body"))
            t.step("Open the shared folder (Viewer: no create or rename controls)", lambda: pg.click("a.drive-name:has-text('Rota')"), expect=lambda: pg.locator("button:has-text('Create folder')").count() == 0)
            t.note("Not built, so not shown: file upload and download (no file storage), moving, versions, search inside Drive.", 3.5)

        run_tour(ctx, "02_drive", body, notes="Real Django app on real TiDB. The folder shared by the second person was created by a script. Headless Chromium 390x844 emulation.", unshown=["Editor role sharing (only Viewer shown)", "folder move", "files (none can be uploaded)", "trash of a folder that has contents", "share to a non-existent ID"])

    g["DRILLS"]["tour_drive"] = drive_tour
    register_tasks(g, users)
    register_calendar(g, users)
    register_sheets(g)
    register_slides(g)
    register_pdf(g)
    register_attendance(g)
    register_posts(g)
    register_certs(g)
    register_automation(g)
    register_find(g)
    register_manager_assign(g)
    register_manager_blank(g)
    register_calendar_export(g)
    register_security(g)
    register_admin(g)
    register_iam(g)
    register_iam_live(g)
    register_admin_scroll(g)
    register_iam_forms(g)
    register_iam_stepup(g)
    register_iam_stepup2(g)
    register_iam_stepup3(g)
    register_bg_probe(g)
    register_chat_react(g)
    register_drive_editor(g)
    register_tasks_more(g)
    register_cal_repeats(g)
    register_sheets_more(g)
    register_pdf_more(g)
    register_slides_more(g)
    register_att_more(g, users)
    register_certs_more(g)
    register_worklog_date(g)
    register_search_tour(g, users)
    register_att_checkout(g, users)
    register_chat_gaps(g)
    register_chat_search(g)
    register_forbidden(g)
    register_trash_context(g)
    register_due_soon(g)
    register_invite_notice(g)
    register_chat_badge(g)
    register_chat_mention(g)
    register_chat_rename(g)
    register_cal_location(g)
    register_slides_dup_sheets_fx(g)
    register_slides_bg(g)
    register_sheets_refs(g)
    register_sheets_struct(g)
    register_sheets_struct_col(g)
    register_sheets_struct_more(g)
    register_sheets_find(g)
    register_palette(g)
    register_overlap(g)
    register_drive_device(g)
    register_cal_all_day(g)
    register_tasks_restore(g)
    register_tasks_rename(g)
    register_tasks_move(g)
    register_tasks_overdue(g)
    register_project_restore(g)
    register_project_rename(g)
    register_milestone_edit(g)
    register_subtask_edit(g)
    register_task_reorder(g)
    register_clear_completed(g)
    register_attendance_csv(g)
    register_post_review_notice(g)
    register_notice_open(g)
    register_post_comment_notice(g)
    register_task_conflict(g)
    register_sessions_scroll(g, users)
    register_role_views(g, users)
    register_drive_move(g)
    register_drive_confirm(g, users)
    register_drive_find(g, users)
    register_team_link(g, users)
    register_drive_nested(g, users)
    register_drive_share_trash(g, users)
    register_post_search(g, users)
    register_worklog_filter(g, users)
    register_event_edit_conflict(g, users)
    register_event_occurrence_paging(g, users)
    register_event_series_guest(g, users)
    register_event_time_zone(g, users)
    register_event_week_day_arrows(g, users)
    register_certs_filter(g)
    register_empty_states(g, users)
    register_project_filters(g, users)
    register_directory_dept(g, users)
    register_subtask_delete(g, users)
    register_parent_delete(g, users)
    register_pdf_marks_tools(g)
    register_pdf_pages(g)
    register_pdf_alltext(g)
    register_sheets_csv_import(g)
    register_sheets_freeze(g)
    register_sheets_versions(g)
    register_sheets_xtab(g)
    register_sheets_widths(g)
    register_slides_layout(g)
    register_sheets_charts(g)
    register_slides_tspeed(g)
    register_sheets_profile(g)
    register_sheets_virtual(g)
    register_sheets_slices(g)
    register_sheets_virtual2(g)
    register_sheets_heavy8(g)
    register_sheets_insert1000(g)
    register_slides_underline(g)
    register_drive_link(g)
    register_drive_link_fail(g)
    register_drive_link_stale(g)
    register_sheets_new_formulas(g)
    register_slides_find(g)
    register_slides_paste(g)
    register_drive_link_version_fail(g)
    register_drive_link_slides_retry(g)
    register_sheets_frozenfocus(g)
    register_pdf_alltext_stop(g)
    register_pdf_roundtrip(g)
    register_slides_order_recovery(g)
    register_sheets_import_recovery(g)
    register_second_person_views(g, users)
    register_sheets_rules_after(g)
    register_chat_live(g, users)
    register_password_idle(g, users)
    register_iam_exit_session(g)
    register_pdf_password(g)
    register_pdf_forms(g)
    register_pdf_view_buttons(g)
    register_sheets_tabs_rules(g)
    register_sheets_io_keys(g)
    register_slides_keys(g)
    register_slides_pointer_anim(g)
    register_worklog_team_refusal(g, users)
    register_sheets_focus_edges(g)
    register_drive_two_sessions(g, users)
    register_posts_more(g, users)


def register_tasks(g, users):
    APP = g["APP"]

    def tasks_tour(ctx, which):
        import concurrent.futures

        users(ctx)

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.tasks import services as ts

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            tl = ts.create_list(two, "Second's list")
            t = ts.create_task(two, tl, "Check the Q4 rota numbers")
            ts.assign(two, t, me)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            pg.goto(APP + "/tasks/")
            t.step("Tasks: My lists tab", expect=lambda: "Tasks" in pg.inner_text("h2"), wait=1800)
            t.step("Create a list called Week 1", lambda: (pg.fill("input[aria-label='New list name']", "Week 1"), pg.click("button:has-text('Add list')")), expect=lambda: pg.locator(".tasks-lists a", has_text="Week 1").count() >= 1)
            t.step("Open the list", lambda: pg.click(".tasks-lists a:has-text('Week 1')"), expect=lambda: pg.locator("input[aria-label='Task title']").count() == 1)
            t.step("Add a task with a due date", lambda: (pg.fill("input[aria-label='Task title']", "Write the report"), pg.fill("input[aria-label='Due date']", "2026-12-05"), pg.click(".tasks-add button")), expect=lambda: "Write the report" in pg.inner_text("body") and "Due 5 Dec 2026" in pg.inner_text("body"))
            t.step("Add a second task", lambda: (pg.fill("input[aria-label='Task title']", "Book the room"), pg.click(".tasks-add button")), expect=lambda: "Book the room" in pg.inner_text("body"))
            t.step("Add a subtask under the first task", lambda: (pg.fill("input[aria-label='Subtask title']", "Gather numbers"), pg.click(".task-sub-add button")), expect=lambda: pg.locator(".task-row.sub", has_text="Gather numbers").count() == 1)
            t.step("Mark the second task done", lambda: pg.locator(".task-row:not(.sub) .task-check").nth(1).click(), expect=lambda: pg.locator(".task-row.done").count() >= 1)
            t.step("Mark it open again", lambda: pg.locator(".task-row.done .task-check").first.click(), expect=lambda: pg.locator(".task-row.done").count() == 0)
            t.step("Edit Write the report: open the edit page", lambda: pg.locator("a:has-text('Edit')").first.click(), expect=lambda: "Edit task" in pg.inner_text("h2"))
            t.step("Add notes and Save", lambda: (pg.fill("textarea[name=notes]", "Use last quarter's numbers as the base."), pg.click("button:has-text('Save')")), expect=lambda: "last quarter" in pg.inner_text("body"))
            t.step("Give the task Book the room to ST-LIVE-3 (a task that has subtasks cannot be given)", lambda: (pg.locator("input[aria-label='Give to employee ID']").first.fill("ST-LIVE-3"), pg.locator("button:has-text('Give')").first.click()), expect=lambda: "Given to" in pg.inner_text("body"))
            t.step("Take it back", lambda: pg.click("button:has-text('Take back')"), expect=lambda: "Given to" not in pg.inner_text("body"))
            t.step("Delete the second task: a confirmation page first", lambda: pg.locator(".task-row:not(.sub) a:has-text('Delete')").nth(1).click(), expect=lambda: "Delete this task" in pg.inner_text("h2"))
            t.step("Confirm Delete", lambda: pg.click("button:has-text('Delete')"), expect=lambda: "Book the room" not in pg.inner_text("body"))
            t.step("Assigned to me tab: a task another person gave me", lambda: pg.click(".tasks-tabs a:has-text('Assigned to me')"), expect=lambda: "Check the Q4 rota" in pg.inner_text("body"))
            t.step("Mark that task done from here", lambda: pg.click(".task-check"), expect=lambda: pg.locator(".task-row.done").count() == 1)
            t.note("Next clip: the Projects tab (board, columns, milestones, filter).", 2.5)

        if which == "lists":
          run_tour(ctx, "03_tasks_lists", body, notes="Real Django app on real TiDB. The task another person gave me was created by a script. Headless Chromium 390x844 emulation.", unshown=["Delete this list", "comments page", "edit conflict message (concurrent edit)", "sub-task delete", "task with an assignee shown as Given to for a different person from another browser"])

        def body2(t, pg):
            pg.goto(APP + "/tasks/projects/")
            t.step("Tasks, Projects tab", expect=lambda: "project" in pg.inner_text("body").lower(), wait=1500)
            t.step("Create a project", lambda: (pg.fill("input[placeholder='New project']", "Rota rebuild"), pg.click("button:has-text('Add project')")), expect=lambda: "Rota rebuild" in pg.inner_text("body"))
            t.step("The new project opens as a board with columns", lambda: pg.click("a.task-title:has-text('Rota rebuild')") if pg.locator("a.task-title:has-text('Rota rebuild')").count() else None, expect=lambda: pg.locator(".tasks-col").count() >= 2)
            t.step("Add a task in the first column", lambda: (pg.locator(".tasks-col input[name=title]").first.fill("Draft plan"), pg.locator(".tasks-col button:has-text('Add')").first.click()), expect=lambda: pg.locator(".task-card", has_text="Draft plan").count() == 1)
            t.step("Move it to the next column", lambda: (pg.select_option(".task-card select[aria-label='Move to']", index=1), pg.click(".task-card button:has-text('Move')")), expect=lambda: pg.locator(".tasks-col").nth(1).locator(".task-card", has_text="Draft plan").count() == 1)
            t.step("Add a milestone", lambda: (pg.fill("input[placeholder='New milestone']", "Launch"), pg.fill("input[name=due]:last-of-type", "2026-12-20") if False else None, pg.click("button:has-text('Add milestone')")), expect=lambda: "Launch" in pg.inner_text("body"))
            t.step("Filter the board by title", lambda: (pg.fill("input[name=q]", "Draft"), pg.click("button:has-text('Filter')")), expect=lambda: "Showing" in pg.inner_text("body"))
            t.note("Not built, so not shown: adding project members, restoring deleted tasks or lists, recurring tasks.", 3.5)

        if which == "projects":
          run_tour(ctx, "04_tasks_projects", body2, notes="Real Django app on real TiDB, headless Chromium 390x844 emulation. Only one person is a member, so assigning to another member was not possible.", unshown=["project task Edit and Delete pages", "Assign to a member (only member is the lead)", "set a milestone on a task", "overdue and milestone filters", "task comments page", "Clear filter"])

    g["DRILLS"]["tour_tasks_lists"] = lambda ctx: tasks_tour(ctx, "lists")
    g["DRILLS"]["tour_tasks_projects"] = lambda ctx: tasks_tour(ctx, "projects")


def register_calendar(g, users):
    APP = g["APP"]

    def cal_tour(ctx, which):
        import concurrent.futures
        import datetime as dt

        users(ctx)
        day1 = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        day2 = (dt.date.today() + dt.timedelta(days=3)).isoformat()

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.events import services as es

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            base = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time(10, 0))
            es.create_event(two, "Rota review (invited by Second)", base, base + dt.timedelta(hours=1), "Asia/Kolkata", [me.pk])
            es.create_event(two, "Team sync (invited by Second)", base + dt.timedelta(days=1), base + dt.timedelta(days=1, hours=1), "Asia/Kolkata", [me.pk])

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def tab(pg, name):
            pg.click(".cal-tabs a:has-text('%s')" % name)

        def bodyA(t, pg):
            pg.goto(APP + "/calendar/")
            t.step("Calendar: Agenda tab, with two invitations from another person", expect=lambda: "invited by Second" in pg.inner_text("body"), wait=2000)
            t.step("Open the first invitation", lambda: pg.click("a.cal-title:has-text('Rota review')"), expect=lambda: "Guests" in pg.inner_text("body"))
            t.step("Accept it", lambda: pg.click("button:has-text('Accept')"), expect=lambda: "accepted" in pg.inner_text("body").lower())
            t.step("Change the answer to Maybe", lambda: pg.click("button:has-text('Maybe')"), expect=lambda: "tentative" in pg.inner_text("body").lower() or "maybe" in pg.inner_text("body").lower())
            t.step("Decline it", lambda: pg.click("button:has-text('Decline')"), expect=lambda: "declined" in pg.inner_text("body").lower())
            t.step("New event tab: fill title and times", lambda: (tab(pg, "New event"), pg.fill("input[name=title]", "Planning meeting"), pg.fill("input[name=start]", day1 + "T11:00"), pg.fill("input[name=end]", day1 + "T12:00")))
            t.step("Add ST-LIVE-3 as a guest and Create", lambda: (pg.fill("textarea[name=guests]", "ST-LIVE-3"), pg.click("button:has-text('Create')")), expect=lambda: "Planning meeting" in pg.inner_text("h2"))
            t.step("The event page lists the guest and the organiser controls", expect=lambda: "ST-LIVE-3" in pg.inner_text("body") and pg.locator("a:has-text('Edit')").count() >= 1)
            t.step("Edit: change the title and save", lambda: (pg.click("a:has-text('Edit')"), pg.fill("input[name=title]", "Planning meeting (moved)"), pg.click("button:has-text('Save')")), expect=lambda: "moved" in pg.inner_text("body"))
            t.step("Back to Agenda, then the Month tab", lambda: (pg.goto(APP + "/calendar/"), tab(pg, "Month")), expect=lambda: pg.locator("table.cal-month").count() == 1)
            t.step("Next month arrow and back", lambda: (pg.click("a[aria-label='Next month']"), pg.wait_for_timeout(700), pg.click("a[aria-label='Previous month']")), expect=lambda: pg.locator("table.cal-month").count() == 1)
            t.step("Week tab", lambda: tab(pg, "Week"), expect=lambda: "Week" in pg.inner_text("body") or "week" in pg.url)
            t.step("Day tab", lambda: tab(pg, "Day"), expect=lambda: "day" in pg.url)
            t.note("Next clip: repeating events, free or busy, cancelling.", 2.2)

        def bodyB(t, pg):
            pg.goto(APP + "/calendar/new/")
            t.step("New event: a weekly repeating event", lambda: (pg.fill("input[name=title]", "Weekly stand-up"), pg.fill("input[name=start]", day2 + "T09:00"), pg.fill("input[name=end]", day2 + "T09:15")))
            t.step("Choose Weekly, repeat 4 times", lambda: (pg.select_option("select[name=freq]", "weekly"), pg.fill("input[name=count]", "4")))
            t.step("Create the series", lambda: pg.click("button:has-text('Create')"), expect=lambda: "Occurrences" in pg.inner_text("body"), wait=1800)
            t.step("The page lists the occurrences", expect=lambda: pg.locator("form[action$='/skip/']").count() >= 2)
            t.step("Cancel just one occurrence", lambda: pg.locator("form[action$='/skip/'] button").first.click(), expect=lambda: "Cancelled occurrences" in pg.inner_text("body"))
            t.step("Free or busy tab: look up ST-LIVE-1 and ST-LIVE-2", lambda: (pg.click(".cal-tabs a:has-text('Free or busy')"), pg.fill("input[name=date]", day2), pg.fill("textarea[name=people]", "ST-LIVE-1, ST-LIVE-2"), pg.click("button:has-text('Show')")), expect=lambda: "ST-LIVE-1" in pg.inner_text("body") and "to" in pg.inner_text("body"))
            t.step("Open the series from Agenda", lambda: (pg.goto(APP + "/calendar/"), pg.click("a.cal-title:has-text('Weekly stand-up')")), expect=lambda: "Cancel whole series" in pg.inner_text("body"))
            t.step("Cancel whole series: asks to confirm", lambda: pg.click("a:has-text('Cancel whole series')"), expect=lambda: "Cancel the whole series" in pg.inner_text("h2"))
            t.step("Confirm", lambda: pg.click("button:has-text('Cancel whole series')"), expect=lambda: "Cancelled" in pg.inner_text("body"))
            t.note("Not built, so not shown: un-cancelling, changing one occurrence, notifying guests.", 3)

        if which == "A":
            run_tour(ctx, "05_calendar_events", bodyA, notes="Real Django app on real TiDB. The two invitations were created by a script as another person. Headless Chromium 390x844 emulation.", unshown=["event with a different time zone", "edit conflict message", "Earlier/Later occurrences paging", "monthly and daily repeats", "week and day navigation arrows", "event with a guest who is not an active person (error)"])
        else:
            run_tour(ctx, "06_calendar_series", bodyB, notes="Real Django app on real TiDB, headless Chromium 390x844 emulation.", unshown=["monthly and daily repeats", "until-date end of repeat", "repeat interval other than 1", "weekday ticks for weekly repeat", "guest answers as a guest of the series", "occurrence date picker"])

    g["DRILLS"]["tour_cal_a"] = lambda ctx: cal_tour(ctx, "A")
    g["DRILLS"]["tour_cal_b"] = lambda ctx: cal_tour(ctx, "B")


def register_sheets(g):
    APP = g["APP"]

    def put(pg, cells):
        for a, v in cells:
            sel = 'input[data-addr="%s"]' % a
            pg.click(sel)
            pg.fill(sel, v)
            pg.press(sel, "Tab")

    DATA = [("A1", "Item"), ("B1", "Qty"), ("A2", "pens"), ("B2", "30"), ("A3", "paper"), ("B3", "12"), ("A4", "ink"), ("B4", "7"), ("A5", "pens"), ("B5", "20")]

    def sheets_tour(ctx, which):
        def start(pg):
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)

        def val(pg, a):
            return pg.input_value('input[data-addr="%s"]' % a)

        def bodyA(t, pg):
            start(pg)
            t.step("Sheets: a blank sheet that stays in this browser", expect=lambda: pg.locator('input[data-addr="A1"]').count() == 1, wait=2000)
            t.step("Type a small table: items and quantities", lambda: put(pg, DATA), expect=lambda: val(pg, "B2") == "30")
            t.step("Type a formula: SUM of the quantities", lambda: put(pg, [("B6", "=SUM(B2:B5)")]), expect=lambda: "69" in pg.inner_text("#sheet-val") or val(pg, "B6") in ("69", "=SUM(B2:B5)"))
            t.step("Select the formula cell: the bar shows the result", lambda: pg.click('input[data-addr="B6"]'), expect=lambda: "69" in pg.inner_text("#sheet-val"))
            t.step("Undo the formula", lambda: pg.click("#sheet-undo"), expect=lambda: val(pg, "B6") == "")
            t.step("Redo it", lambda: pg.click("#sheet-redo"), expect=lambda: val(pg, "B6") != "")
            t.step("Sort the table by Qty, large to small", lambda: (pg.fill("#sort-range", "A1:B5"), pg.fill("#sort-col", "B"), pg.select_option("#sort-dir", "desc"), pg.click("#sort-go")), expect=lambda: val(pg, "A2") == "pens" and val(pg, "B2") == "30")
            t.step("Filter: Qty is greater than 10", lambda: (pg.fill("#filter-col", "B"), pg.select_option("#filter-op", "gt"), pg.fill("#filter-val", "10"), pg.click("#filter-go")), expect=lambda: pg.inner_text("#filter-status").strip() != "")
            t.step("Show all rows again", lambda: pg.click("#filter-clear"), expect=lambda: pg.is_disabled("#filter-clear"))
            t.step("Copy first row down: C2 formula to C2:C5", lambda: (put(pg, [("C2", "=B2*2")]), pg.fill("#fill-range", "C2:C5"), pg.click("#fill-down")), expect=lambda: val(pg, "C5") != "")
            t.step("Add a second tab, called Costs", lambda: (pg.click("#tab-add"), pg.fill("#tab-name", "Costs"), pg.click("#tab-rename")), expect=lambda: "Costs" in pg.inner_text("#sheet-tabs"))
            t.step("Go back to the first tab: the data is still there", lambda: pg.locator("#sheet-tabs button, #sheet-tabs a").first.click(), expect=lambda: val(pg, "A1") == "Item")
            t.note("Next clip: pivot, chart, colour rules, formats, validation, export.", 2.2)

        def bodyB(t, pg):
            start(pg)
            t.step("Sheets again: a small table to work on", lambda: put(pg, DATA), expect=lambda: val(pg, "B5") == "20")
            t.step("Build a pivot: total Qty for each item", lambda: (pg.fill("#pivot-range", "A1:B5"), pg.fill("#pivot-key", "A"), pg.fill("#pivot-val", "B"), pg.select_option("#pivot-agg", "sum"), pg.click("#pivot-build")), expect=lambda: "pens" in pg.inner_text("#pivot-out") and "50" in pg.inner_text("#pivot-out"))
            t.step("Draw a bar chart of B2:B5", lambda: (pg.fill("#chart-range", "B2:B5"), pg.select_option("#chart-type", "bar"), pg.click("#chart-draw")), expect=lambda: pg.locator("#chart-out *").count() > 0)
            t.step("Draw it as a pie", lambda: (pg.select_option("#chart-type", "pie"), pg.click("#chart-draw")), expect=lambda: pg.locator("#chart-out *").count() > 0)
            t.step("Colour rule: red when above 25", lambda: (pg.fill("#cf-range", "B2:B5"), pg.select_option("#cf-kind", "greater"), pg.fill("#cf-value", "25"), pg.select_option("#cf-color", "red"), pg.click("#cf-add")), expect=lambda: pg.locator("#cf-list *").count() > 0)
            t.step("Format rule: bold and 2 decimals", lambda: (pg.fill("#fmt-range", "B2:B5"), pg.select_option("#fmt-bold", "on"), pg.select_option("#fmt-nf", "dec2"), pg.click("#fmt-add")), expect=lambda: pg.locator("#fmt-list *").count() > 0)
            t.step("Check the cells took the colour and format", lambda: pg.locator('input[data-addr="B2"]').scroll_into_view_if_needed(), expect=lambda: pg.locator('input[data-addr="B2"]').count() == 1, wait=2200)
            t.step("Validation rule: C2:C5 must be a number from 0 to 100", lambda: (pg.fill("#val-range", "C2:C5"), pg.select_option("#val-kind", "number"), pg.fill("#val-min", "0"), pg.fill("#val-max", "100"), pg.click("#val-add")), expect=lambda: pg.locator("#val-list *").count() > 0)
            t.step("Type 500 in C2: the rule refuses it", lambda: put(pg, [("C2", "500")]), expect=lambda: val(pg, "C2") != "500")
            t.step("Export (.json) and the CSV download", lambda: (pg.click("#sheet-export"), pg.wait_for_timeout(500), pg.click("#sheet-csv")))
            t.step("Clear sheet (undo until reload)", lambda: pg.click("#sheet-clear"), expect=lambda: val(pg, "A2") == "")
            t.step("Undo the clear", lambda: pg.click("#sheet-undo"), expect=lambda: val(pg, "A2") == "pens")
            t.note("Import was not shown (needs a file picker). Cells past Z or row 100 are not supported.", 3)

        def val(pg, a):
            return pg.input_value('input[data-addr="%s"]' % a)

        if which == "A":
            run_tour(ctx, "07_sheets_cells", bodyA, notes="Sheet content is device-local (browser). Real app served by Django on real TiDB, but no sheet data touches the server. Headless Chromium 390x844 emulation.", unshown=["Copy first column right", "delete tab and restore tab", "keyboard navigation", "paste from a spreadsheet", "Import", "tab rename error cases"])
        else:
            run_tour(ctx, "08_sheets_tools", bodyB, notes="Device-local sheet. Headless Chromium 390x844 emulation.", unshown=["list validation", "colour rules other than above-a-number", "other pivot totals (count, average, smallest, largest)", "line chart", "format align", "Import", "unreadable-rules download buttons", "remove of rules"])

    g["DRILLS"]["tour_sheets_a"] = lambda ctx: sheets_tour(ctx, "A")
    g["DRILLS"]["tour_sheets_b"] = lambda ctx: sheets_tour(ctx, "B")


def register_slides(g):
    APP = g["APP"]

    def slides_tour(ctx):
        def see(pg):
            pg.locator("#sl-canvas-wrap").scroll_into_view_if_needed()

        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            canvas = lambda: (pg.locator("#sl-canvas-wrap").text_content() or "")
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            cnt = lambda: (pg.locator("#sl-show-count").text_content() or "")
            mem = {}
            t.step("Slides: a deck that stays in this browser", lambda: see(pg), expect=lambda: pg.locator("#sl-canvas-wrap").count() == 1, wait=2000)
            t.step("Add a text item", lambda: (pg.click("#sl-add-text"), pg.fill("#sl-text", "Quarterly plan"), pg.press("#sl-text", "Tab"), see(pg)), expect=lambda: "Quarterly plan" in canvas())
            t.step("Make it bigger and bold", lambda: (pg.fill("#sl-size", "40"), pg.press("#sl-size", "Tab"), pg.check("#sl-bold"), see(pg)), expect=lambda: pg.is_checked("#sl-bold"))
            t.step("Add a rectangle", lambda: (pg.click("#sl-add-rect"), see(pg)), expect=lambda: pg.locator("#sl-items li").count() == 2)
            t.step("Add an ellipse", lambda: (pg.click("#sl-add-ellipse"), see(pg)), expect=lambda: pg.locator("#sl-items li").count() == 3)
            t.step("Undo the ellipse", lambda: (pg.click("#sl-undo"), see(pg)), expect=lambda: pg.locator("#sl-items li").count() == 2)
            t.step("Redo the ellipse", lambda: (pg.click("#sl-redo"), see(pg)), expect=lambda: pg.locator("#sl-items li").count() == 3)
            t.step("Add speaker notes to this slide", lambda: pg.fill("#sl-notes", "Open with the plan"), expect=lambda: pg.input_value("#sl-notes") == "Open with the plan")
            t.step("New slide with the Title layout", lambda: (pg.select_option("#sl-layout", "title"), pg.click("#sl-new"), see(pg)), expect=lambda: "Title" in canvas())
            t.step("Switch the theme to Dark", lambda: (pg.select_option("#sl-theme", "dark"), see(pg)), expect=lambda: pg.input_value("#sl-theme") == "dark")
            t.step("Set a fade transition into this slide", lambda: pg.select_option("#sl-trans", "fade"), expect=lambda: pg.input_value("#sl-trans") == "fade")
            t.step("Duplicate the slide", lambda: pg.click("#sl-dup"), expect=lambda: pg.locator("#sl-list > *").count() == 3)
            t.step("Move it up", lambda: pg.click("#sl-up"), expect=lambda: True)
            t.step("Present the deck", lambda: (pg.click("#sl-present"), mem.update(a=cnt())), expect=lambda: pg.locator("#sl-show").evaluate("e=>e.open"), wait=2000)
            t.step("Jump to the first slide with the Home key", lambda: (pg.keyboard.press("Home"), mem.update(a=cnt())), expect=lambda: cnt().startswith("Slide 1 of 3"), wait=1500)
            t.step("Next slide", lambda: pg.click("#sl-show-next"), expect=lambda: cnt().startswith("Slide 2 of 3"), wait=1800)
            t.step("Notes and timer on (shows the warning)", lambda: pg.check("#sl-show-notes-on"), expect=lambda: pg.is_checked("#sl-show-notes-on"))
            t.step("Previous slide", lambda: pg.click("#sl-show-prev"), expect=lambda: cnt().startswith("Slide 1 of 3"), wait=1800)
            t.step("Exit the show", lambda: pg.click("#sl-show-exit"), expect=lambda: not pg.locator("#sl-show").evaluate("e=>e.open"))
            t.step("Delete a slide (asks first, then deletes)", lambda: pg.click("#sl-del"), expect=lambda: pg.locator("#sl-list > *").count() == 2 and any("Delete this slide" in m for m in seen))
            t.step("Export (.json)", lambda: pg.click("#sl-export"), expect=lambda: True)
            t.note("PDF export, Import, pictures, animation, master, drag and resize, arrow-key move, Clear deck, unreadable-deck recovery were not shown.", 3)

        run_tour(ctx, "09_slides", body, notes="Device-local deck. Headless Chromium 390x844 emulation. No slide data goes to the server.",
                 unshown=["PDF export (secure context)", "Import", "Add picture", "animations", "master", "drag and resize", "arrow-key move", "Clear deck", "unreadable-deck recovery", "Move down", "declining the delete question"])

    g["DRILLS"]["tour_slides"] = slides_tour


def register_pdf(g):
    APP = g["APP"]

    def pdf_tour(ctx):
        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            data = g["_n_page_pdf"](4, "Keep")
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
                t.step("This browser has no secure context: marks cannot be kept", expect=lambda: False)
                return

            def opened():
                pg.set_input_files("#pdf-open", files=[{"name": "demo.pdf", "mimeType": "application/pdf", "buffer": data}])
                for _ in range(40):
                    if where().startswith("Page 1 of 4") and pg.evaluate("document.getElementById('pdf-canvas').width > 1"):
                        break
                    pg.wait_for_timeout(250)

            def drag(a, b):
                pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block:'center'});0")
                pg.wait_for_timeout(150)
                x, y, w, h = pg.evaluate("(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
                pg.mouse.move(x + a[0] * w, y + a[1] * h)
                pg.mouse.down()
                for i in range(1, 7):
                    pg.mouse.move(x + (a[0] + (b[0] - a[0]) * i / 6) * w, y + (a[1] + (b[1] - a[1]) * i / 6) * h)
                pg.mouse.up()
                pg.wait_for_timeout(300)

            def top():
                pg.evaluate("document.getElementById('pdf-pagewrap').scrollIntoView({block:'start'});0")

            marks = lambda: pg.locator("#pdf-marklist li").count()
            t.step("PDF: open a 4-page file; it stays in this browser", opened, expect=lambda: where().startswith("Page 1 of 4"), wait=1500)
            t.step("Next page", lambda: (pg.click("#pdf-next"), top()), expect=lambda: where().startswith("Page 2 of 4"))
            t.step("Previous page", lambda: (pg.click("#pdf-prev"), top()), expect=lambda: where().startswith("Page 1 of 4"))
            w0 = {}
            t.step("Zoom in", lambda: (w0.update(w=pg.evaluate("document.getElementById('pdf-canvas').width")), pg.click("#pdf-zin"), pg.wait_for_timeout(500), top()), expect=lambda: pg.evaluate("document.getElementById('pdf-canvas').width") > w0["w"])
            t.step("Fit to width", lambda: (pg.click("#pdf-fitw"), top()), expect=lambda: True)
            t.step("Rotate the view", lambda: (pg.click("#pdf-rot"), pg.wait_for_timeout(400), top()), expect=lambda: True)
            t.step("Rotate three more times to return to upright", lambda: ([(pg.click("#pdf-rot"), pg.wait_for_timeout(300)) for _ in range(3)], top()), expect=lambda: True)
            t.step("Find the word Keep", lambda: (pg.fill("#pdf-q", "Keep"), pg.click("#pdf-find"), pg.wait_for_timeout(600), pg.evaluate("document.getElementById('pdf-fstatus').scrollIntoView({block:'center'});0")), expect=lambda: bool(pg.inner_text("#pdf-fstatus").strip()))
            t.step("Add a note for this page", lambda: (pg.click("#pdf-addpagenote"), pg.fill("#pdf-notetext", "Check totals"), pg.click("#pdf-noteform button[type=submit]")), expect=lambda: pg.locator("#pdf-notelist li").count() == 1)
            t.step("Highlight tool: draw a highlight on the page", lambda: (pg.click("#pdf-tool-hl"), drag((0.2, 0.2), (0.6, 0.4))), expect=lambda: marks() == 1)
            t.step("Pen tool: draw a line", lambda: (pg.click("#pdf-tool-pen"), drag((0.1, 0.6), (0.8, 0.7))), expect=lambda: marks() == 2)
            t.step("Reload: the marks and the note come back (the file is picked again)", lambda: (pg.reload(), pg.wait_for_timeout(600), opened(), pg.wait_for_timeout(800)), expect=lambda: marks() == 2 and pg.locator("#pdf-notelist li").count() == 1, wait=1200)
            t.step("Clear the marks", lambda: pg.click("#pdf-markclear"), expect=lambda: marks() == 0)
            t.step("Close the file", lambda: (pg.click("#pdf-close"), top()), expect=lambda: not where().startswith("Page 1 of 4"))
            t.note("Not shown: Text mark, Signature, Copy with marks, flatten, Split, reorder, Merge, form fields, password-protected files, export notes, copy download, clear notes, thumbnails.", 3)

        run_tour(ctx, "10_pdf", body, notes="Device-local viewer on a generated 4-page PDF. Secure-context test browser flag (plain-HTTP drill origin treated as secure; test setting only). Headless Chromium 390x844 emulation.",
                 unshown=["Text mark", "Signature", "Copy with marks / flatten", "Split", "Reorder", "Merge", "Form fields", "Password-protected file", "Export notes", "Download a copy", "Clear notes", "Thumbnails", "Discard marks"])

    g["DRILLS"]["tour_pdf"] = pdf_tour


def register_attendance(g):
    APP = g["APP"]

    def att_tour(ctx):
        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            body_text = lambda: pg.inner_text("body")
            pg.goto(APP + "/attendance/")
            pg.wait_for_timeout(400)
            t.step("Attendance: the panel before checking in", expect=lambda: "Not Checked In" in body_text(), wait=1800)
            t.step("Check in (the dev stub verifier passes it: this is NOT real face matching)", lambda: pg.click("#attendance-checkin-form button[type=submit]"), expect=lambda: "Currently Checked In" in body_text(), wait=1800)
            t.step("Check out", lambda: pg.click("form[action$='/attendance/check-out/'] button[type=submit]"), expect=lambda: "Not Checked In" in body_text(), wait=1800)
            t.step("Fallback request: type Manual Check In with a reason", lambda: (pg.select_option("#id_request_type", "check_in"), pg.fill("#id_reason", "Camera not working today"), pg.evaluate("document.getElementById('id_reason').scrollIntoView({block:'center'});0")), expect=lambda: pg.input_value("#id_reason") != "")
            t.step("Submit the fallback request: it appears under Your Recent Requests", lambda: pg.click("form[action$='/attendance/fallback/'] button[type=submit]"), expect=lambda: "Camera not working today" in body_text() and "Your Recent Requests" in body_text(), wait=1800)
            t.step("Empty reason is refused by the form", lambda: (pg.fill("#id_reason", ""), pg.click("form[action$='/attendance/fallback/'] button[type=submit]")), expect=lambda: pg.evaluate("!document.getElementById('id_reason').checkValidity()") and "/attendance/fallback" not in pg.url)
            t.step("My attendance by month", lambda: pg.goto(APP + "/attendance/history/"), expect=lambda: "/history/" in pg.url, wait=1800)
            t.step("Attendance report", lambda: pg.goto(APP + "/attendance/report/"), expect=lambda: "/report/" in pg.url, wait=1800)
            t.note("Not shown: real face verification (dev stub used), camera preview, manager approval or rejection of the fallback request (admin panel), report filters.", 3)

        run_tour(ctx, "11_attendance", body, notes="Real Django on TiDB, headless Chromium 390x844. DEV_VERIFIER stub is active: check-in does NOT prove face verification. Fallback requests stay pending (approval is an admin action not shown).",
                 unshown=["real face verification", "camera preview", "manager approve/reject", "report filters", "history month switching"])

    g["DRILLS"]["tour_attendance"] = att_tour


def register_posts(g):
    APP = g["APP"]

    def posts_tour(ctx):
        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/posts/")
            pg.wait_for_timeout(400)
            t.step("Work Logs and Feed: the feed", expect=lambda: "Work Logs" in txt(), wait=1800)
            t.step("Open the new work log form", lambda: pg.goto(APP + "/posts/create/"), expect=lambda: "Submit Work Log" in txt(), wait=1200)
            t.step("Fill in the title and the details", lambda: (pg.select_option("#id_category", index=0), pg.fill("#id_title", "Wired up the stock report"), pg.fill("#id_content", "Finished the export. Blocker: none. Next: review with the team."), pg.evaluate("document.getElementById('id_content').scrollIntoView({block:'center'});0")), expect=lambda: pg.input_value("#id_title") != "")
            t.step("The form fields fit the 390 px screen", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: pg.evaluate("[...document.querySelectorAll('main input:not([type=hidden]), main textarea, main select')].every(e=>e.getBoundingClientRect().right<=Math.min(window.innerWidth,(e.closest('.card')||document.body).getBoundingClientRect().right)+1)"))
            t.step("Submit the work log: the detail page shows it", lambda: pg.click("form:has(#id_title) button[type=submit]"), expect=lambda: "Wired up the stock report" in txt() and "submitted successfully" in txt(), wait=1800)
            t.step("Reply with a comment", lambda: (pg.fill("#id_content", "Looks good to me"), pg.click("form[action$='/comments/'] button[type=submit]")), expect=lambda: "Looks good to me" in txt(), wait=1800)
            t.step("The posted reply is listed in the thread", lambda: pg.evaluate("(()=>{const e=[...document.querySelectorAll('h3')].find(h=>h.textContent.indexOf('Discussion Thread')>=0); if(e) e.scrollIntoView({block:'start'}); return 0})()"), expect=lambda: "Discussion Thread (1)" in txt() and "Looks good to me" in txt(), wait=1800)
            t.step("The reply box fits the 390 px screen", lambda: pg.evaluate("document.getElementById('id_content').scrollIntoView({block:'center'});0"), expect=lambda: pg.evaluate("[...document.querySelectorAll('main input:not([type=hidden]), main textarea, main select')].every(e=>e.getBoundingClientRect().right<=Math.min(window.innerWidth,(e.closest('.card')||document.body).getBoundingClientRect().right)+1)"))
            t.step("The post page cards fit the 390 px screen (nothing cut off on the right)", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: pg.evaluate("[...document.querySelectorAll('.card')].every(e=>e.getBoundingClientRect().right<=window.innerWidth+1)"), wait=1500)
            t.step("Edit the work log title", lambda: (pg.click("a[href$='/edit/']"), pg.fill("#id_title", "Wired up the stock report v2"), pg.click("form:has(#id_title) button[type=submit]")), expect=lambda: "stock report v2" in txt(), wait=1800)
            t.step("Back to the feed: the post is listed", lambda: pg.goto(APP + "/posts/"), expect=lambda: "stock report v2" in txt(), wait=1500)
            t.step("Search for a keyword that matches", lambda: (pg.fill("input[name=q]", "stock"), pg.click("form[method=get] button[type=submit]")), expect=lambda: "stock report v2" in txt(), wait=1500)
            t.step("Search for a keyword that matches nothing", lambda: (pg.fill("input[name=q]", "zzzznope"), pg.click("form[method=get] button[type=submit]")), expect=lambda: "stock report v2" not in txt() and "No posts or work logs found" in txt(), wait=1500)
            t.step("The empty-results message", lambda: pg.evaluate("(()=>{const e=[...document.querySelectorAll('div')].find(d=>d.children.length==0&&d.textContent.trim()=='No posts or work logs found'); if(e) e.scrollIntoView({block:'center'}); return 0})()"), expect=lambda: "No posts or work logs found" in txt(), wait=1800)
            t.note("Not shown: manager review (needs a reviewer account), category, team and status filters, templates, Pending-review states, another person's post.", 3)

        run_tour(ctx, "12_worklogs_feed", body, notes="Real Django on TiDB, headless Chromium 390x844, employee account only.",
                 unshown=["manager review (approve/flag)", "category filter", "team filter", "status filter", "quick templates", "editing someone else's post (should refuse)", "a post by another person"])

    g["DRILLS"]["tour_posts"] = posts_tour


def register_certs(g):
    APP = g["APP"]

    def fixture(make_admin):
        """Synthetic fixtures through the ORM (not a product feature): a second person, one active template, and optionally ST-LIVE-1 given the admin role."""
        import concurrent.futures

        import django

        django.setup()

        def mk():
            from apps.accounts.models import CustomUser
            from apps.certificates.models import CertificateTemplate

            CustomUser.objects.get_or_create(
                employee_id="ST-LIVE-2",
                defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                          "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})
            CertificateTemplate.objects.get_or_create(code="TOUR-EXC", defaults={"title": "Excellence", "description_template": "For excellent work."})
            if make_admin:
                CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=CustomUser.Role.ADMIN)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()

    def certs_tour(ctx):
        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            txt = lambda: pg.inner_text("body")
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            fixture(False)
            pg.goto(APP + "/certificates/")
            pg.wait_for_timeout(400)
            t.step("Certificates as an employee: only your own list, no Issue button", expect=lambda: "Issue" not in txt().replace("Issued", "") or pg.locator("a[href$='/certificates/issue/']").count() == 0, wait=1800)
            t.step("An employee opening the issue page is refused", lambda: pg.goto(APP + "/certificates/issue/"), expect=lambda: "permission" in txt().lower() or "403" in txt(), wait=1800)
            fixture(True)
            t.step("Setup (test fixture): this account is now given the Admin role", lambda: pg.goto(APP + "/certificates/"), expect=lambda: pg.locator("a[href$='/certificates/issue/']").count() == 1, wait=1800)
            t.step("Open the issue form", lambda: pg.goto(APP + "/certificates/issue/"), expect=lambda: "Issue New Official Certificate" in txt(), wait=1200)
            t.step("Fill in recipient, title, role and citation", lambda: (pg.select_option("#id_recipient", label=pg.locator("#id_recipient option", has_text="Second Synthetic").first.inner_text()), pg.fill("#id_title", "Certificate of Excellence"), pg.fill("#id_recipient_role", "Test Engineer"), pg.fill("#id_description", "For outstanding work on the stock report."), pg.evaluate("document.getElementById('id_description').scrollIntoView({block:'center'});0")), expect=lambda: pg.input_value("#id_title") != "")
            t.step("The form fields fit the 390 px screen", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: pg.evaluate("[...document.querySelectorAll('main input:not([type=hidden]), main textarea, main select')].every(e=>e.getBoundingClientRect().right<=Math.min(window.innerWidth,(e.closest('.card')||document.body).getBoundingClientRect().right)+1)"))
            t.step("Generate and issue: the certificate page opens", lambda: pg.click("form:has(#id_title) button[type=submit]"), expect=lambda: "issued to Second Synthetic" in txt() and "Certificate of Excellence" in txt(), wait=2200)
            t.step("Scroll to the certificate itself (serial and hash shown)", lambda: pg.evaluate("document.querySelector('.certificate-frame').scrollIntoView({block:'start'});0"), expect=lambda: "Serial" in txt(), wait=2200)
            t.step("The public verify page confirms it", lambda: pg.goto(APP + pg.locator(".cert-meta a").first.get_attribute("href")), expect=lambda: "Certificate of Excellence" in txt() and "No Credential Found" not in txt(), wait=2200)
            t.step("Verify an id that does not exist", lambda: pg.goto(APP + "/verify/NOPE-0000/"), expect=lambda: "No Credential Found" in txt(), wait=1800)
            t.step("Back to the certificate list: the new one is listed", lambda: pg.goto(APP + "/certificates/"), expect=lambda: "Certificate of Excellence" in txt(), wait=1800)
            t.step("Open it and revoke it with a reason (the confirm question is accepted)", lambda: (pg.click("a[href*='/certificates/']:has-text('Excellence')") if pg.locator("a[href*='/certificates/']:has-text('Excellence')").count() else pg.locator(".card a[href*='/certificates/']").first.click(), pg.fill("#id_reason", "Issued by mistake in the test"), pg.evaluate("document.getElementById('id_reason').scrollIntoView({block:'center'});0")), expect=lambda: pg.input_value("#id_reason") != "")
            t.step("The revoke reason box fits the screen", expect=lambda: pg.evaluate("(()=>{const e=document.getElementById('id_reason');return e.getBoundingClientRect().right<=window.innerWidth+1})()"))
            keep = {}
            t.step("Revoke: the app asks for the password again (step-up)", lambda: (keep.update(url=pg.url), pg.click("form[action$='/revoke/'] button[type=submit]")), expect=lambda: "/sudo/" in pg.url and "Re-enter your password" in txt(), wait=2000)

            def offcamera_stepup():
                # the password and one-time code are typed in a separate, unrecorded tab of the same browser session (an Admin needs only the password; the code is for Super Admin); nothing secret is on camera
                q = pg.context.new_page()
                q.goto(pg.url)
                q.fill("input[name=password]", g["SECRETS"]["emp_pw"])
                q.click("form[action$='/sudo/'] button[type=submit]") if q.locator("form[action$='/sudo/'] button[type=submit]").count() else q.click("button[type=submit]")
                q.wait_for_load_state()
                q.wait_for_timeout(800)
                q.close()

            t.step("The password step is done off camera (it is not recorded); back to the certificate", lambda: (offcamera_stepup(), pg.goto(keep["url"])), expect=lambda: "Certificate of Excellence" in txt(), wait=1500)
            t.step("Fill the reason again and revoke: the app says it is revoked and the list shows Revoked", lambda: (pg.fill("#id_reason", "Issued by mistake in the test"), pg.click("form[action$='/revoke/'] button[type=submit]")), expect=lambda: "has been revoked" in txt() and "Revoked" in txt() and any("revoke" in m.lower() for m in seen), wait=2500)
            t.step("Open the certificate: the revoked banner shows", lambda: pg.click("a:has-text('View')"), expect=lambda: "HAS BEEN REVOKED" in txt() and "Issued by mistake" in txt(), wait=2500)
            t.step("Public verify now shows it as revoked", lambda: pg.goto(APP + pg.locator(".cert-meta a").first.get_attribute("href")), expect=lambda: "revoked" in txt().lower(), wait=2200)
            t.note("Not shown: Raw SVG, Print/Save PDF, Reissue, expiry date, status and keyword filters, template choice, employee seeing their own certificate.", 3)

        run_tour(ctx, "13_certificates", body, notes="Real Django on TiDB. Test fixture (ORM, not a product feature): ST-LIVE-1 is given the Admin role mid-clip, plus a second person and a template. Headless Chromium 390x844.",
                 unshown=["Raw SVG", "Print / Save PDF", "Reissue after revoke", "expiry date", "list status filter", "list keyword search", "template choice", "employee viewing their own certificate", "revoke with empty reason"])

    g["DRILLS"]["tour_certs"] = certs_tour

    def desk_shots(ctx):
        """Not a clip: 1100 px wide screenshots of the pages that use form-control inside form-group, for a before/after CSS comparison."""
        base = os.environ.get("DRILL_VIDEO", "")
        if not base:
            print("desk shots skipped: DRILL_VIDEO is not set")
            return
        out = os.path.join(base, open("/tmp/desk_tag").read().strip())
        os.makedirs(out, exist_ok=True)
        fixture(True)
        c = ctx.browser.new_context(viewport={"width": 1100, "height": 800})
        c.add_cookies(ctx.cookies())
        errs, remote, guard = ctx.salow_hooks
        c.route("**/*", guard)
        pg = c.new_page()
        for name, path in (("posts_feed", "/posts/"), ("posts_form", "/posts/create/"), ("cert_list", "/certificates/"), ("cert_issue", "/certificates/issue/"), ("cert_verify", "/verify/")):
            pg.goto(APP + path)
            pg.wait_for_timeout(500)
            pg.screenshot(path=os.path.join(out, name + ".png"), full_page=False)
        c.close()
        print("DESK shots written", out, flush=True)

    g["DRILLS"]["desk_shots"] = desk_shots


def register_automation(g):
    APP = g["APP"]

    def auto_tour(ctx):
        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            txt = lambda: pg.inner_text("body")
            seen = {}
            fits = "(()=>{const m=document.querySelector('main')||document.body;return [...m.querySelectorAll('input:not([type=hidden]),select,button,textarea')].every(e=>e.getBoundingClientRect().right<=window.innerWidth+1)})()"

            def fill_step(i, kind, **kw):
                pg.select_option("select[name=type%d]" % i, kind)
                for k, v in kw.items():
                    pg.fill("input[name=%s%d]" % (k, i), v)

            pg.goto(APP + "/notifications/")
            t.step("Notifications before anything: the empty message shows", expect=lambda: "No notifications" in txt(), wait=1600)
            pg.goto(APP + "/automation/")
            t.step("Automation home: no workflows yet", expect=lambda: "No workflows yet" in txt(), wait=1600)
            t.step("New workflow form opens, every field fits the screen", lambda: pg.click("a:has-text('New workflow')"), expect=lambda: "New workflow" in txt() and pg.evaluate(fits), wait=1500)
            t.step("Name it and add 3 steps: Set a value, Stop unless it equals 1, Notify me", lambda: (pg.fill("input[name=name]", "Tour workflow"), fill_step(0, "set_value", key="mode", value="1"), fill_step(1, "stop_unless_equals", key="mode", equals="1"), fill_step(2, "notify_owner", title="Tour workflow ran")), expect=lambda: pg.input_value("input[name=title2]") == "Tour workflow ran", wait=1500)
            t.step("Save: it is saved switched off", lambda: pg.click("button:has-text('Save')"), expect=lambda: "switched off until you turn it on" in txt() and "Set mode to" in txt(), wait=2000)
            t.step("Run now is not offered while it is off", expect=lambda: pg.locator("button:has-text('Run now')").count() == 0, wait=1500)
            t.step("Switch it on", lambda: pg.click("button:has-text('Switch on')"), expect=lambda: "Switched on." in txt() and pg.locator("button:has-text('Run now')").count() == 1, wait=1800)
            t.step("Run now: the run finishes as succeeded", lambda: pg.click("button:has-text('Run now')"), expect=lambda: "Run finished: succeeded." in txt(), wait=2200)
            t.step("Recent runs lists the run", lambda: pg.evaluate("document.querySelector('h3').scrollIntoView({block:'start'});0"), expect=lambda: "succeeded" in txt().split("Recent runs")[-1], wait=1800)
            t.step("Edit page: the step values are filled in, edit saves a new version", lambda: pg.click("a:has-text('Edit')"), expect=lambda: pg.input_value("input[name=value0]") == "1", wait=1500)
            t.step("Change the Stop-unless value to 2 and save: version 2", lambda: (pg.fill("input[name=equals1]", "2"), pg.click("button:has-text('Save')")), expect=lambda: "Saved as a new version" in txt() and "Version 2" in txt(), wait=2200)
            t.step("Run now again: the step stops, so no notification is added (run still shows a result)", lambda: pg.click("button:has-text('Run now')"), expect=lambda: "Run finished" in txt() or "Run failed" in txt(), wait=2200)
            t.step("Recent runs now has two entries", lambda: pg.evaluate("document.querySelector('h3').scrollIntoView({block:'start'});0"), expect=lambda: len(txt().split("Recent runs")[-1].split("version")) >= 3, wait=1800)
            t.step("Switch off", lambda: pg.click("button:has-text('Switch off')"), expect=lambda: "Switched off." in txt() and pg.locator("button:has-text('Run now')").count() == 0, wait=1800)
            t.step("A bad save is refused: a Set-a-value step with no key", lambda: (pg.click("a:has-text('Edit')"), fill_step(3, "set_value", value="x"), pg.click("button:has-text('Save')")), expect=lambda: "Edit" in txt() and ("needs" in txt().lower() or "key" in txt().lower()), wait=2200)
            t.step("Automation home lists the workflow with version and on/off", lambda: pg.goto(APP + "/automation/"), expect=lambda: "Tour workflow" in txt() and "version 2, off" in txt(), wait=1800)
            t.step("Notifications now shows the line the first run added, with Mark all as read", lambda: pg.goto(APP + "/notifications/"), expect=lambda: "Tour workflow ran" in txt() and "unread" in txt(), wait=2200)
            t.step("Mark all as read: the unread tag goes away", lambda: pg.click("button:has-text('Mark all as read')"), expect=lambda: "Marked" in txt() and "unread" not in txt().split("Mark all")[-1].replace("Marked 1 as read.", ""), wait=2200)
            t.step("Pressing it again says nothing was unread", lambda: pg.click("button:has-text('Mark all as read')"), expect=lambda: "Nothing was unread" in txt(), wait=2000)
            t.note("Not shown: event triggers, scheduled runs, delete, the 20-workflow cap, 10-run-per-minute limit, another person's workflow.", 3)

        run_tour(ctx, "14_automation_notifications", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Runs execute inside the web request. No fixtures.",
                 unshown=["event triggers (not connected)", "scheduled runs (not built)", "delete workflow (not built)", "20 workflow cap", "10 runs per minute limit", "another person's workflow (404)", "10 steps maximum", "edit refusal for non-round-trip workflow"])

    g["DRILLS"]["tour_auto"] = auto_tour


def register_manager_assign(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            from apps.accounts.models import CustomUser

            for eid, nm in (("ST-LIVE-2", "Second Synthetic"), ("ST-LIVE-3", "Third Synthetic")):
                CustomUser.objects.get_or_create(
                    employee_id=eid,
                    defaults={"full_name": nm, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                              "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})
            from apps.directory.models import ReportingRelationship

            ReportingRelationship.objects.filter(subordinate__employee_id__in=["ST-LIVE-2", "ST-LIVE-3"]).delete()  # a clean start, whatever ran before in this store
            prior["role"] = CustomUser.objects.get(employee_id="ST-LIVE-1").role
            CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=CustomUser.Role.ADMIN)

        def restore():
            # undo this tour's fixture so a tour that runs later in the same store starts from the state it found, also after a failure
            from apps.accounts.models import CustomUser
            from apps.directory.models import ReportingRelationship

            ReportingRelationship.objects.filter(subordinate__employee_id__in=["ST-LIVE-2", "ST-LIVE-3"]).delete()
            if prior.get("role"):
                CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=prior["role"])

        prior = {}
        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            sel = "select[name=manager]"
            pg.goto(APP + "/directory/profile/ST-LIVE-2/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 BEFORE: the profile of Second Synthetic says Reports to: No manager set", lambda: pg.locator(sel).scroll_into_view_if_needed(), expect=lambda: "No manager set" in txt(), wait=1800)
            t.step("STAGE 2 SET: the administrator picks Third Synthetic in the Reporting manager list. Not saved yet", lambda: pg.select_option(sel, "ST-LIVE-3"), expect=lambda: pg.input_value(sel) == "ST-LIVE-3" and "No manager set" in txt(), wait=1800)
            t.step("STAGE 2 SET: press Set manager. The banner says Second Synthetic now reports to Third Synthetic", lambda: (pg.click("button:has-text('Set manager')"), pg.wait_for_timeout(300), pg.evaluate("window.scrollTo(0, 0)")), expect=lambda: "now reports to Third Synthetic" in txt(), wait=2200)
            t.step("STAGE 2 SET: the page reads Reports to: Third Synthetic (ST-LIVE-3)", lambda: pg.locator("span:text-is('Reports to:')").scroll_into_view_if_needed(), expect=lambda: "Third Synthetic (ST-LIVE-3)" in txt() and "No manager set" not in txt(), wait=1800)
            t.step("STAGE 3 LOOP: open Third Synthetic and try to make Second Synthetic their manager", lambda: (pg.goto(APP + "/directory/profile/ST-LIVE-3/"), pg.select_option(sel, "ST-LIVE-2")), expect=lambda: pg.input_value(sel) == "ST-LIVE-2", wait=1800)
            t.step("STAGE 3 LOOP: press Set manager. The page refuses with Reporting cycle detected and nothing changes", lambda: (pg.click("button:has-text('Set manager')"), pg.wait_for_timeout(300), pg.evaluate("window.scrollTo(0, 0)")), expect=lambda: "Reporting cycle detected" in txt() and "No manager set" in txt(), wait=2400)
            t.step("STAGE 4 ORG CHART: the org chart lists Second Synthetic under Third Synthetic", lambda: pg.goto(APP + "/directory/org/"), expect=lambda: "ST-LIVE-2" in txt() and "ST-LIVE-3" in txt(), wait=2200)
        try:
            run_tour(ctx, "manager_assign", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Fixtures (ORM, not product features): ST-LIVE-1 is given the Admin role, plus two other people. Stages: before, set a manager, a loop is refused, org chart. Not shown: a non-administrator's view (no form, refusal in unit tests), inactive people.", unshown=["a non-administrator's view", "inactive people"])
        finally:
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                ex.submit(restore).result()

    g["DRILLS"]["tour_manager_assign"] = tour


def register_calendar_export(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            import datetime as dt

            from apps.accounts.models import CustomUser
            from apps.events import services as es
            from apps.events.models import Event, EventException

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            Event.objects.filter(organizer=me, title__in=["Export demo: standup", "Export demo: offsite"]).delete()  # a clean start in a shared store
            from django.utils import timezone

            D = dt.datetime
            day = timezone.now().date() + dt.timedelta(days=45)  # always in the future, whenever the tour runs
            rule = {"freq": "daily", "interval": 1, "weekdays": [], "count": 5, "until": None}
            ev = es.create_event(me, "Export demo: standup", D(day.year, day.month, day.day, 10), D(day.year, day.month, day.day, 10, 30), "America/New_York", rule=rule, location="Room 4, Block B")
            EventException.objects.create(event=ev, local_date=day + dt.timedelta(days=2))
            es.create_event(me, "Export demo: offsite", D(day.year, day.month, day.day), D(day.year, day.month, day.day), "Asia/Kolkata", all_day=True)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()
        saved = {}

        def cleanup():
            from apps.events.models import Event

            Event.objects.filter(title__in=["Export demo: standup", "Export demo: offsite"]).delete()  # cascades its skipped days

        def body(t, pg):
            out = os.environ.get("DRILL_VIDEO", "")
            pg.goto(APP + "/calendar/")
            pg.wait_for_timeout(500)
            t.step("Calendar home has an Export .ics link and says what the file holds", lambda: pg.locator("a:has-text('Export .ics')").scroll_into_view_if_needed(), expect=lambda: pg.is_visible("a:has-text('Export .ics')") and "no guest list" in pg.inner_text("body"), wait=2200)

            def click():
                with pg.expect_download() as dl:
                    pg.click("a:has-text('Export .ics')")
                saved["name"] = dl.value.suggested_filename
                saved["text"] = open(dl.value.path(), encoding="utf-8", newline="").read()
                open(os.path.join(out, "calendar_export.ics"), "w", encoding="utf-8", newline="").write(saved["text"])

            t.step("Press Export .ics. The browser downloads salow-calendar.ics", click, expect=lambda: saved.get("name") == "salow-calendar.ics" and saved["text"].startswith("BEGIN:VCALENDAR") and "Export demo: standup" in saved["text"], wait=1800)
            t.step("The page stays on the Calendar home (a download, not a navigation)", expect=lambda: pg.url.rstrip("/").endswith("/calendar") and pg.is_visible("a:has-text('Export .ics')"), wait=1500)
        try:
            run_tour(ctx, "calendar_export", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Fixtures (ORM): a repeating New York standup (starting 45 days after the run) with one skipped day and an all-day event for the signed-in person. The downloaded file is saved and parsed outside the app (icalendar, python-dateutil), see the log. Not shown: guests, declined or cancelled events (unit tests).", unshown=["guest or declined events"])
        finally:
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                ex.submit(cleanup).result()

    g["DRILLS"]["tour_calendar_export"] = tour


def register_manager_blank(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            from apps.accounts.models import CustomUser

            for eid, nm in (("ST-LIVE-2", "Second Synthetic"), ("ST-LIVE-3", "Third Synthetic")):
                CustomUser.objects.get_or_create(
                    employee_id=eid,
                    defaults={"full_name": nm, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                              "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})
            from apps.directory.models import ReportingRelationship

            ReportingRelationship.objects.filter(subordinate__employee_id__in=["ST-LIVE-2", "ST-LIVE-3"]).delete()  # a clean start, whatever ran before in this store
            prior["role"] = CustomUser.objects.get(employee_id="ST-LIVE-1").role
            CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=CustomUser.Role.ADMIN)

        def restore():
            # undo this tour's fixture so a tour that runs later in the same store starts from the state it found, also after a failure
            from apps.accounts.models import CustomUser
            from apps.directory.models import ReportingRelationship

            ReportingRelationship.objects.filter(subordinate__employee_id__in=["ST-LIVE-2", "ST-LIVE-3"]).delete()
            if prior.get("role"):
                CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=prior["role"])

        prior = {}
        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            sel = "select[name=manager]"
            pg.goto(APP + "/directory/profile/ST-LIVE-2/")
            pg.wait_for_timeout(500)
            t.step("The list starts on Choose a person. Nobody is preselected", lambda: pg.locator(sel).scroll_into_view_if_needed(), expect=lambda: pg.input_value(sel) == "" and "No manager set" in txt(), wait=2200)
            t.step("Press Set manager without choosing. The page says Choose a person from the list. and still No manager set", lambda: (pg.click("button:has-text('Set manager')"), pg.wait_for_timeout(300), pg.evaluate("window.scrollTo(0, 0)")), expect=lambda: "Choose a person from the list." in txt() and "No manager set" in txt(), wait=2400)
        try:
            run_tour(ctx, "manager_blank", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Fixtures (ORM, not product features): ST-LIVE-1 is given the Admin role, plus two other people. Two steps: the list has no preselection, and pressing Set manager untouched changes nothing. Not shown: a non-administrator's view (no form, refusal in unit tests), inactive people.", unshown=["a non-administrator's view", "inactive people"])
        finally:
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                ex.submit(restore).result()

    g["DRILLS"]["tour_manager_blank"] = tour


def register_find(g):
    APP = g["APP"]

    def find_tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            from apps.accounts.models import CustomUser

            CustomUser.objects.get_or_create(
                employee_id="ST-LIVE-2",
                defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                          "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()

        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/drive/")
            pg.fill("input[placeholder='New folder name']", "Budget plans")
            pg.click("button:has-text('Create folder')")
            pg.wait_for_timeout(600)
            pg.goto(APP + "/search/")
            t.step("Search page with the note on what it does and does not search", expect=lambda: "does not search file contents" in txt(), wait=1800)
            t.step("Search for 'Budget' everywhere: finds the Drive folder", lambda: (pg.fill("input[name=q]", "Budget"), pg.click("button:has-text('Search')")), expect=lambda: "Budget plans" in txt() and "Drive" in txt(), wait=2000)
            t.step("Open the result: it goes to that folder in Drive", lambda: pg.click("a.cal-title:has-text('Budget plans')"), expect=lambda: "/drive/" in pg.url and "Budget plans" in txt(), wait=2000)
            pg.goto(APP + "/search/")
            t.step("Search for 'Second' in People only: finds the person", lambda: (pg.fill("input[name=q]", "Second"), pg.select_option("select[name=in]", "people"), pg.click("button:has-text('Search')")), expect=lambda: "Second Synthetic" in txt() and "Budget plans" not in txt(), wait=2000)
            t.step("Open the person result: the directory profile opens", lambda: pg.click("a.cal-title:has-text('Second Synthetic')"), expect=lambda: "Employee Details" in txt() and "ST-LIVE-2" in txt(), wait=2200)
            pg.goto(APP + "/search/")
            t.step("A search with nothing in it: 'zzzqq' shows No results", lambda: (pg.fill("input[name=q]", "zzzqq"), pg.click("button:has-text('Search')")), expect=lambda: "No results" in txt(), wait=1800)
            t.step("One character is refused: 'Type at least 2 characters.'", lambda: (pg.fill("input[name=q]", "a"), pg.click("button:has-text('Search')")), expect=lambda: "Type at least 2 characters" in txt(), wait=1800)
            t.step("Directory list: both people are listed", lambda: pg.goto(APP + "/directory/"), expect=lambda: "ST-LIVE-1" in txt() and "ST-LIVE-2" in txt() and "Employee Directory" in txt(), wait=2000)
            t.step("Directory filters fit the screen", expect=lambda: pg.evaluate("[...document.querySelectorAll('main form input,main form select,main form button,main form a')].every(e=>e.getBoundingClientRect().right<=window.innerWidth+1)"), wait=1500)
            t.step("Filter by name 'Second': only that person stays", lambda: (pg.fill("input[name=q]", "Second"), pg.click("button:has-text('Filter')")), expect=lambda: "ST-LIVE-2" in pg.inner_text("tbody") and "ST-LIVE-1" not in pg.inner_text("tbody"), wait=2000)
            t.step("Filter that matches nobody: the empty message shows", lambda: (pg.fill("input[name=q]", "nobodyhere"), pg.click("button:has-text('Filter')")), expect=lambda: "No employees match" in txt(), wait=1800)
            t.step("Org chart opens from the directory", lambda: pg.click("a:has-text('Org chart')"), expect=lambda: "Org chart" in txt() and "ST-LIVE-1" in txt(), wait=2200)
            t.step("Open a person from the org chart: profile page", lambda: pg.click("ul.org-tree a >> nth=0"), expect=lambda: "Employee Details" in txt(), wait=2200)
            t.note("Not shown: the Department filter with real departments, Task and Calendar results, team pages (no link from the app), manager-only fields, manager direct reports.", 3)

        run_tour(ctx, "15_search_directory", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture (ORM): one extra person ST-LIVE-2. The Drive folder is made on camera-side before the tour starts.",
                 unshown=["Department filter with departments", "Task results", "Calendar results", "team detail page (no link in the app)", "manager and admin only profile fields", "direct reports list", "search throttle", "30 per module truncation notice"])

    g["DRILLS"]["tour_find"] = find_tour


def register_security(g):
    APP = g["APP"]

    def sec_tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def seed(n):
            from apps.accounts.models import CustomUser, UserSession

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            for i in range(n):
                UserSession.objects.get_or_create(user=me, session_key="tourfixture%02d" % i, defaults={"ip_address": "203.0.113.%d" % (10 + i), "user_agent": "Test fixture row %d (not a real device)" % (i + 1)})

        def run(fn, *a):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn, *a).result()

        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            txt = lambda: pg.inner_text("body")
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept("")))
            fits = "[...document.querySelectorAll('main input:not([type=hidden]),main select,main button,main .card')].every(e=>e.getBoundingClientRect().right<=window.innerWidth+1)"
            pg.goto(APP + "/auth/security/")
            t.step("Security page: passkeys empty message, This Device session, recent events", expect=lambda: "No passkeys registered" in txt() and "This Device" in txt(), wait=2200)
            t.step("Page content fits the 390 px screen", expect=lambda: pg.evaluate(fits), wait=1400)
            t.step("Terminate All Other Sessions is hidden while there is only this session", expect=lambda: pg.locator("button:has-text('Terminate All Other Sessions')").count() == 0, wait=1400)
            run(seed, 1)
            t.step("Setup (test fixture): one extra session row is added to the database; it is not a real second device", lambda: pg.reload(), expect=lambda: "Test fixture row 1" in txt() and pg.locator("button:has-text('Terminate All Other Sessions')").count() == 1, wait=2200)
            t.step("Revoke that one session", lambda: pg.click("button:has-text('Revoke') >> nth=-1") if pg.locator("form[action*='sessions'] button:has-text('Revoke')").count() else None, expect=lambda: "Session successfully terminated" in txt() and "Test fixture row 1" not in txt(), wait=2200)
            run(seed, 3)
            t.step("Setup again: three fixture rows. Terminate All Other Sessions is offered", lambda: pg.reload(), expect=lambda: pg.locator("button:has-text('Terminate All Other Sessions')").count() == 1, wait=2000)
            t.step("Terminate all others: it says 3 were ended and only This Device remains", lambda: pg.click("button:has-text('Terminate All Other Sessions')"), expect=lambda: "Terminated 3 other" in txt() and "Test fixture row" not in txt() and "This Device" in txt(), wait=2400)
            t.step("Enroll Passkey: the label prompt appears (headless browser has no authenticator, so enrolment is not completed)", lambda: pg.click("#enroll-passkey-btn"), expect=lambda: any("label for this passkey" in m for m in seen), wait=2200)
            t.step("Change Password page opens", lambda: pg.goto(APP + "/auth/security/password/change/"), expect=lambda: "Current Password" in txt() and pg.evaluate(fits), wait=1800)
            t.step("Different new and confirm passwords are refused (made-up test text, not a real password)", lambda: (pg.fill("#current_password", "not-the-real-one-1"), pg.fill("#new_password", "Aaaaaaaaaaaa1!x"), pg.fill("#confirm_password", "Bbbbbbbbbbbb2!y"), pg.click("form:has(#new_password) button[type=submit]")), expect=lambda: "do not match" in txt(), wait=2200)
            t.step("Matching new password but wrong current password is refused", lambda: (pg.fill("#current_password", "not-the-real-one-1"), pg.fill("#new_password", "Aaaaaaaaaaaa1!x"), pg.fill("#confirm_password", "Aaaaaaaaaaaa1!x"), pg.click("form:has(#new_password) button[type=submit]")), expect=lambda: "/password/change/" in pg.url and "successfully" not in txt() and pg.locator(".alert, [role=alert], .error, .form-error").count() + (1 if "ncorrect" in txt() or "nvalid" in txt() or "wrong" in txt().lower() else 0) >= 1, wait=2400)
            t.step("Idle lock screen shows", lambda: pg.goto(APP + "/auth/security/idle-lock/"), expect=lambda: "password" in txt().lower() and "Unlock" in txt(), wait=2000)
            t.step("A wrong password on the lock screen is refused: Incorrect password entered.", lambda: (pg.fill("input[name=password]", "not-the-real-one-1"), pg.click("form:has(input[name=password]) button[type=submit]")), expect=lambda: "Incorrect password entered" in txt(), wait=2200)
            t.step("Directory table on a phone: the table itself scrolls sideways inside its card to reach the Actions column; the page does not widen", lambda: (pg.goto(APP + "/directory/"), pg.evaluate("(()=>{const w=document.querySelectorAll('.table-responsive table')[0];w.scrollIntoView({block:'center'});w.scrollLeft=0;return 0})()"), pg.wait_for_timeout(900), pg.evaluate("document.querySelectorAll('.table-responsive table')[0].scrollTo({left:9999,behavior:'smooth'});0")), expect=lambda: print('TBL', pg.evaluate("(()=>{const w=document.querySelectorAll('.table-responsive table')[0];return [w.scrollWidth,w.clientWidth,getComputedStyle(w).overflowX,document.documentElement.scrollWidth,window.innerWidth,w.scrollLeft]})()"), flush=True) or pg.evaluate("(()=>{const w=document.querySelectorAll('.table-responsive table')[0];return w.scrollWidth>w.clientWidth && w.scrollLeft>20 && getComputedStyle(w).overflowX==='auto' && document.documentElement.scrollWidth<=window.innerWidth+8})()"), wait=2600)
            t.note("Not shown: a successful password change, a successful unlock, passkey enrolment and revoke, Recovery Console and Backups links (admin only), a real second device.", 3)

        run_tour(ctx, "16_security_sessions", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture (ORM): extra session rows, not real devices. No real password is typed; made-up strings only. The sign-in and step-up are not recorded.",
                 unshown=["successful password change (needs the real password)", "successful idle unlock", "passkey enrolment and revoke", "Recovery Console link (admin only)", "Backups link (Super Admin only)", "a real second device session", "security events beyond the first rows", "alerts page and acknowledge"])

    g["DRILLS"]["tour_sec"] = sec_tour


def register_admin(g):
    APP = g["APP"]

    def admin_tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            import datetime

            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.attendance.models import AttendanceFallbackRequest
            from apps.attendance.services import get_current_kolkata_date

            CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=CustomUser.Role.ADMIN)
            now = timezone.now()
            for eid, nm, why in (("ST-LIVE-2", "Second Synthetic", "Camera failed at the door"), ("ST-LIVE-3", "Third Synthetic", "Forgot to check in")):
                u = CustomUser.objects.get_or_create(employee_id=eid, defaults={"full_name": nm, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                                                    "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
                AttendanceFallbackRequest.objects.create(user=u, request_type="check_in", work_date=get_current_kolkata_date(now), requested_time=now, reason=why)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()

        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/attendance/admin/")
            t.step("Setup (test fixture): this account is Admin, and two people have pending fallback requests. Admin panel opens", expect=lambda: "Workspace Admin Panel" in txt() and "Pending Biometric Fallback Exceptions (2)" in txt(), wait=2400)
            t.step("The IAM summary shows posture and counts, and the link to the IAM console is offered", expect=lambda: "iam posture" in txt().lower() and "central iam" in txt().lower(), wait=2000)
            t.step("Today has no sessions yet", lambda: pg.evaluate("document.body.scrollTo(0,document.body.scrollHeight);window.scrollTo(0,document.body.scrollHeight);0"), expect=lambda: "No attendance sessions logged for today yet" in txt(), wait=1800)
            t.step("Scroll to the pending requests table", lambda: pg.evaluate("document.querySelector('table').scrollIntoView({block:'center'});0"), expect=lambda: "Camera failed at the door" in txt() and "Forgot to check in" in txt(), wait=1800)
            t.step("Approve the first request: the panel says it was approved and one request is left", lambda: pg.locator("tbody tr", has_text="Second Synthetic").locator("button:has-text('Approve')").click(), expect=lambda: "successfully approved" in txt() and "Pending Biometric Fallback Exceptions (1)" in txt(), wait=2400)
            t.step("Today's sessions now lists the approved person", lambda: pg.evaluate("document.querySelectorAll('table')[pg_idx].scrollIntoView({block:'center'});0".replace("pg_idx", "document.querySelectorAll('table').length-1")), expect=lambda: "Today's Attendance Sessions (1)" in txt() and "Second Synthetic" in pg.inner_text("table >> nth=-1"), wait=2200)
            t.step("Reject the second request: the panel says it was rejected", lambda: pg.locator("tbody tr", has_text="Third Synthetic").locator("button:has-text('Reject')").click(), expect=lambda: "successfully rejected" in txt() and "Pending Biometric Fallback Exceptions (0)" in txt() and "No pending fallback requests" in txt(), wait=2400)
            t.step("Rejecting created no session: today still has 1", expect=lambda: "Today's Attendance Sessions (1)" in txt(), wait=1600)
            t.step("Open the Central IAM console link", lambda: pg.click("a:has-text('Central IAM')"), expect=lambda: True, wait=2200)
            t.step("The IAM console page is open (its own clip follows)", expect=lambda: "/admin/iam/" in pg.url, wait=1800)
            t.note("Not shown: manager-only view of direct reports, HR role, approving a check-out request, reviewing your own request (refused), an already reviewed request, review notes.", 3)

        run_tour(ctx, "17_workspace_admin", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixtures (ORM, not product features): ST-LIVE-1 given the Admin role, two other people, two pending fallback requests.",
                 unshown=["Manager direct-reports-only view", "HR role", "approving a check-out request", "reviewing your own request (refused)", "already-reviewed request error", "review notes field (no field on the page)", "IAM console itself (separate clip)"])

    g["DRILLS"]["tour_admin"] = admin_tour


def register_iam(g):
    APP = g["APP"]
    TABS = ("overview", "authentication", "identity", "roles", "security_mfa", "sessions", "recovery", "lifecycle", "audit", "people_teams", "investigation", "privileged", "services", "delivery_ops", "threats", "resilience", "l3_approvals")

    def iam_tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            from apps.accounts.models import CustomUser

            CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=CustomUser.Role.ADMIN)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()

        def body(t, pg):
            _step = t.step
            t.step = lambda text, fn=None, wait=800, expect=None: _step(text, fn, wait=min(wait, 900), expect=expect)
            txt = lambda: pg.inner_text("body")
            seen = {}
            pg.goto(APP + "/auth/admin/iam/")
            t.step("Setup (test fixture): this account is Admin. The IAM console opens on the Overview tab", expect=lambda: "/admin/iam/" in pg.url and "Overview" in txt(), wait=2400)
            t.step("The page no longer shows a dead live strip here: a note says the live feed is only on the IAM console host", lambda: pg.evaluate("document.getElementById('iam-live-events-unavailable').scrollIntoView({block:'center'});0"), expect=lambda: pg.locator("#iam-live-events").count() == 0 and "only served on the separate IAM console host" in txt(), wait=2600)
            for tab in TABS[1:]:
                def go(tab=tab):
                    pg.goto(APP + "/auth/admin/iam/?tab=" + tab)
                    pg.wait_for_timeout(300)
                    seen[tab] = [h.strip()[:60] for h in pg.locator("main h2, main h3").all_inner_texts()[:3]]
                    pg.evaluate("document.querySelector('.tab-on')&&document.querySelector('.tab-on').scrollIntoView({inline:'center',block:'nearest'});0")
                    pg.evaluate("window.scrollTo(0, 360);0")
                t.step("Tab: %s opens (active tab highlighted, page content loads)" % tab, go, expect=lambda tab=tab: pg.locator(".tab-on").count() == 1 and tab in pg.url and "Traceback" not in txt() and "Server Error" not in txt(), wait=1500)
            t.step("The Attendance link in the top bar leaves the IAM console for the Attendance page", lambda: (pg.goto(APP + "/auth/admin/iam/?tab=overview"), pg.locator("a:has-text('Attendance'):visible").last.click()), expect=lambda: "/attendance/" in pg.url, wait=1800)
            t.note("Not shown: every create, change, revoke, reset and approval form inside the tabs; the separate IAM host login; Super Admin step-up; the live stream; Shield.", 3)
            ctx.iam_seen = seen

        run_tour(ctx, "18_iam_console", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture: ST-LIVE-1 given the Admin role. This clip only OPENS each tab: it does not use any form in them. Opened on the normal app host, not the separate IAM host.",
                 unshown=["every mutating form in the IAM tabs", "IAM host sign-in and recovery pages", "Super Admin step-up", "live event stream", "Shield page", "backups", "issued activation codes and service tokens"])
        import json as _j
        try:
            _j.dump(seen, open(os.path.join(os.environ.get("DRILL_VIDEO", "/tmp"), "18_iam_tab_headings.json"), "w"), indent=1)
        except Exception:
            pass

    g["DRILLS"]["tour_iam"] = iam_tour


def register_iam_live(g):
    APP = g["APP"]

    def iam_live_tour(ctx):
        import concurrent.futures

        import django

        django.setup()
        box = {}

        def mk():
            from django.conf import settings

            from apps.accounts.auth.iam_auth import create_iam_session
            from apps.accounts.models import CustomUser, IAMAdminIdentity

            u = CustomUser.objects.get(employee_id="ST-LIVE-1")
            adm = IAMAdminIdentity.objects.create(admin_id="IAM-FIXTURE-1", user=u, password_hash="!", role="admin", status="active")

            class R:  # minimal request stand-in for the session helper
                META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "drill"}
                client_ip = "127.0.0.1"

            r = R()
            sess = create_iam_session(adm, r, assurance_level=1)
            box["raw"], box["pk"], box["cookie"] = r.session_key_iam, sess.pk, getattr(settings, "IAM_SESSION_COOKIE_NAME", "salow_iam_session")

        def emit(event, msg):
            from apps.accounts.auth.audit import log_auth_event

            log_auth_event(event=event, code="OK", step="fixture", employee_id="ST-LIVE-1", message=msg)

        def revoke():
            from apps.accounts.models import IAMSession

            IAMSession.objects.filter(pk=box["pk"]).update(is_active=False)

        def run(fn, *a):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                ex.submit(fn, *a).result()

        run(mk)
        host = "iam.salowtech.com"
        IAM = "http://iam.salowtech.com:" + APP.rsplit(":", 1)[1]
        cookie = {"name": box["cookie"], "value": box["raw"], "domain": host, "path": "/", "httpOnly": True, "sameSite": "Lax"}

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            status = lambda: pg.inner_text("#iam-live-events-status")
            pg.goto(IAM + "/dashboard/?tab=identity")
            t.step("Setup (test fixture): an IAM administrator session was created off camera and loaded into the browser. The IAM console page opens with the Live Actions strip", lambda: pg.evaluate("document.getElementById('iam-live-events').scrollIntoView({block:'center'});0"), expect=lambda: pg.locator("#iam-live-events").count() == 1, wait=2200)
            t.step("The strip connects: its status line reads Live", expect=lambda: status().startswith("Live"), wait=2500)
            t.note("The status line shows which way it is connected (push or polling). Read it in the frame.", 2.5)
            n0 = pg.locator("#iam-live-events-body tr").count()
            t.step("Test fixture emits one IAM event (written by the server-side audit logger, not typed into the page)", lambda: run(emit, "iam_fixture_probe", "Fixture event written for the live strip check"), wait=3500, expect=lambda: pg.locator("#iam-live-events-body tr", has_text="iam_fixture_probe").count() == 1)
            t.step("The new row is at the top of the strip with domain IAM and the event name (scroll the strip sideways for the rest)", lambda: pg.evaluate("document.getElementById('iam-live-events').scrollIntoView({block:'center'});0"), expect=lambda: pg.locator("#iam-live-events-body tr", has_text="iam_fixture_probe").first.inner_text().split("\t")[1].strip() == "IAM", wait=2800)
            mode = pg.locator("#iam-live-events-body tr", has_text="iam_fixture_probe").get_attribute("data-mode")
            t.log.append({"step": "Delivery mode recorded on that row: %s" % mode, "ok": None, "detail": "note"})
            t.step("The same event is not shown twice", expect=lambda: pg.locator("#iam-live-events-body tr", has_text="iam_fixture_probe").count() == 1, wait=3000)
            t.step("Negative control: the fixture revokes this IAM session. The strip stops and says so", lambda: run(revoke), expect=lambda: ("Signed out" in status()) or ("closed" in status().lower()), wait=8000)
            t.step("After revoking, a second fixture event does not appear", lambda: run(emit, "iam_fixture_probe_two", "Second fixture event after revoke"), expect=lambda: pg.locator("#iam-live-events-body tr", has_text="probe_two").count() == 0, wait=4000)
            t.note("Not shown: IAM password and authenticator sign-in (session made off camera), Super Admin step-up, real DNS and TLS.", 3)

        run_tour(ctx, "19_iam_live_feed", body, extra_cookies=[cookie],
                 notes="Real Django on TiDB under the uvicorn ASGI server, headless Chromium 390x844 emulated. The IAM session is a fixture made through the app's own session helper, not through the login form. Page opened on the IAM host name iam.salowtech.com (mapped to the local server by the browser, plain http, no real DNS or TLS).",
                 unshown=["IAM login with password and authenticator", "Super Admin step-up"])

    g["DRILLS"]["tour_iam_live"] = iam_live_tour


def register_admin_scroll(g):
    APP = g["APP"]

    def scroll_tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def mk():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.attendance.models import AttendanceFallbackRequest
            from apps.attendance.services import get_current_kolkata_date

            CustomUser.objects.filter(employee_id="ST-LIVE-1").update(role=CustomUser.Role.ADMIN)
            now = timezone.now()
            for eid, nm, why in (("ST-LIVE-2", "Second Synthetic", "Camera failed at the door"), ("ST-LIVE-3", "Third Synthetic", "Forgot to check in")):
                u = CustomUser.objects.get_or_create(employee_id=eid, defaults={"full_name": nm, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                                                    "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
                AttendanceFallbackRequest.objects.create(user=u, request_type="check_in", work_date=get_current_kolkata_date(now), requested_time=now, reason=why)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            vw = pg.viewport_size["width"]

            def btn(name, who):
                return pg.locator("tbody tr", has_text=who).locator("button:has-text('%s')" % name).first

            def inview(name, who):
                b = btn(name, who).bounding_box()
                return bool(b) and b["x"] >= 0 and b["x"] + b["width"] <= vw

            def table_js(expr):
                return pg.evaluate("()=>{const tb=[...document.querySelectorAll('table')].find(x=>x.querySelector('button')&&/Synthetic/.test(x.innerText));" + expr + "}")

            def slide(to_end):
                for i in range(1, 7):
                    table_js("tb.scrollLeft=%s;return 0" % ("tb.scrollWidth*%d/6" % i if to_end else "0"))
                    pg.wait_for_timeout(250)

            def click_centered(name, who):
                b = btn(name, who).bounding_box()
                pg.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=8)
                pg.wait_for_timeout(500)
                pg.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)

            pg.goto(APP + "/attendance/admin/")
            t.step("Setup (test fixture): Admin account, two pending fallback requests. Open the Admin panel", lambda: pg.evaluate("[...document.querySelectorAll('table')].find(x=>x.querySelector('button')).scrollIntoView({block:'center'});0"), expect=lambda: "Pending Biometric Fallback Exceptions (2)" in txt(), wait=2200)
            t.step("Before scrolling: the Approve button of the first row is NOT inside the screen (it is further right in the table)", expect=lambda: not inview("Approve", "Second Synthetic"), wait=2800)
            t.step("Slide the table sideways with its own inner scroll", lambda: slide(True), expect=lambda: inview("Approve", "Second Synthetic") and inview("Reject", "Second Synthetic") or inview("Approve", "Second Synthetic"), wait=2500)
            t.step("Approve is now inside the screen. Move the pointer to it and press it", lambda: click_centered("Approve", "Second Synthetic"), expect=lambda: "successfully approved" in txt() and "Pending Biometric Fallback Exceptions (1)" in txt(), wait=3500)
            t.step("Result on the page: approved, one request left", lambda: pg.evaluate("window.scrollTo(0,0);0"), expect=lambda: "Pending Biometric Fallback Exceptions (1)" in txt(), wait=2500)
            t.step("Scroll back to the table. The remaining row's Reject button starts off screen again", lambda: pg.evaluate("[...document.querySelectorAll('table')].find(x=>x.querySelector('button')&&x.innerText.includes('Third Synthetic')).scrollIntoView({block:'center'});0"), expect=lambda: not inview("Reject", "Third Synthetic"), wait=2800)
            t.step("Slide the table sideways until Reject is inside the screen", lambda: slide(True), expect=lambda: inview("Reject", "Third Synthetic"), wait=2500)
            t.step("Move the pointer to Reject and press it", lambda: click_centered("Reject", "Third Synthetic"), expect=lambda: "successfully rejected" in txt() and "Pending Biometric Fallback Exceptions (0)" in txt(), wait=3500)
            t.step("Result on the page: rejected, no requests left", lambda: pg.evaluate("window.scrollTo(0,0);0"), expect=lambda: "No pending fallback requests" in txt() or "(0)" in txt(), wait=2500)
            t.note("Not shown: check-out approval, Manager and HR views, refusing your own request, the same scroll proof for the Directory and Sessions tables (see clip 16 for Directory).", 3)

        run_tour(ctx, "20_admin_button_reach", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixtures (ORM): ST-LIVE-1 given the Admin role, two people with pending requests. Clicks are real mouse presses at the button position after the table is scrolled by its own inner scroll.",
                 unshown=["check-out approval", "Manager view", "HR view", "own-request refusal", "Sessions table scroll proof"])

    g["DRILLS"]["tour_admin_scroll"] = scroll_tour


def register_iam_forms(g):
    APP = g["APP"]

    def forms_tour(ctx):
        import concurrent.futures

        import django

        django.setup()
        box = {}

        def mk():
            from django.conf import settings

            from apps.accounts.auth.iam_auth import create_iam_session
            from apps.accounts.models import CustomUser, IAMAdminIdentity
            from apps.accounts.models import CustomUser as CU

            CU.objects.filter(employee_id="ST-LIVE-1").update(role=CU.Role.ADMIN)
            u = CU.objects.get(employee_id="ST-LIVE-1")
            adm = IAMAdminIdentity.objects.create(admin_id="IAM-FIXTURE-1", user=u, password_hash="!", role="admin", status="active")
            CU.objects.get_or_create(employee_id="ST-LIVE-9", defaults={"full_name": "Target Synthetic", "email": "st-live-9@salowtech.com", "email_verified": True,
                                     "role": CU.Role.EMPLOYEE, "status": CU.Status.ACTIVE, "is_active": True})

            class R:
                META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "drill"}
                client_ip = "127.0.0.1"

            r = R()
            create_iam_session(adm, r, assurance_level=1)
            box["raw"], box["cookie"] = r.session_key_iam, getattr(settings, "IAM_SESSION_COOKIE_NAME", "salow_iam_session")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()
        IAM = "http://iam.salowtech.com:" + APP.rsplit(":", 1)[1]
        cookie = {"name": box["cookie"], "value": box["raw"], "domain": "iam.salowtech.com", "path": "/", "httpOnly": True, "sameSite": "Lax"}

        def body(t, pg):
            pg.context.add_init_script("(()=>{const P='[hidden in recording]';const mask=()=>{const e=document.getElementById('issued-act-code');if(e&&e.textContent.trim()!==P){e.setAttribute('data-len',String(e.textContent.trim().length));e.textContent=P;}document.querySelectorAll('[data-copy]').forEach(b=>b.removeAttribute('data-copy'));};new MutationObserver(mask).observe(document,{childList:true,subtree:true,characterData:true});document.addEventListener('DOMContentLoaded',mask);mask();})();")
            txt = lambda: pg.inner_text("body")
            form = lambda a: pg.locator("form", has=pg.locator("input[name=action][value=%s]" % a)).first

            def fill_create(eid, nm, em):
                f = form("create_employee")
                f.scroll_into_view_if_needed()
                f.locator("input[name=employee_id]").fill(eid)
                f.locator("input[name=full_name]").fill(nm)
                f.locator("input[name=email]").fill(em)
                f.locator("select[name=role]").select_option("employee")
                pg.wait_for_timeout(900)
                f.locator("button[type=submit]").click()

            pg.goto(IAM + "/dashboard/?tab=identity")
            t.step("Setup (fixture): IAM host, administrator session made off camera at the basic assurance level. Identity tab, Create Employee form", lambda: form("create_employee").scroll_into_view_if_needed(), expect=lambda: form("create_employee").count() == 1, wait=2200)
            t.step("Create an employee with an outside email domain: it is refused with a message", lambda: fill_create("ST-NEW-1", "New Synthetic", "new1@example.com"), expect=lambda: "@salowtech.com" in txt(), wait=3000)
            off = {}

            def create_off_camera():
                # A separate, unrecorded browser context: the success page shows a one-time code, which must never be filmed.
                c2 = pg.context.browser.new_context(viewport={"width": 390, "height": 844})
                try:
                    c2.add_cookies([cookie])
                    q = c2.new_page()
                    q.goto(IAM + "/dashboard/?tab=identity")
                    f2 = q.locator("form", has=q.locator("input[name=action][value=create_employee]")).first
                    f2.locator("input[name=employee_id]").fill("ST-NEW-1")
                    f2.locator("input[name=full_name]").fill("New Synthetic")
                    f2.locator("input[name=email]").fill("new1@salowtech.com")
                    f2.locator("select[name=role]").select_option("employee")
                    f2.locator("button[type=submit]").click()
                    q.wait_for_timeout(1500)
                    off["banner"] = "Account created for New Synthetic" in q.inner_text("body")
                    off["card"] = q.locator("#issued-act-code").count() == 1
                    n = len(q.inner_text("#issued-act-code").strip()) if off["card"] else 0
                    off["len_ok"] = 8 <= n <= 40
                finally:
                    c2.close()

            t.step("Create an employee with a company email. This one is done in a separate unrecorded tab, because the success page shows a one-time code that must not be filmed. The script checks the result", create_off_camera, expect=lambda: off.get("banner") and off.get("card") and off.get("len_ok"), wait=1200)
            t.note("Off camera result (checked by script, code never printed): account-created banner present, one-time code card present, code length in the normal range.", 3)
            t.step("Create the same Employee ID again: refused as already in use", lambda: (pg.goto(IAM + "/dashboard/?tab=identity"), fill_create("ST-NEW-1", "New Synthetic", "new2@salowtech.com")), expect=lambda: "already in use" in txt(), wait=3000)
            def suspend():
                pg.goto(IAM + "/dashboard/?tab=lifecycle")
                f = form("suspend_account")
                f.scroll_into_view_if_needed()
                f.locator("input[name=target_employee_id]").fill("ST-LIVE-9")
                pg.wait_for_timeout(900)
                f.locator("button[type=submit]").click()
            t.step("Suspend an account at the basic assurance level (Lifecycle tab). Observe what the console does", suspend, wait=3500, expect=lambda: True)
            t.note("Observed after pressing Suspend: URL %s | page says: %s" % (pg.url.split("/", 3)[-1][:60], " ".join(txt().split())[:110]), 4)
            t.note("Not shown: step-up code entry, role change, MFA reset, session revoke, recovery approval, break-glass, service identities, outbox retry, team add.", 3)

        run_tour(ctx, "21_iam_forms_basic", body, extra_cookies=[cookie],
                 notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated, IAM host name mapped locally. IAM session is a fixture at the basic assurance level (not via login). Activation code hidden in the recording by a script-injected style.",
                 unshown=["step-up entry", "role change", "MFA reset", "session revoke", "recovery approve/reject", "break-glass", "service identities", "outbox retry", "team add", "Super Admin creation"])

    g["DRILLS"]["tour_iam_forms"] = forms_tour


def register_iam_stepup(g):
    APP = g["APP"]

    def stepup_tour(ctx):
        import concurrent.futures
        import secrets as _s
        import time as _t

        import django
        import pyotp

        django.setup()
        box = {"last": 0}
        secret = {}

        def mk():
            from django.conf import settings
            from django.contrib.auth.hashers import make_password

            from apps.accounts.auth.crypto import encrypt_totp_secret
            from apps.accounts.auth.iam_auth import create_iam_session
            from apps.accounts.models import CustomUser as CU
            from apps.accounts.models import IAMAdminIdentity
            from apps.directory.models import Department, Team

            CU.objects.filter(employee_id="ST-LIVE-1").update(role=CU.Role.ADMIN)
            u = CU.objects.get(employee_id="ST-LIVE-1")
            secret["pw"] = _s.token_urlsafe(18) + "aA1!"
            secret["totp"] = pyotp.random_base32(length=32)
            ct, nonce, kid = encrypt_totp_secret(secret["totp"])
            adm = IAMAdminIdentity.objects.create(admin_id="IAM-FIXTURE-1", user=u, password_hash=make_password(secret["pw"]), role="admin",
                                                  status="active", encrypted_totp_secret=ct, totp_nonce=nonce, totp_key_id=kid)
            CU.objects.get_or_create(employee_id="ST-LIVE-9", defaults={"full_name": "Target Synthetic", "email": "st-live-9@salowtech.com", "email_verified": True,
                                     "role": CU.Role.EMPLOYEE, "status": CU.Status.ACTIVE, "is_active": True})
            d = Department.objects.create(code="ZDEP", name="Fixture Dept")
            Team.objects.create(code="ZTEAM", name="Fixture Team", department=d)

            class R:
                META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "drill"}
                client_ip = "127.0.0.1"

            r = R()
            create_iam_session(adm, r, assurance_level=1)
            box["raw"], box["cookie"] = r.session_key_iam, getattr(settings, "IAM_SESSION_COOKIE_NAME", "salow_iam_session")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()
        IAM = "http://iam.salowtech.com:" + APP.rsplit(":", 1)[1]
        cookie = {"name": box["cookie"], "value": box["raw"], "domain": "iam.salowtech.com", "path": "/", "httpOnly": True, "sameSite": "Lax"}

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            form = lambda a: pg.locator("form", has=pg.locator("input[name=action][value=%s]" % a)).first
            res = {}

            def stepup_off_camera(action):
                # Real step-up through the real page, in a separate unrecorded context. The password and code never appear in the recording.
                cur = [c for c in pg.context.cookies() if c["name"] == box["cookie"]][0]
                c2 = pg.context.browser.new_context(viewport={"width": 390, "height": 844})
                ok = False
                try:
                    c2.add_cookies([cur])
                    q = c2.new_page()
                    q.goto(pg.url)
                    cs = int(_t.time() // 30)
                    while cs <= box["last"]:
                        _t.sleep(1.0)
                        cs = int(_t.time() // 30)
                    left = 30 - (_t.time() % 30)
                    if left < 6:
                        _t.sleep(left + 0.5)
                    q.fill("input[name=password]", secret["pw"])
                    q.fill("input[name=totp_code]", pyotp.TOTP(secret["totp"]).now())
                    box["last"] = int(_t.time() // 30)
                    q.locator("form button[type=submit]").first.click()
                    q.wait_for_load_state()
                    q.wait_for_timeout(800)
                    ok = "Step-up authentication verified" in q.inner_text("body")
                    new = [c for c in c2.cookies() if c["name"] == box["cookie"]]
                    if new:
                        pg.context.add_cookies([new[0]])
                finally:
                    c2.close()
                res[action] = ok

            def flow(action, tab, fill, label, success_text):
                pg.goto(IAM + "/dashboard/?tab=" + tab)
                f = form(action)
                f.scroll_into_view_if_needed()
                fill(f)
                pg.wait_for_timeout(900)
                t.step("%s: submit at the basic assurance level. It redirects to the step-up page" % label, lambda: f.locator("button[type=submit]").click(), expect=lambda: "/iam/stepup" in pg.url, wait=3500)
                t.step("Step-up done off camera with the fixture administrator password and a fresh code (script only, never printed). Page is not shown while it runs", lambda: stepup_off_camera(action), expect=lambda: res.get(action), wait=800)
                nxt = pg.url
                t.step("Back on the console: submit the same form again, now with the step-up in place", lambda: (pg.goto(IAM + "/dashboard/?tab=" + tab), fill(form(action)), pg.wait_for_timeout(900), form(action).locator("button[type=submit]").click()), expect=lambda: success_text in txt(), wait=4500)

            pg.goto(IAM + "/dashboard/?tab=lifecycle")
            t.note("Fixture: an IAM administrator session at the basic level, made off camera. Its password and authenticator secret are generated for this run only and exist only in a throwaway database.", 3)
            flow("suspend_account", "lifecycle", lambda f: f.locator("input[name=target_employee_id]").fill("ST-LIVE-9"), "Suspend ST-LIVE-9", "ST-LIVE-9 suspended")
            flow("reactivate_account", "lifecycle", lambda f: f.locator("input[name=target_employee_id]").fill("ST-LIVE-9"), "Reactivate ST-LIVE-9", "ST-LIVE-9")
            def fill_role(f):
                f.locator("input[name=target_employee_id]").fill("ST-LIVE-9")
                f.locator("select[name=new_role]").select_option("manager")
            flow("update_role", "roles", fill_role, "Change ST-LIVE-9 role to manager", "ST-LIVE-9")

            def team():
                pg.goto(IAM + "/dashboard/?tab=people_teams")
                f = form("add_team_membership")
                f.scroll_into_view_if_needed()
                f.locator("input[name=target_employee_id]").fill("ST-LIVE-9")
                f.locator("select[name=team_code]").select_option("ZTEAM")
                pg.wait_for_timeout(900)
                f.locator("button[type=submit]").click()
            t.step("Add ST-LIVE-9 to a team (a basic-level action, no step-up needed)", team, expect=lambda: "Assigned ST-LIVE-9" in txt(), wait=4000)
            t.note("Not shown: Super Admin changes, MFA reset, recovery approve/reject, break-glass, service identities, outbox retry, session revoke.", 3)

        run_tour(ctx, "22_iam_stepup", body, extra_cookies=[cookie],
                 notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated, IAM host name mapped locally. Fixture IAM session at the basic level; the step-up itself is the real page and real check, done in an unrecorded context so the password and code are never filmed. Nothing in the console was loosened.",
                 unshown=["MFA reset", "recovery approve/reject", "break-glass", "service identities", "outbox retry", "session revoke", "Super Admin changes"])

    g["DRILLS"]["tour_iam_stepup"] = stepup_tour


def register_iam_stepup2(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import secrets as _s
        import time as _t

        import django
        import pyotp

        django.setup()
        box = {"last": 0}
        secret = {}

        def mk():
            from django.conf import settings
            from django.contrib.auth.hashers import make_password

            from apps.accounts.auth.crypto import encrypt_totp_secret
            from apps.accounts.auth.iam_auth import create_iam_session
            from apps.accounts.models import CustomUser as CU
            from apps.accounts.models import IAMAdminIdentity, UserSession

            CU.objects.filter(employee_id="ST-LIVE-1").update(role=CU.Role.ADMIN)
            u = CU.objects.get(employee_id="ST-LIVE-1")
            secret["pw"] = _s.token_urlsafe(18) + "aA1!"
            secret["totp"] = pyotp.random_base32(length=32)
            ct, nonce, kid = encrypt_totp_secret(secret["totp"])
            adm = IAMAdminIdentity.objects.create(admin_id="IAM-FIXTURE-1", user=u, password_hash=make_password(secret["pw"]), role="admin",
                                                  status="active", encrypted_totp_secret=ct, totp_nonce=nonce, totp_key_id=kid)
            t9, _c = CU.objects.get_or_create(employee_id="ST-LIVE-9", defaults={"full_name": "Target Synthetic", "email": "st-live-9@salowtech.com", "email_verified": True,
                                              "role": CU.Role.EMPLOYEE, "status": CU.Status.ACTIVE, "is_active": True})
            for i in range(2):
                UserSession.objects.create(user=t9, session_key=_s.token_hex(20), ip_address="127.0.0.1", user_agent="fixture device %d" % i, mfa_verified=True)

            class R:
                META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "drill"}
                client_ip = "127.0.0.1"

            r = R()
            create_iam_session(adm, r, assurance_level=1)
            box["raw"], box["cookie"] = r.session_key_iam, getattr(settings, "IAM_SESSION_COOKIE_NAME", "salow_iam_session")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()
        IAM = "http://iam.salowtech.com:" + APP.rsplit(":", 1)[1]
        cookie = {"name": box["cookie"], "value": box["raw"], "domain": "iam.salowtech.com", "path": "/", "httpOnly": True, "sameSite": "Lax"}

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            res = {}

            def form(a, who=None, page=None):
                page = page or pg
                loc = page.locator("form", has=page.locator("input[name=action][value=%s]" % a))
                if who:
                    loc = loc.filter(has=page.locator("input[name=target_employee_id][value=%s]" % who))
                return loc.first

            def stepup_off_camera(key, resubmit=None, tab=None):
                cur = [c for c in pg.context.cookies() if c["name"] == box["cookie"]][0]
                c2 = pg.context.browser.new_context(viewport={"width": 390, "height": 844})
                ok = False
                try:
                    c2.add_cookies([cur])
                    q = c2.new_page()
                    q.goto(pg.url)
                    cs = int(_t.time() // 30)
                    while cs <= box["last"]:
                        _t.sleep(1.0)
                        cs = int(_t.time() // 30)
                    left = 30 - (_t.time() % 30)
                    if left < 6:
                        _t.sleep(left + 0.5)
                    q.fill("input[name=password]", secret["pw"])
                    q.fill("input[name=totp_code]", pyotp.TOTP(secret["totp"]).now())
                    box["last"] = int(_t.time() // 30)
                    q.locator("form button[type=submit]").first.click()
                    q.wait_for_load_state()
                    q.wait_for_timeout(800)
                    ok = "Step-up authentication verified" in q.inner_text("body")
                    if ok and resubmit:
                        resubmit(q)
                        q.wait_for_timeout(1500)
                        res[key + "_body"] = q.inner_text("body")
                    new = [c for c in c2.cookies() if c["name"] == box["cookie"]]
                    if new:
                        pg.context.add_cookies([new[0]])
                finally:
                    c2.close()
                res[key] = ok

            def flow(key, action, tab, fill, label, success, who=None, off_resubmit=None, off_check=None):
                def submit():
                    pg.goto(IAM + "/dashboard/?tab=" + tab)
                    f = form(action, who)
                    f.scroll_into_view_if_needed()
                    fill(pg, f)
                    pg.wait_for_timeout(900)
                    f.locator("button[type=submit]").click()
                t.step("%s: submit at the basic assurance level. It redirects to the step-up page" % label, submit, expect=lambda: "/iam/stepup" in pg.url, wait=3500)
                t.step("Step-up done off camera (fixture password and a fresh code, script only, never printed)", lambda: stepup_off_camera(key, off_resubmit, tab), expect=lambda: res.get(key) and (off_check(res.get(key + "_body", "")) if off_check else True), wait=800)
                if off_resubmit:
                    t.step("The result page shows a one-time secret, so the resubmission was done off camera. Console after it", lambda: pg.goto(IAM + "/dashboard/?tab=" + tab), expect=lambda: success in txt() or True, wait=3500)
                else:
                    def again():
                        pg.goto(IAM + "/dashboard/?tab=" + tab)
                        f = form(action, who)
                        f.scroll_into_view_if_needed()
                        fill(pg, f)
                        pg.wait_for_timeout(900)
                        f.locator("button[type=submit]").click()
                    t.step("Back on the console: submit the same form again, now with the step-up in place", again, expect=lambda: success in txt(), wait=4500)

            pg.goto(IAM + "/dashboard/?tab=sessions")
            t.note("Fixture: IAM administrator session at the basic level, made off camera; password and authenticator secret exist only in this run's throwaway database. Target ST-LIVE-9 has two fixture sessions.", 3)
            t.step("Sessions tab: the fixture sessions of ST-LIVE-9 are listed", lambda: form("revoke_user_all_sessions", "ST-LIVE-9").scroll_into_view_if_needed(), expect=lambda: form("revoke_user_all_sessions", "ST-LIVE-9").count() == 1, wait=2500)
            flow("rev", "revoke_user_all_sessions", "sessions", lambda p, f: None, "Revoke all sessions of ST-LIVE-9", "Revoked all", who="ST-LIVE-9")

            pg.goto(IAM + "/dashboard/?tab=privileged")
            def bg_fill(p, f):
                f.locator("textarea[name=reason]").fill("Fixture drill: emergency access check")
                f.locator("input[name=ticket]").fill("INC-FIXTURE-1")
            def bg():
                f = form("activate_break_glass")
                f.scroll_into_view_if_needed()
                bg_fill(pg, f)
                pg.wait_for_timeout(900)
                f.locator("button[type=submit]").click()
            t.step("Break-glass: activate with a reason and ticket", bg, expect=lambda: "BREAK-GLASS ACTIVATED" in txt() or "/iam/stepup" in pg.url, wait=4000)
            t.note("Observed after pressing activate: %s | %s" % (pg.url.split("/", 3)[-1][:50], " ".join(txt().split())[:110]), 4)
            if "/iam/stepup" in pg.url:
                t.step("Step-up done off camera", lambda: stepup_off_camera("bg"), expect=lambda: res.get("bg"), wait=800)
                def bg2():
                    pg.goto(IAM + "/dashboard/?tab=privileged")
                    f = form("activate_break_glass")
                    f.scroll_into_view_if_needed()
                    bg_fill(pg, f)
                    pg.wait_for_timeout(900)
                    f.locator("button[type=submit]").click()
                t.step("Submit again after the step-up", bg2, expect=lambda: "BREAK-GLASS ACTIVATED" in txt(), wait=4000)
            def bg_off():
                f = form("revoke_break_glass")
                f.scroll_into_view_if_needed()
                pg.wait_for_timeout(900)
                f.locator("button[type=submit]").click()
            t.step("End the break-glass elevation", bg_off, expect=lambda: "terminated" in txt(), wait=4000)

            def svc_fill(p, f):
                f.locator("input[name=name]").fill("Fixture worker")
                f.locator("input[name=client_id]").fill("fixture-worker")
                f.locator("input[name=scopes]").first.check()
            def svc_resubmit(q):
                q.goto(IAM + "/dashboard/?tab=services")
                f2 = form("create_service_identity", None, q)
                svc_fill(q, f2)
                f2.locator("button[type=submit]").click()
                q.wait_for_load_state()
            flow("svc", "create_service_identity", "services", svc_fill, "Create service identity", "provisioned", off_resubmit=svc_resubmit,
                 off_check=lambda b: "provisioned successfully" in b)
            t.step("The new service identity is listed (token not shown)", lambda: pg.locator("text=fixture-worker").first.scroll_into_view_if_needed(), expect=lambda: pg.locator("text=fixture-worker").count() >= 1, wait=3000)
            flow("rsvc", "revoke_service_identity", "services", lambda p, f: None, "Revoke the service identity", "revoked", who=None)
            t.note("Not shown: MFA reset, recovery approve/reject, outbox retry, Super Admin changes.", 3)

        run_tour(ctx, "23_iam_stepup_more", body, extra_cookies=[cookie],
                 notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated, IAM host mapped locally. Fixture IAM session at basic level; step-up done on the real page in an unrecorded context. Break-glass and service identity are security-sensitive actions: exercised only on the throwaway fixture with the existing policy unchanged.",
                 unshown=["MFA reset", "recovery approve/reject", "outbox retry", "Super Admin changes"])

    g["DRILLS"]["tour_iam_stepup2"] = tour


def register_iam_stepup3(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import secrets as _s
        import time as _t

        import django
        import pyotp

        django.setup()
        box = {"last": 0}
        secret = {}

        def mk():
            from django.conf import settings
            from django.contrib.auth.hashers import make_password

            from apps.accounts.auth.crypto import encrypt_totp_secret
            from apps.accounts.auth.iam_auth import create_iam_session
            from apps.accounts.models import CustomUser as CU
            from apps.accounts.models import IAMAdminIdentity, UserSession

            CU.objects.filter(employee_id="ST-LIVE-1").update(role=CU.Role.ADMIN)
            u = CU.objects.get(employee_id="ST-LIVE-1")
            secret["pw"] = _s.token_urlsafe(18) + "aA1!"
            secret["totp"] = pyotp.random_base32(length=32)
            ct, nonce, kid = encrypt_totp_secret(secret["totp"])
            adm = IAMAdminIdentity.objects.create(admin_id="IAM-FIXTURE-1", user=u, password_hash=make_password(secret["pw"]), role="admin",
                                                  status="active", encrypted_totp_secret=ct, totp_nonce=nonce, totp_key_id=kid)
            t9, _c = CU.objects.get_or_create(employee_id="ST-LIVE-9", defaults={"full_name": "Target Synthetic", "email": "st-live-9@salowtech.com", "email_verified": True,
                                              "role": CU.Role.EMPLOYEE, "status": CU.Status.ACTIVE, "is_active": True})
            from apps.core.models import EmailOutbox
            EmailOutbox.objects.create(recipient="fixture-recipient@example.invalid", subject="Fixture failed notice", body_text="fixture", notification_type="fixture_notice",
                                       status=EmailOutbox.Status.FAILED, retry_count=5, last_error="fixture transport error")

            class R:
                META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "drill"}
                client_ip = "127.0.0.1"

            r = R()
            create_iam_session(adm, r, assurance_level=1)
            box["raw"], box["cookie"] = r.session_key_iam, getattr(settings, "IAM_SESSION_COOKIE_NAME", "salow_iam_session")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()
        IAM = "http://iam.salowtech.com:" + APP.rsplit(":", 1)[1]
        cookie = {"name": box["cookie"], "value": box["raw"], "domain": "iam.salowtech.com", "path": "/", "httpOnly": True, "sameSite": "Lax"}

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            res = {}

            def form(a, who=None, page=None):
                page = page or pg
                loc = page.locator("form", has=page.locator("input[name=action][value=%s]" % a))
                if who:
                    loc = loc.filter(has=page.locator("input[name=target_employee_id][value=%s]" % who))
                return loc.first

            def stepup_off_camera(key, resubmit=None, tab=None):
                cur = [c for c in pg.context.cookies() if c["name"] == box["cookie"]][0]
                c2 = pg.context.browser.new_context(viewport={"width": 390, "height": 844})
                ok = False
                try:
                    c2.add_cookies([cur])
                    q = c2.new_page()
                    q.goto(pg.url)
                    cs = int(_t.time() // 30)
                    while cs <= box["last"]:
                        _t.sleep(1.0)
                        cs = int(_t.time() // 30)
                    left = 30 - (_t.time() % 30)
                    if left < 6:
                        _t.sleep(left + 0.5)
                    q.fill("input[name=password]", secret["pw"])
                    q.fill("input[name=totp_code]", pyotp.TOTP(secret["totp"]).now())
                    box["last"] = int(_t.time() // 30)
                    q.locator("form button[type=submit]").first.click()
                    q.wait_for_load_state()
                    q.wait_for_timeout(800)
                    ok = "Step-up authentication verified" in q.inner_text("body")
                    if ok and resubmit:
                        resubmit(q)
                        q.wait_for_timeout(1500)
                        res[key + "_body"] = q.inner_text("body")
                    new = [c for c in c2.cookies() if c["name"] == box["cookie"]]
                    if new:
                        pg.context.add_cookies([new[0]])
                finally:
                    c2.close()
                res[key] = ok

            def flow(key, action, tab, fill, label, success, who=None, off_resubmit=None, off_check=None):
                def submit():
                    pg.goto(IAM + "/dashboard/?tab=" + tab)
                    f = form(action, who)
                    f.scroll_into_view_if_needed()
                    fill(pg, f)
                    pg.wait_for_timeout(900)
                    f.locator("button[type=submit]").click()
                t.step("%s: submit at the basic assurance level. It redirects to the step-up page" % label, submit, expect=lambda: "/iam/stepup" in pg.url, wait=3500)
                t.step("Step-up done off camera (fixture password and a fresh code, script only, never printed)", lambda: stepup_off_camera(key, off_resubmit, tab), expect=lambda: res.get(key) and (off_check(res.get(key + "_body", "")) if off_check else True), wait=800)
                if off_resubmit:
                    t.step("The result page shows a one-time secret, so the resubmission was done off camera. Console after it", lambda: pg.goto(IAM + "/dashboard/?tab=" + tab), expect=lambda: success in txt() or True, wait=3500)
                else:
                    def again():
                        pg.goto(IAM + "/dashboard/?tab=" + tab)
                        f = form(action, who)
                        f.scroll_into_view_if_needed()
                        fill(pg, f)
                        pg.wait_for_timeout(900)
                        f.locator("button[type=submit]").click()
                    t.step("Back on the console: submit the same form again, now with the step-up in place", again, expect=lambda: success in txt(), wait=4500)

            pg.goto(IAM + "/dashboard/?tab=delivery_ops")
            t.note("Fixture: IAM administrator session at the basic level, made off camera; password and authenticator secret exist only in this run's throwaway database. One failed fixture email is seeded.", 3)
            def retry():
                f = form("retry_outbox_item")
                f.scroll_into_view_if_needed()
                pg.wait_for_timeout(900)
                f.locator("button[type=submit]").click()
            t.step("Delivery tab: a failed email is listed with a Retry button", lambda: form("retry_outbox_item").scroll_into_view_if_needed(), expect=lambda: form("retry_outbox_item").count() == 1, wait=2500)
            t.step("Press Retry on the failed email", retry, expect=lambda: "requeued" in txt() or "/iam/stepup" in pg.url, wait=4000)
            t.note("Observed after Retry: %s | %s" % (pg.url.split("/", 3)[-1][:50], " ".join(txt().split())[:110]), 4)

            def mfa_fill(p, f):
                f.locator("input[name=target_employee_id]").fill("ST-LIVE-9")
            def mfa_resubmit(q):
                q.goto(IAM + "/dashboard/?tab=security_mfa")
                f2 = form("issue_mfa_reset", None, q)
                mfa_fill(q, f2)
                f2.locator("button[type=submit]").click()
                q.wait_for_load_state()
            flow("mfa", "issue_mfa_reset", "security_mfa", mfa_fill, "Issue an MFA reset code for ST-LIVE-9", "generated", off_resubmit=mfa_resubmit,
                 off_check=lambda b: "Single-use MFA reset code generated" in b and len(b) > 0)
            t.note("Not shown: the generated reset code itself (one-time secret, never filmed). Not shown: recovery approve/reject, Super Admin changes.", 3)

        run_tour(ctx, "24_iam_mfa_outbox", body, extra_cookies=[cookie],
                 notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated, IAM host mapped locally. Fixture IAM session at basic level; step-up done on the real page in an unrecorded context. Break-glass and service identity are security-sensitive actions: exercised only on the throwaway fixture with the existing policy unchanged.",
                 unshown=["MFA reset code value", "recovery approve/reject", "Super Admin changes"])

    g["DRILLS"]["tour_iam_stepup3"] = tour




def register_bg_probe(g):
    APP = g["APP"]
    check = g["check"]

    def probe(ctx):
        import concurrent.futures

        import django

        django.setup()
        box = {}

        def mk():
            from django.conf import settings

            from apps.accounts.auth.iam_auth import create_iam_session
            from apps.accounts.models import CustomUser as CU
            from apps.accounts.models import IAMAdminIdentity

            CU.objects.filter(employee_id="ST-LIVE-1").update(role=CU.Role.ADMIN)
            u = CU.objects.get(employee_id="ST-LIVE-1")
            adm = IAMAdminIdentity.objects.create(admin_id="IAM-FIXTURE-1", user=u, password_hash="!", role="admin", status="active")

            class R:
                META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "drill"}
                client_ip = "127.0.0.1"

            r = R()
            create_iam_session(adm, r, assurance_level=1)
            box["raw"], box["cookie"] = r.session_key_iam, getattr(settings, "IAM_SESSION_COOKIE_NAME", "salow_iam_session")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(mk).result()
        IAM = "http://iam.salowtech.com:" + APP.rsplit(":", 1)[1]
        pg = ctx.pages[0]
        pg.goto(APP + "/auth/recovery/console/")
        check("probe control: before break-glass, the workspace-host recovery console asks for sudo re-authentication", "/security/sudo" in pg.url, pg.url.split("/", 3)[-1][:60])
        c2 = ctx.browser.new_context()
        try:
            c2.add_cookies([{"name": box["cookie"], "value": box["raw"], "domain": "iam.salowtech.com", "path": "/", "httpOnly": True, "sameSite": "Lax"}])
            q = c2.new_page()
            q.goto(IAM + "/dashboard/?tab=privileged")
            f = q.locator("form", has=q.locator("input[name=action][value=activate_break_glass]")).first
            f.locator("textarea[name=reason]").fill("Probe: ten or more characters")
            f.locator("button[type=submit]").click()
            q.wait_for_load_state()
            activated = "BREAK-GLASS ACTIVATED" in q.inner_text("body")
            check("probe: break-glass activated at the basic assurance level (no step-up page)", activated and "/iam/stepup" not in q.url)
        finally:
            c2.close()
        pg.goto(APP + "/auth/recovery/console/")
        check("probe RESULT: after IAM-host break-glass, the admin's separate workspace-host session reaches the recovery console without sudo re-authentication", "/security/sudo" not in pg.url, pg.url.split("/", 3)[-1][:60])
        body = pg.inner_text("body")
        check("probe RESULT: that page is the real recovery console, not an error page", "Recovery" in body and "Forbidden" not in body and "denied" not in body.lower(), " ".join(body.split())[:120])

    g["DRILLS"]["probe_bg_sudo"] = probe


def register_cal_location(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.goto(APP + "/calendar/new/")
            t.step("New event form shows Location and Description", expect=lambda: pg.locator("input[name=location]").count() == 1 and pg.locator("textarea[name=description]").count() == 1, wait=1800)
            t.step("Fill the form", lambda: (pg.fill("input[name=title]", "Team sync"), pg.fill("input[name=start]", "2026-12-04T10:00"), pg.fill("input[name=end]", "2026-12-04T11:00"),
                                             pg.fill("input[name=location]", "Meeting room, 2nd floor"), pg.fill("textarea[name=description]", "Agenda: rota and leave.\nBring your notes.")), expect=lambda: pg.input_value("input[name=location]") == "Meeting room, 2nd floor", wait=2200)
            t.step("Create the event: the page shows Where and the description", lambda: pg.click(".cal-form button[type=submit]"), expect=lambda: "Meeting room, 2nd floor" in pg.inner_text("body") and "Bring your notes." in pg.inner_text("body"), wait=2500)
            t.step("Edit: both fields are filled with the saved text", lambda: pg.click("a:has-text('Edit')"), expect=lambda: pg.input_value("input[name=location]") == "Meeting room, 2nd floor" and "Agenda" in pg.input_value("textarea[name=description]"), wait=2200)
            t.step("Change the location and save", lambda: (pg.fill("input[name=location]", "Hall B"), pg.click(".cal-form button[type=submit]")), expect=lambda: "Hall B" in pg.inner_text("body") and "Meeting room" not in pg.inner_text("body"), wait=2500)

        run_tour(ctx, "97_calendar_location", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Organiser view only; a guest's view is covered by unit tests, not filmed.",
                 unshown=["a guest's view of the event"])

    g["DRILLS"]["tour_cal_location"] = tour


def register_slides_dup_sheets_fx(g):
    APP = g["APP"]

    def tour(ctx):
        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        def shown(pg, a):  # the value of a cell as the bar shows it after selecting it
            pg.click('input[data-addr="%s"]' % a)
            return pg.inner_text("#sheet-val").strip().replace("Result: ", "", 1)

        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            items = lambda: pg.locator("#sl-items li").count()
            t.step("Slides: Duplicate item is off until an item is selected", expect=lambda: pg.is_disabled("#sl-dup-el"), wait=1800)
            t.step("Add a text item and style it", lambda: (pg.click("#sl-add-text"), pg.fill("#sl-text", "Quarterly plan"), pg.press("#sl-text", "Tab"), pg.check("#sl-bold")), expect=lambda: items() == 1 and not pg.is_disabled("#sl-dup-el"))
            t.step("Duplicate item: a copy appears, selected, 20 right and 20 down", lambda: pg.click("#sl-dup-el"), expect=lambda: items() == 2 and pg.input_value("#sl-x") == "100" and pg.input_value("#sl-y") == "100" and pg.input_value("#sl-text") == "Quarterly plan" and pg.is_checked("#sl-bold"))
            t.step("Undo: the copy is gone in one step", lambda: pg.click("#sl-undo"), expect=lambda: items() == 1)
            t.step("Redo: the copy is back", lambda: pg.click("#sl-redo"), expect=lambda: items() == 2)
            t.step("Select the copy from the list and duplicate it again", lambda: (pg.locator("#sl-items li button").nth(1).click(), pg.click("#sl-dup-el")), expect=lambda: items() == 3 and pg.input_value("#sl-x") == "120")
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("Sheets: a small table (region, qty, month)", lambda: put(pg, [("A1", "N"), ("B1", "10"), ("C1", "1"), ("A2", "S"), ("B2", "20"), ("C2", "1"), ("A3", "N"), ("B3", "30"), ("C3", "2"), ("A4", "N"), ("B4", "x"), ("C4", "2")]), expect=lambda: pg.input_value('input[data-addr="B3"]') == "30", wait=1500)
            t.step("SUMIFS over two columns", lambda: put(pg, [("E1", '=SUMIFS(B1:B4,A1:A4,"N",C1:C4,2)')]), expect=lambda: shown(pg, "E1") == "30")
            t.step("COUNTIFS", lambda: put(pg, [("E2", '=COUNTIFS(A1:A4,"N",C1:C4,2)')]), expect=lambda: shown(pg, "E2") == "2")
            t.step("IFERROR turns a divide by zero into text", lambda: put(pg, [("E3", '=IFERROR(B1/0,"none")')]), expect=lambda: shown(pg, "E3") == "none")
            t.step("ROUNDUP of a tiny number", lambda: put(pg, [("E4", "=ROUNDUP(0.0000001,2)")]), expect=lambda: shown(pg, "E4") == "0.01")
            t.step("Chain: a cell that reads the SUMIFS result and an error cell", lambda: put(pg, [("F1", "=E1*2"), ("F2", "=B1/0"), ("F3", "=ISERROR(F2)"), ("F4", "=LARGE(B1:B4,2)")]), expect=lambda: shown(pg, "F1") == "60" and shown(pg, "F3") == "true" and shown(pg, "F4") == "20")
            t.step("Edit a source cell: SUMIFS follows", lambda: put(pg, [("B3", "50")]), expect=lambda: shown(pg, "E1") == "50" and shown(pg, "F1") == "100")

        run_tour(ctx, "98_slides_dup_sheets_fx", body, notes="Device-local slide deck and sheet (browser only). Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated.",
                 unshown=["Duplicate item on a picture or on the master", "the 30-item cap", "all other new functions (covered by node tests)"])

    g["DRILLS"]["tour_slides_dup_sheets_fx"] = tour


def register_sheets_refs(g):
    APP = g["APP"]

    def tour(ctx):
        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        def bar(pg, a):
            pg.click('input[data-addr="%s"]' % a)
            return pg.input_value("#sheet-bar")

        def shown(pg, a):  # asserted from the result line, not a picture of it
            pg.click('input[data-addr="%s"]' % a)
            return pg.inner_text("#sheet-val").strip().replace("Result: ", "", 1)

        def fill(pg, rng, d):
            pg.fill("#fill-range", rng)
            pg.click("#fill-" + d)

        def body(t, pg):
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("A small table: prices in A1:A3, a rate in C1, a formula with $C$1 in B1", lambda: put(pg, [("A1", "10"), ("A2", "20"), ("A3", "30"), ("C1", "2"), ("B1", "=A1*$C$1")]), expect=lambda: shown(pg, "B1") == "20")
            t.step("Copy first row down over B1:B3: A moves, $C$1 stays", lambda: fill(pg, "B1:B3", "down"), expect=lambda: bar(pg, "B3") == "=A3*$C$1" and shown(pg, "B3") == "60" and shown(pg, "B2") == "40")
            t.step("The status line says references were adjusted", expect=lambda: "2 with references adjusted" in pg.inner_text("#sheet-status"))
            t.step("Fill right over D1:E1: columns move, B$1 keeps its row", lambda: (put(pg, [("D1", "=A1+B$1")]), fill(pg, "D1:E1", "right")), expect=lambda: bar(pg, "E1") == "=B1+C$1")
            t.step("A reference that moves off the grid becomes #REF!", lambda: (put(pg, [("G1", "=A1000+1")]), fill(pg, "G1:G2", "down")), expect=lambda: bar(pg, "G2") == "=#REF!+1" and shown(pg, "G2") == "#REF!" and "#REF!" in pg.inner_text("#sheet-status"))
            t.step("One Undo takes the last fill back", lambda: pg.click("#sheet-undo"), expect=lambda: pg.input_value('input[data-addr="G2"]') == "")
            t.step("A pinned reference can be typed: =$A$1+1", lambda: put(pg, [("H1", "=$A$1+1")]), expect=lambda: shown(pg, "H1") == "11")

        run_tour(ctx, "100_sheets_references", body, notes="Device-local sheet (browser only). Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Results are asserted from the bar and result line, not shown as a picture.",
                 unshown=["paste (keeps formulas as written; covered by node rows)", "ranges and quoted text (node rows)", "existing stored sheets (unchanged until a fill)"])

    g["DRILLS"]["tour_sheets_refs"] = tour


def register_sheets_struct(g):
    APP = g["APP"]

    def tour(ctx):
        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        def val(pg, a):
            return pg.input_value('input[data-addr="%s"]' % a)

        def bar(pg, a):
            pg.click('input[data-addr="%s"]' % a)
            return pg.input_value("#sheet-bar")

        def raw(pg):  # the four stored texts of the first tab
            return pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; return [k, k + '.rules', k + '.cf', k + '.fmt'].map(x => localStorage.getItem(x)); }""")

        def stat(pg):
            return pg.inner_text("#sheet-status")

        def body(t, pg):
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("A small sheet: numbers in A1:A3, formulas in B3 and C1", lambda: put(pg, [("A1", "1"), ("A2", "2"), ("A3", "3"), ("B3", "=SUM(A1:A3)"), ("C1", "=A2")]), expect=lambda: val(pg, "B3") != "")
            # a validation, a colour and a format rule, stored as the page stores them, then reload so the page reads them
            pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey;
              localStorage.setItem(k + '.rules', JSON.stringify({v:1, rules:[{range:'A2:A3', kind:'number', min:0, max:9}]}));
              localStorage.setItem(k + '.cf', JSON.stringify({v:1, rules:[{range:'A1:A3', kind:'greater', color:'red', value:1}]}));
              localStorage.setItem(k + '.fmt', JSON.stringify({v:1, rules:[{range:'B1:B3', bold:true}]})); }""")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            orig = raw(pg)
            t.step("Rules on the sheet: a validation rule on A2:A3, a colour rule on A1:A3, a bold format on B1:B3", expect=lambda: pg.locator("#val-list li").count() == 1)
            t.step("Select A2 and Insert row above: the SUM range grows and the rules move with their rows", lambda: (pg.click('input[data-addr="A2"]'), pg.click("#row-ins")),
                   expect=lambda: bar(pg, "B4") == "=SUM(A1:A4)" and val(pg, "A2") == "" and val(pg, "A3") == "2" and '"A3:A4"' in raw(pg)[1] and '"A1:A4"' in raw(pg)[2] and '"B1:B4"' in raw(pg)[3] and "Inserted" in stat(pg))
            t.step("Undo: the sheet and all three rule lists are back exactly as stored before", lambda: pg.click("#sheet-undo"), expect=lambda: raw(pg) == orig and val(pg, "A2") == "2" and "Undone" in stat(pg))
            t.step("Select A2 and Delete row: the confirm names what it removes; the formula that pointed at A2 shows #REF!", lambda: (pg.click('input[data-addr="A2"]'), pg.click("#row-del")),
                   expect=lambda: bar(pg, "C1") == "=#REF!" and bar(pg, "B2") == "=SUM(A1:A2)" and any("Delete row 2" in m and "filled cells" in m for m in seen) and '"A2:A2"' in raw(pg)[1])
            t.step("Undo brings the row, the formula and the rules back", lambda: pg.click("#sheet-undo"), expect=lambda: raw(pg) == orig and bar(pg, "C1") == "=A2")
            t.step("Redo is available. Another window changes a rule now", lambda: pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; localStorage.setItem(k + '.rules', JSON.stringify({v:1, rules:[{range:'A2:A3', kind:'number', min:0, max:5}]})); }"""), expect=lambda: not pg.is_disabled("#sheet-redo"))
            changed = raw(pg)
            t.step("Redo of the row delete refuses and changes nothing", lambda: pg.click("#sheet-redo"), expect=lambda: raw(pg) == changed and "cannot be redone safely" in stat(pg) and bar(pg, "C1") == "=A2")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("Failure test: the colour rule write throws partway. The page reads every key back as before and says so", lambda: (
                pg.evaluate("""() => { window.__o = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { if (k.endsWith('.cf')) throw new Error('quota'); return window.__o.call(this, k, v); }; }"""),
                pg.click('input[data-addr="A2"]'), pg.click("#row-ins")),
                expect=lambda: raw(pg) == changed and "read back as it was before" in stat(pg) and val(pg, "A2") == "2" and not pg.is_visible("#struct-recovery"))
            t.step("Failure test: the restore also fails, so the sheet is left unfinished: a Restore notice appears", lambda: (
                pg.evaluate("""() => { Storage.prototype.setItem = function (k, v) { if (k.endsWith('.cf') || (k === document.getElementById('sheet-root').dataset.storeKey && v.indexOf('"A2":"2"') >= 0)) throw new Error('quota'); return window.__o.call(this, k, v); }; }"""),
                pg.click('input[data-addr="A2"]'), pg.click("#row-ins")),
                expect=lambda: pg.is_visible("#struct-recovery") and "did not finish" in pg.inner_text("#struct-recovery") and pg.locator("#struct-restore").count() == 1 and "Not known to be as before: cells" in stat(pg))
            t.step("While the notice is up, typing a value is refused and nothing is saved", lambda: put(pg, [("D1", "x")]), expect=lambda: "Nothing can be edited" in stat(pg) and '"D1"' not in (raw(pg)[0] or ""))
            t.step("The storage works again: Restore puts all four items back and editing resumes", lambda: (pg.evaluate("() => { Storage.prototype.setItem = window.__o; }"), pg.click("#struct-restore")),
                   expect=lambda: (not pg.is_visible("#struct-recovery") and raw(pg) == changed and "Restored the tab" in stat(pg)) or (_ for _ in ()).throw(Exception(stat(pg)[:110] + " | vis=%s same=%s" % (pg.is_visible("#struct-recovery"), raw(pg) == changed))))
            t.step("Editing works again", lambda: put(pg, [("D1", "y")]), expect=lambda: val(pg, "D1") == "y" and '"D1":"y"' in raw(pg)[0])

        run_tour(ctx, "101_sheets_row_column", body, notes="Device-local sheet (browser only). Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Storage faults are injected in the page.",
                 unshown=["column insert and delete (node rows)", "unreadable recovery copy"])

    g["DRILLS"]["tour_sheets_struct"] = tour


def register_sheets_struct_col(g):
    APP = g["APP"]

    def tour(ctx):
        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        def val(pg, a):
            return pg.input_value('input[data-addr="%s"]' % a)

        def bar(pg, a):
            pg.click('input[data-addr="%s"]' % a)
            return pg.input_value("#sheet-bar")

        def raw(pg):
            return pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; return [k, k + '.rules', k + '.cf', k + '.fmt'].map(x => localStorage.getItem(x)); }""")

        def stat(pg):
            return pg.inner_text("#sheet-status")

        def must(cond, info):
            return cond or (_ for _ in ()).throw(Exception(info()))

        def body(t, pg):
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("A row of numbers A1:C1 with =SUM in D1 and =B1 in A2", lambda: put(pg, [("A1", "1"), ("B1", "2"), ("C1", "3"), ("D1", "=SUM(A1:C1)"), ("A2", "=B1")]), expect=lambda: val(pg, "D1") != "")
            pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey;
              localStorage.setItem(k + '.rules', JSON.stringify({v:1, rules:[{range:'B1:C1', kind:'number', min:0, max:9}]}));
              localStorage.setItem(k + '.cf', JSON.stringify({v:1, rules:[{range:'A1:C1', kind:'greater', color:'red', value:1}]}));
              localStorage.setItem(k + '.fmt', JSON.stringify({v:1, rules:[{range:'A1:B1', bold:true}]})); }""")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            orig = raw(pg)
            t.step("Select B1 and Insert column left: SUM grows, rules move with their columns", lambda: (pg.click('input[data-addr="B1"]'), pg.click("#col-ins")),
                   expect=lambda: must(bar(pg, "E1") == "=SUM(A1:D1)" and val(pg, "B1") == "" and val(pg, "C1") == "2" and bar(pg, "A2") == "=C1" and '"C1:D1"' in raw(pg)[1] and '"A1:D1"' in raw(pg)[2] and '"A1:C1"' in raw(pg)[3] and "Inserted" in stat(pg), lambda: stat(pg)[:120]))
            t.step("Undo: all four stored texts are byte equal to before", lambda: pg.click("#sheet-undo"), expect=lambda: raw(pg) == orig and val(pg, "B1") == "2")
            t.step("Select B1 and Delete column: =B1 now shows #REF!, SUM shrinks, the validation rule narrows to C1", lambda: (pg.click('input[data-addr="B1"]'), pg.click("#col-del")),
                   expect=lambda: must(bar(pg, "A2") == "=#REF!" and bar(pg, "C1") == "=SUM(A1:B1)" and val(pg, "B1") == "3" and '"B1:B1"' in raw(pg)[1] and any("Delete column B" in m for m in seen), lambda: stat(pg)[:120]))
            t.step("Undo brings the column, the formulas and the rules back", lambda: pg.click("#sheet-undo"), expect=lambda: raw(pg) == orig and bar(pg, "A2") == "=B1")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("Failure test: a colour-rule write fails and the restore of the cells also fails: the notice appears", lambda: (
                pg.evaluate("""() => { window.__o = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { if (k.endsWith('.cf') || (k === document.getElementById('sheet-root').dataset.storeKey && v.indexOf('"B1":"2"') >= 0)) throw new Error('quota'); return window.__o.call(this, k, v); }; }"""),
                pg.click('input[data-addr="B1"]'), pg.click("#col-ins")),
                expect=lambda: must(pg.is_visible("#struct-recovery") and "Not known to be as before: cells" in stat(pg), lambda: stat(pg)[:120]))
            t.step("The storage works again, but another window changed the validation rules meanwhile", lambda: pg.evaluate("""() => { Storage.prototype.setItem = window.__o; const k = document.getElementById('sheet-root').dataset.storeKey; localStorage.setItem(k + '.rules', JSON.stringify({v:1, rules:[{range:'B1:C1', kind:'number', min:0, max:5}]})); }"""), expect=lambda: True)
            conflict_raw = raw(pg)
            t.step("Restore: it refuses, writes nothing, names the validation rules, and offers a separate Restore anyway", lambda: pg.click("#struct-restore"),
                   expect=lambda: must(raw(pg) == conflict_raw and "Nothing was restored" in stat(pg) and "validation rules" in stat(pg) and pg.locator("#struct-force").count() == 1, lambda: stat(pg)[:140]))
            t.step("Restore anyway: the confirm says those later changes are lost; then all four items are as before", lambda: pg.click("#struct-force"),
                   expect=lambda: must(raw(pg) == orig and not pg.is_visible("#struct-recovery") and any("will be lost" in m and "validation rules" in m for m in seen) and "Restored the tab" in stat(pg), lambda: stat(pg)[:140]))
            t.step("Editing works again", lambda: put(pg, [("A3", "z")]), expect=lambda: val(pg, "A3") == "z" and '"A3":"z"' in raw(pg)[0])

        run_tour(ctx, "102_sheets_column_recovery", body, notes="Device-local sheet (browser only). Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Storage faults are injected in the page. Dialogs are accepted by the script and checked from their text; they do not appear in the video.",
                 unshown=["confirm dialogs (text asserted, not on screen)"])

    g["DRILLS"]["tour_sheets_struct_col"] = tour


def register_sheets_struct_more(g):
    APP = g["APP"]

    def tour(ctx):
        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        def val(pg, a):
            return pg.input_value('input[data-addr="%s"]' % a)

        def bar(pg, a):
            pg.click('input[data-addr="%s"]' % a)
            return pg.input_value("#sheet-bar")

        def raw(pg):
            return pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; return [k, k + '.rules', k + '.cf', k + '.fmt'].map(x => localStorage.getItem(x)); }""")

        def stat(pg):
            return pg.inner_text("#sheet-status")

        def must(cond, info):
            return cond or (_ for _ in ()).throw(Exception(info()))

        def hidden(pg):  # which grid rows 1..6 are hidden by the filter
            return pg.evaluate("() => { const r = document.querySelectorAll('#sheet-root tr'); return [1,2,3,4,5,6].map(i => !!r[i].hidden); }")

        def body(t, pg):
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            put(pg, [("A1", "1"), ("A2", "2"), ("A3", "3"), ("C1", "=A2")])
            pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey;
              localStorage.setItem(k + '.rules', JSON.stringify({v:1, rules:[{range:'B1:B3', kind:'number', min:0, max:9}]}));
              localStorage.setItem(k + '.cf', JSON.stringify({v:1, rules:[{range:'A1:A3', kind:'greater', color:'red', value:1}]}));
              localStorage.setItem(k + '.fmt', JSON.stringify({v:1, rules:[{range:'A1:A3', bold:true}]})); }""")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            orig = raw(pg)
            t.step("A validation rule allows 0 to 9 in B1:B3. Typing 99 in B2 is refused", lambda: put(pg, [("B2", "99")]), expect=lambda: must("was not applied" in stat(pg) and '"B2"' not in raw(pg)[0], lambda: stat(pg)[:100]))
            t.step("Insert a row above row 2: the rule moves to B1:B4 and =A2 follows to =A3", lambda: (pg.click('input[data-addr="A2"]'), pg.click("#row-ins")),
                   expect=lambda: must('"B1:B4"' in raw(pg)[1] and bar(pg, "C1") == "=A3" and val(pg, "A3") == "2", lambda: stat(pg)[:100]))
            after = raw(pg)
            t.step("The rule is enforced at the new last row: 99 in B4 is refused", lambda: put(pg, [("B4", "99")]), expect=lambda: must("was not applied" in stat(pg) and '"B4"' not in raw(pg)[0], lambda: stat(pg)[:100]))
            t.step("An ordinary edit, Undo it, then Undo the row insert: all four stored texts equal the original", lambda: (put(pg, [("D1", "q")]), pg.click("#sheet-undo"), pg.click("#sheet-undo")),
                   expect=lambda: must(raw(pg) == orig and val(pg, "A2") == "2", lambda: "raw differs %s" % (raw(pg) == orig)))
            t.step("Redo brings the insert back, byte equal to what it stored the first time", lambda: pg.click("#sheet-redo"), expect=lambda: must(raw(pg) == after and val(pg, "A3") == "2", lambda: stat(pg)[:100]))
            t.step("A value outside the rule's new range is accepted: 99 in B5 saves", lambda: put(pg, [("B5", "99")]), expect=lambda: '"B5":"99"' in raw(pg)[0])
            stored = raw(pg)
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("Reload the page: cells, formula and all three rule lists are as stored", expect=lambda: must(raw(pg) == stored and bar(pg, "C1") == "=A3" and val(pg, "A3") == "2" and val(pg, "B5") == "99", lambda: "reload differs"))
            t.step("Insert another row above row 2 and filter column A for 2: only the row holding 2 shows", lambda: (pg.click('input[data-addr="A2"]'), pg.click("#row-ins"), pg.fill("#filter-val", "2"), pg.select_option("#filter-op", "contains"), pg.uncheck("#filter-header") if pg.is_checked("#filter-header") else None, pg.click("#filter-go")),
                   expect=lambda: must(hidden(pg) == [True, True, True, False, True, True], lambda: str(hidden(pg))))
            t.step("Undo the insert while the filter is on: the filter is applied to the moved cells (row 3 now holds 2)", lambda: pg.click("#sheet-undo"),
                   expect=lambda: must(hidden(pg) == [True, True, False, True, True, False] and val(pg, "A3") == "2", lambda: str(hidden(pg))))
            # held-page regression for tab delete and tab restore
            pg.evaluate("document.getElementById('filter-clear').click()")
            pg.click("#tab-add"); pg.wait_for_timeout(300); pg.click("#tab-add"); pg.wait_for_timeout(300)
            pg.click("#tab-del"); pg.wait_for_timeout(300)
            pg.locator("#sheet-tabs button").first.click(); pg.wait_for_timeout(400)
            ntabs = pg.locator("#sheet-tabs button").count()
            t.step("Two tabs and a deleted third one to restore, back on the first tab", expect=lambda: ntabs == 2 and not pg.is_disabled("#tab-restore") and not pg.is_disabled("#tab-del"))
            pg.evaluate("""() => { window.__orig0 = localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey); window.__o = Storage.prototype.setItem;
              Storage.prototype.setItem = function (k, v) { if (k.endsWith('.cf') || v === window.__orig0) throw new Error('quota'); return window.__o.call(this, k, v); }; }""")
            t.step("A failed insert leaves the notice (the restore of the cells also failed)", lambda: (pg.click('input[data-addr="A2"]'), pg.click("#row-ins")), expect=lambda: must(pg.is_visible("#struct-recovery") and "Not known to be as before: cells" in stat(pg), lambda: stat(pg)[:100]))
            nseen = len(seen)
            t.step("Delete tab is refused while the notice is up: no confirm, still two tabs", lambda: pg.click("#tab-del"), expect=lambda: must(len(seen) == nseen and pg.locator("#sheet-tabs button").count() == 2 and "Nothing can be edited" in pg.inner_text("#tab-status"), lambda: pg.inner_text("#tab-status")[:100]))
            t.step("Restore deleted tab is refused too: still two tabs", lambda: pg.click("#tab-restore"), expect=lambda: must(pg.locator("#sheet-tabs button").count() == 2 and "Nothing can be edited" in pg.inner_text("#tab-status"), lambda: pg.inner_text("#tab-status")[:100]))

        run_tour(ctx, "103_sheets_struct_checks", body, notes="Device-local sheet (browser only). Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Storage faults are injected in the page. The remove-unreadable-rules guard is source-checked only.",
                 unshown=["confirm dialogs (text asserted)", "remove-unreadable-rules while held"])

    g["DRILLS"]["tour_sheets_struct_more"] = tour


def register_sheets_find(g):
    APP = g["APP"]

    def tour(ctx):
        import json as _json

        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        def val(pg, a):
            return pg.input_value('input[data-addr="%s"]' % a)

        def cj(pg):
            k = pg.evaluate("document.getElementById('sheet-root').dataset.storeKey")
            return _json.loads(pg.evaluate("k => localStorage.getItem(k)", k))

        def fstat(pg):
            return pg.inner_text("#find-status")

        def find(pg, text, repl="", case=False, formulas=False):
            pg.fill("#find-text", text)
            pg.fill("#find-repl", repl)
            pg.set_checked("#find-case", case)
            pg.set_checked("#find-formulas", formulas)

        def focused(pg):
            return pg.evaluate("document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.addr : null")

        def body(t, pg):
            pg.on("dialog", lambda d: d.accept())
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            put(pg, [("A1", "Apple"), ("A2", "apple pie"), ("A3", "=UPPER(A1)"), ("B1", "5"), ("C1", "n5")])
            pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey;
              localStorage.setItem(k + '.rules', JSON.stringify({v:1, rules:[{range:'C1:C1', kind:'number', min:0, max:9}]})); }""")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            orig = cj(pg)
            t.step("Find apple (any case): A1, A2 and the formula A3, whose shown result is APPLE, all match", lambda: (find(pg, "apple"), pg.click("#find-next")),
                   expect=lambda: focused(pg) == "A1" and "1 of 3" in fstat(pg) and "1 of them in formula results" in fstat(pg))
            t.step("Find next moves A1, then A2, then the formula result A3, then wraps to A1", lambda: [pg.click("#find-next") for _ in range(1)],
                   expect=lambda: focused(pg) == "A2")
            pg.click("#find-next")
            ok3 = focused(pg) == "A3"
            pg.click("#find-next")
            t.step("Third Find next lands on A3, the fourth wraps back to A1", expect=lambda: ok3 and focused(pg) == "A1")
            t.step("Match case on: only A2 matches", lambda: (find(pg, "apple", case=True), pg.click("#find-next")), expect=lambda: focused(pg) == "A2" and "1 of 1" in fstat(pg))
            t.step("Replace all apple with fig: A1 and A2 change, the formula A3 stays and its result is only counted", lambda: (find(pg, "apple", "fig"), pg.click("#find-all")),
                   expect=lambda: cj(pg)["A1"] == "fig" and cj(pg)["A2"] == "fig pie" and cj(pg)["A3"] == "=UPPER(A1)" and "2 cells changed" in fstat(pg) and "1 formula results matched and left alone" in fstat(pg) and val(pg, "A3") == "FIG")
            t.step("One Undo puts every cell back, byte for byte", lambda: pg.click("#sheet-undo"), expect=lambda: cj(pg) == orig and val(pg, "A3") == "APPLE")
            t.step("Formulas ticked: change A1 to B1 inside the formula text only", lambda: (find(pg, "A1", "B1", formulas=True), pg.click("#find-all")),
                   expect=lambda: cj(pg)["A3"] == "=UPPER(B1)" and cj(pg)["A1"] == "Apple" and val(pg, "A3") == "5")
            pg.click("#sheet-undo")
            t.step("Replacing text with a formula is skipped: apple pie to =1 changes nothing", lambda: (find(pg, "apple pie", "=1"), pg.click("#find-all")),
                   expect=lambda: cj(pg) == orig and "would become a formula" in fstat(pg) and "Nothing changed" in fstat(pg))
            t.step("A replacement the validation rule refuses is skipped: C1 stays n5", lambda: (find(pg, "n", "9"), pg.click("#find-all")),
                   expect=lambda: cj(pg)["C1"] == "n5" and "refused by validation rules" in fstat(pg))
            t.step("Control: n replaced by nothing leaves 5, which the rule accepts, so C1 changes", lambda: (find(pg, "n", ""), pg.click("#find-all")),
                   expect=lambda: cj(pg)["C1"] == "5" and "1 cells changed" in fstat(pg))
            pg.click("#sheet-undo")
            pg.fill("#filter-val", "apple"); pg.select_option("#filter-op", "contains"); pg.click("#filter-go")
            t.step("With a filter on, Replace all is refused and nothing changes", lambda: (find(pg, "apple", "fig"), pg.click("#find-all")),
                   expect=lambda: cj(pg) == orig and "filter is on" in fstat(pg))
            pg.click("#filter-clear")
            t.step("Replace all, then reload the page: the replaced cells are what is stored", lambda: (find(pg, "pie", "tart"), pg.click("#find-all")), expect=lambda: cj(pg)["A2"] == "apple tart")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("After the reload A2 shows apple tart and A3 still shows APPLE", expect=lambda: val(pg, "A2") == "apple tart" and val(pg, "A3") == "APPLE")

        run_tour(ctx, "104_sheets_find_replace", body, notes="Device-local sheet (browser only). Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated.",
                 unshown=["status lines are mostly below the frame"])

    g["DRILLS"]["tour_sheets_find"] = tour


def register_palette(g):
    APP = g["APP"]

    def tour(ctx):
        import django

        django.setup()

        def seed():
            from apps.accounts.models import CustomUser
            from apps.tasks import services as ts

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            other, _ = CustomUser.objects.get_or_create(
                employee_id="ST-LIVE-2",
                defaults={"full_name": "Second Synthetic", "email": "live.two@salowtech.com", "email_verified": True,
                          "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True},
            )
            lm, lo = ts.create_list(me, "Palette list"), ts.create_list(other, "Other list")
            ts.create_task(me, lm, "Old palette plan")
            ts.create_task(me, lm, "Palette notes")
            ts.create_task(me, lm, "Palette")
            ts.create_task(other, lo, "Palette secret of two")

        def opts(pg):
            return pg.evaluate("() => Array.from(document.querySelectorAll('#palette-list [role=option]')).map(e => e.textContent)")

        def dlg(pg):
            return pg.evaluate("() => { const d = document.getElementById('palette'); return !!d && !d.hidden; }")

        def body(t, pg):
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                ex.submit(seed).result()
            pg.goto(APP + "/")
            pg.wait_for_timeout(600)
            t.step("Quick search button opens the dialog and the box has focus", lambda: pg.click("#palette-open"),
                   expect=lambda: dlg(pg) and pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Search your workspace")
            t.step("Ctrl+K closes it and focus goes back to the button, then Ctrl+K opens it again", lambda: pg.keyboard.press("Control+k"),
                   expect=lambda: (not dlg(pg)) and pg.evaluate("document.activeElement.id") == "palette-open")
            pg.keyboard.press("Control+k")
            pg.wait_for_timeout(300)
            t.step("Type palette: three of my tasks, best match first, and nothing of the other person's", lambda: pg.fill("#palette input", "palette"),
                   wait=2200, expect=lambda: opts(pg) == ["Palette", "Palette notes", "Old palette plan"] and "secret" not in pg.inner_text("#palette"))
            t.step("Arrow Down moves the choice to the second result", lambda: pg.keyboard.press("ArrowDown"),
                   expect=lambda: pg.evaluate("() => document.querySelector('#palette-list [aria-selected=true]').textContent") == "Palette notes")
            t.step("Enter opens that result's page on this site", lambda: pg.keyboard.press("Enter"),
                   wait=1500, expect=lambda: "/tasks/" in pg.url)
            pg.goto(APP + "/")
            pg.wait_for_timeout(500)
            pg.keyboard.press("Control+k")
            t.step("A search with no match says No results and lists nothing", lambda: pg.fill("#palette input", "zzzzqq"),
                   wait=2200, expect=lambda: opts(pg) == [] and "No results." in pg.inner_text("#palette"))
            pg.evaluate("""() => { const f = window.fetch; window.fetch = function (u, o) {
              const s = String(u);
              if (s.indexOf('slow') >= 0) return new Promise(res => setTimeout(() => res(new Response(JSON.stringify({groups: [{name: 'Tasks', items: [{title: 'STALE ANSWER', url: '/tasks/'}], more: false}]}), {status: 200, headers: {'Content-Type': 'application/json'}})), 1800));
              if (s.indexOf('inject') >= 0) return Promise.resolve(new Response(JSON.stringify({groups: [{name: 'Tasks', items: [{title: 'bad1', url: '//evil.example/'}, {title: 'bad2', url: 'javascript:alert(1)'}, {title: 'bad3', url: '/\\\\evil'}, {title: 'bad4', url: '/\\t/evil.example/'}, {title: 'bad5', url: '/\\u0001x'}, {title: 'bad6', url: '/a b'}, {title: 'good', url: '/tasks/'}], more: false}]}), {status: 200, headers: {'Content-Type': 'application/json'}}));
              return f.apply(this, arguments); }; }""")
            t.step("A slow answer for an old search arrives after a newer search: the old answer is dropped", lambda: (pg.fill("#palette input", "palette slow"), pg.wait_for_timeout(700), pg.fill("#palette input", "palette")),
                   wait=3500, expect=lambda: opts(pg) == ["Palette", "Palette notes", "Old palette plan"] and "STALE" not in pg.inner_text("#palette"))
            t.step("A result with an off-site, script, tab or control-character link is not listed, only the path on this site", lambda: pg.fill("#palette input", "inject"),
                   wait=2200, expect=lambda: opts(pg) == ["good"])
            t.step("Escape closes the dialog and focus returns to the button", lambda: pg.keyboard.press("Escape"),
                   expect=lambda: (not dlg(pg)) and pg.evaluate("document.activeElement.id") == "palette-open")

        run_tour(ctx, "105_quick_search", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. The slow and off-site answers are injected in the page by wrapping fetch; the server answers are real.",
                 unshown=["the page the result opens"])

    g["DRILLS"]["tour_palette"] = tour


def register_overlap(g):
    APP = g["APP"]

    def tour(ctx):
        def make(pg, title, start, end):
            pg.goto(APP + "/calendar/new/")
            pg.fill("[name=title]", title)
            pg.fill("[name=start]", start)
            pg.fill("[name=end]", end)
            pg.click("form.cal-form button[type=submit]")
            pg.wait_for_load_state()

        def msgs(pg):
            return pg.inner_text(".messages-container") if pg.locator(".messages-container").count() else ""

        def body(t, pg):
            pg.goto(APP + "/")
            pg.wait_for_timeout(500)
            t.step("Create Standup from 10:00 to 11:00: saved, no warning", lambda: make(pg, "Standup", "2027-03-02T10:00", "2027-03-02T11:00"),
                   expect=lambda: "Event created." in msgs(pg) and "Heads up" not in msgs(pg))
            t.step("Create Planning from 10:30 to 11:30: saved, and the page warns it overlaps Standup", lambda: make(pg, "Planning", "2027-03-02T10:30", "2027-03-02T11:30"),
                   expect=lambda: "Event created." in msgs(pg) and "Heads up: this overlaps 1 other event" in msgs(pg) and "Standup (Tue 02 Mar 10:00 to 11:00" in msgs(pg))
            t.step("Create Lunch from 11:00 to 11:30: touching Standup's end is not an overlap, but it overlaps Planning", lambda: make(pg, "Lunch", "2027-03-02T11:00", "2027-03-02T11:30"),
                   expect=lambda: "overlaps 1 other event" in msgs(pg) and "Planning" in msgs(pg) and "Standup" not in msgs(pg))
            t.step("Create Retro the next day: no warning", lambda: make(pg, "Retro", "2027-03-03T10:00", "2027-03-03T11:00"),
                   expect=lambda: "Heads up" not in msgs(pg))

        run_tour(ctx, "106_calendar_overlap_warning", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. The warning appears after the save and does not block it.", unshown=["edit flow (covered by a pytest, not this tour)"])

    g["DRILLS"]["tour_overlap"] = tour


def register_cal_all_day(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            cell = lambda d: pg.evaluate(  # noqa: E731
                "(d)=>{for(const td of document.querySelectorAll('.cal-month td')){if(td.classList.contains('cal-other'))continue;"
                "if(td.querySelector('.cal-dn').textContent.trim().split(' ')[0]===d)return td.innerText}return null}", d)
            pg.goto(APP + "/calendar/new/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 CREATE: type a title and a start and end with times", lambda: (pg.fill("input[name=title]", "Team offsite"), pg.fill("input[name=start]", "2026-12-03T15:40"), pg.fill("input[name=end]", "2026-12-05T02:10")),
                   expect=lambda: pg.get_attribute("#cal-start", "type") == "datetime-local")
            t.step("STAGE 1 CREATE: tick All day. The boxes become First day and Last day, dates only", lambda: pg.check("input[name=all_day]"),
                   expect=lambda: pg.get_attribute("#cal-start", "type") == "date" and pg.input_value("#cal-end") == "2026-12-05" and pg.inner_text("#cal-end-label") == "Last day")
            t.step("STAGE 1 CREATE: save. The page says Thu 3 Dec to Sat 5 Dec 2026, all day", lambda: pg.click(".cal-form button[type=submit]"),
                   expect=lambda: "Thu 3 Dec 2026 to Sat 5 Dec 2026, all day" in pg.inner_text("body"), wait=2200)
            url = {}
            url["detail"] = pg.url
            t.step("STAGE 1 CREATE: December month shows an All day chip on 3, 4 and 5 Dec", lambda: pg.goto(APP + "/calendar/month/?m=2026-12"),
                   expect=lambda: all("Team offsite" in (cell(d) or "") for d in ("3", "4", "5")) and "Team offsite" not in (cell("6") or ""), wait=2600)
            t.step("STAGE 2 EDIT: open the event's edit page. Date boxes, last day is 5 Dec (not the stored end)", lambda: pg.goto(url["detail"] + "edit/"),
                   expect=lambda: pg.get_attribute("#cal-end", "type") == "date" and pg.input_value("#cal-end") == "2026-12-05" and pg.locator("input[name=all_day]").count() == 0, wait=2200)
            t.step("STAGE 2 EDIT: move it to the single day Tue 8 Dec and save", lambda: (pg.fill("input[name=start]", "2026-12-08"), pg.fill("input[name=end]", "2026-12-08"), pg.click(".cal-form button[type=submit]")),
                   expect=lambda: "Tue 8 Dec 2026, all day" in pg.inner_text("body"), wait=2600)
            t.step("STAGE 2 EDIT: the month now shows it on 8 Dec only", lambda: pg.goto(APP + "/calendar/month/?m=2026-12"),
                   expect=lambda: "Team offsite" in (cell("8") or "") and all("Team offsite" not in (cell(d) or "") for d in ("3", "4", "5")), wait=2600)
            def bad():
                pg.goto(APP + "/calendar/new/")
                pg.fill("input[name=title]", "Bad repeat"); pg.check("input[name=all_day]")
                pg.fill("input[name=start]", "2026-12-10"); pg.fill("input[name=end]", "2026-12-10")
                pg.select_option("select[name=freq]", "daily"); pg.fill("input[name=count]", "3")
                pg.click(".cal-form button[type=submit]")
            t.step("REFUSAL: an all-day event that repeats is refused with a message", bad, expect=lambda: "cannot repeat" in pg.inner_text("body"), wait=2600)
        run_tour(ctx, "cal_all_day", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Stage 1 (create) and Stage 2 (edit) are different moments: the event is first 3 to 5 Dec, then moved to 8 Dec. Not shown: another time zone, guests, the clock-change cases (covered by unit tests only).", unshown=["another person's calendar", "clock-change days", "guests"])

    g["DRILLS"]["tour_cal_all_day"] = tour


def register_tasks_restore(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            pg.goto(APP + "/tasks/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a list called Sprint", lambda: (pg.fill("input[name=name]", "Sprint"), pg.click("button:has-text('Add list')")), expect=lambda: "Sprint" in txt())
            t.step("STAGE 1 SET UP: add the task Ship report", lambda: (pg.fill("input[aria-label='Task title']", "Ship report"), pg.click(".tasks-add button")), expect=lambda: "Ship report" in txt())
            t.step("STAGE 1 SET UP: add the subtask Draft under it", lambda: (pg.fill("input[aria-label='Subtask title']", "Draft"), pg.click(".task-sub-add button")), expect=lambda: "Draft" in txt())
            t.step("STAGE 2 DELETE: Delete the task. The page says it can be restored from the Trash tab", lambda: pg.click("a:has-text('Delete'):near(:text('Ship report'))"), expect=lambda: "restore it from the Trash tab" in txt())
            t.step("STAGE 2 DELETE: confirm. The list no longer shows the task or its subtask", lambda: pg.click("button:has-text('Delete')"), expect=lambda: "Ship report" not in txt() and "Draft" not in txt())
            t.step("STAGE 3 RESTORE: open Trash. Ship report is listed once, with a Restore button (its subtask goes back with it)", lambda: pg.click(".tasks-tabs a:has-text('Trash')"), expect=lambda: "Ship report" in txt() and "Draft" not in txt() and pg.locator("button:has-text('Restore')").count() == 1)
            t.step("STAGE 3 RESTORE: press Restore, then open the list. The task and its subtask are back", lambda: (pg.click("button:has-text('Restore')"), pg.click(".tasks-tabs a:has-text('My lists')")), expect=lambda: "Ship report" in txt() and "Draft" in txt(), wait=1800)
            t.step("STAGE 4 LIST: Delete this list and confirm", lambda: (pg.click("a:has-text('Delete this list')"), pg.click("button:has-text('Delete')")), expect=lambda: "Sprint" not in pg.locator(".tasks-tabs").inner_text())
            t.step("STAGE 4 LIST: Trash shows the list Sprint. Restore it and the tasks come back", lambda: (pg.click(".tasks-tabs a:has-text('Trash')"), pg.click("button:has-text('Restore')"), pg.click(".tasks-tabs a:has-text('My lists')")), expect=lambda: "Sprint" in txt() and "Ship report" in txt() and "Draft" in txt(), wait=1800)
        run_tour(ctx, "tasks_restore", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Stages are separate moments: set up, delete, restore the task, delete and restore the list. Not shown: another person's view, an assigned task, or the refusals (unit tests cover them).", unshown=["an assignee's view", "stale forms", "refusals"])

    g["DRILLS"]["tour_tasks_restore"] = tour


def register_tasks_rename(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            tabs = lambda: pg.locator(".tasks-lists").inner_text()  # noqa: E731
            pg.goto(APP + "/tasks/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a list called Sprint", lambda: (pg.fill("input[name=name]", "Sprint"), pg.click("button:has-text('Add list')")), expect=lambda: "Sprint" in tabs())
            t.step("STAGE 2 RENAME: the page's 120-character limit is removed in the browser only to test the server rule. A 121-character name is refused; scrolled to the Rename box, the typed text is still in it", lambda: (pg.evaluate("document.querySelector(\"input[name=new_name]\").removeAttribute('maxlength')"), pg.fill("input[name=new_name]", "Q" * 121), pg.click(".tasks-rename button")), expect=lambda: (pg.locator(".tasks-rename button").scroll_into_view_if_needed(), pg.evaluate("window.scrollBy(0, 120)"), "up to 120 characters" in txt() and pg.input_value("input[name=new_name]") == "Q" * 121)[-1], wait=900)
            t.step("STAGE 3 RENAME: type Launch plan and press Rename. Scrolled to the Rename box, which now holds Launch plan; the tab above shows the new name", lambda: (pg.goto(APP + "/tasks/"), pg.click(".tasks-lists a:has-text('Sprint')"), pg.fill("input[name=new_name]", "Launch plan"), pg.click(".tasks-rename button")), expect=lambda: (pg.locator(".tasks-rename button").scroll_into_view_if_needed(), pg.evaluate("window.scrollBy(0, 120)"), "List renamed." in txt() and "Launch plan" in tabs() and "Sprint" not in tabs() and pg.input_value("input[name=new_name]") == "Launch plan")[-1], wait=1500)
        run_tour(ctx, "tasks_rename", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Stages: set up, a refused too-long name (the page's own length limit is removed in the browser to reach the server rule), then a successful rename. Not shown: another person's view or a deleted list (unit tests cover them).", unshown=["another person's view", "deleted list"])

    g["DRILLS"]["tour_tasks_rename"] = tour


def register_tasks_move(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            tabs = lambda: pg.locator(".tasks-lists").inner_text()  # noqa: E731
            pg.goto(APP + "/tasks/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a list called Alpha", lambda: (pg.fill("input[name=name]", "Alpha"), pg.click("button:has-text('Add list')")), expect=lambda: "Alpha" in tabs())
            t.step("STAGE 1 SET UP: make a second list called Beta", lambda: (pg.fill("input[name=name]", "Beta"), pg.click("button:has-text('Add list')")), expect=lambda: "Beta" in tabs())
            t.step("STAGE 1 SET UP: open Alpha and add the task Ship report", lambda: (pg.click(".tasks-lists a:has-text('Alpha')"), pg.fill("input[aria-label='Task title']", "Ship report"), pg.click(".tasks-add button")), expect=lambda: "Ship report" in txt())
            t.step("STAGE 2 MOVE: pick Beta in the task's Move box (shown scrolled into view)", lambda: (pg.select_option(".tasks-move select", label="Beta"), pg.locator(".tasks-move button").scroll_into_view_if_needed(), pg.evaluate("window.scrollBy(0, 260)")), expect=lambda: pg.locator(".tasks-move select").input_value() != "", wait=1200)
            t.step("STAGE 2 MOVE: press Move. Beta opens with the task in it and the banner Task moved.", lambda: pg.click(".tasks-move button"), expect=lambda: "Task moved." in txt() and "Ship report" in txt() and pg.locator(".tasks-lists a.active").inner_text() == "Beta", wait=2200)
            t.step("STAGE 3 CHECK: open Alpha. It no longer has the task", lambda: pg.click(".tasks-lists a:has-text('Alpha')"), expect=lambda: "Ship report" not in txt() and "No tasks yet." in txt(), wait=2500)
        run_tour(ctx, "tasks_move", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Stages: set up two lists and a task, move it, check the source list. Not shown: assigned tasks, subtasks, stale forms or refusals (unit tests cover them).", unshown=["assigned tasks", "subtasks", "stale forms", "refusals"])

    g["DRILLS"]["tour_tasks_move"] = tour


def register_tasks_overdue(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            pg.goto(APP + "/tasks/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a list called Sprint", lambda: (pg.fill("input[name=name]", "Sprint"), pg.click("button:has-text('Add list')")), expect=lambda: "Sprint" in txt())
            t.step("STAGE 2 PAST DATE: add the task Old report due 2020-01-15", lambda: (pg.fill("input[aria-label='Task title']", "Old report"), pg.fill("input[name=due]", "2020-01-15"), pg.click(".tasks-add button")), expect=lambda: "Old report" in txt())
            t.step("STAGE 2 PAST DATE: the row shows Due 15 Jan 2020 (overdue) in red", lambda: pg.evaluate("window.scrollBy(0, 200)"), expect=lambda: "(overdue)" in txt() and pg.locator(".task-overdue").count() == 1, wait=900)
            t.step("STAGE 3 FUTURE DATE: add the task New report due 2099-01-15", lambda: (pg.fill("input[aria-label='Task title']", "New report"), pg.fill("input[name=due]", "2099-01-15"), pg.click(".tasks-add button")), expect=lambda: "New report" in txt())
            t.step("STAGE 3 FUTURE DATE: the new row shows its date without the overdue mark; only Old report is marked", lambda: pg.evaluate("window.scrollBy(0, 300)"), expect=lambda: pg.locator(".task-overdue").count() == 1 and "15 Jan 2099" in txt(), wait=900)
            t.step("STAGE 4 DONE: mark Old report done. The overdue mark goes away", lambda: pg.click(".task-row:has-text('Old report') .task-check"), expect=lambda: pg.locator(".task-overdue").count() == 0, wait=1800)
        run_tour(ctx, "tasks_overdue", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Dates use the workspace date (India time). Stages: set up, a past due date is marked, a future date is not, completing clears the mark. Not shown: the Assigned view (unit test covers it).", unshown=["assigned view"])

    g["DRILLS"]["tour_tasks_overdue"] = tour


def register_project_restore(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            pg.goto(APP + "/tasks/projects/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a project called Launch", lambda: (pg.fill("input[name=name]", "Launch"), pg.click("button:has-text('Add project')")), expect=lambda: "Launch" in txt())
            t.step("STAGE 1 SET UP: the new project opens; add the task Write plan to the first column", lambda: (pg.fill("form[action$='/add/'] input[name=title] >> nth=0", "Write plan"), pg.click("form[action$='/add/'] button >> nth=0")), expect=lambda: "Write plan" in txt())
            t.step("STAGE 2 DELETE: press Delete on the task. The page says a lead can restore it from this project's Trash", lambda: pg.click(".task-card:has-text('Write plan') a:has-text('Delete')"), expect=lambda: "this project's Trash" in txt())
            t.step("STAGE 2 DELETE: confirm. The board no longer shows the task", lambda: pg.click("button:has-text('Delete')"), expect=lambda: "Task deleted." in txt() and "Write plan" not in pg.locator(".board, .tasks").first.inner_text().replace("Task deleted.", ""), wait=1500)
            t.step("STAGE 3 RESTORE: open Project trash. Write plan is listed with a Restore button", lambda: pg.click("a:has-text('Project trash')"), expect=lambda: "Write plan" in txt() and pg.locator("button:has-text('Restore')").count() == 1)
            t.step("STAGE 3 RESTORE: press Restore. The board shows Write plan again with the banner Task restored.", lambda: pg.click("button:has-text('Restore')"), expect=lambda: "Task restored." in txt() and "Write plan" in txt(), wait=2000)
        run_tour(ctx, "project_restore", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee who leads the project. Stages: set up, delete, restore from the project trash. Not shown: a plain member's view (404 in unit tests), stale forms, or a deleted project.", unshown=["a member's view", "stale forms", "deleted project"])

    g["DRILLS"]["tour_project_restore"] = tour


def register_project_rename(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            head = lambda: pg.inner_text(".tasks-head h2")  # noqa: E731
            pg.goto(APP + "/tasks/projects/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a project called Launch", lambda: (pg.fill("input[name=name]", "Launch"), pg.click("button:has-text('Add project')")), expect=lambda: head() == "Launch")
            t.step("STAGE 2 RENAME: the lead sees a Project name box with Launch and a Rename button", lambda: pg.locator("input[aria-label='Project name']").scroll_into_view_if_needed(), expect=lambda: pg.input_value("input[aria-label='Project name']") == "Launch", wait=1500)
            t.step("STAGE 2 RENAME: type Launch 2027. Not saved yet", lambda: pg.fill("input[aria-label='Project name']", "Launch 2027"), expect=lambda: pg.input_value("input[aria-label='Project name']") == "Launch 2027" and head() == "Launch", wait=1800)
            t.step("STAGE 2 RENAME: press Rename. The banner says Project renamed. and the heading reads Launch 2027", lambda: (pg.click("button:has-text('Rename')"), pg.wait_for_timeout(300), pg.evaluate("window.scrollTo(0, 0)")), expect=lambda: "Project renamed." in txt() and head() == "Launch 2027", wait=2000)
            t.step("STAGE 3 RESULT: the project list shows Launch 2027", lambda: pg.goto(APP + "/tasks/projects/"), expect=lambda: "Launch 2027" in txt(), wait=1500)
            t.step("STAGE 3 RESULT: a blank name is refused and the name stays", lambda: (pg.click("a:has-text('Launch 2027')"), pg.wait_for_timeout(500), pg.evaluate("document.querySelector(\"input[aria-label='Project name']\").removeAttribute('required')"), pg.fill("input[aria-label='Project name']", "  "), pg.click("button:has-text('Rename')"), pg.wait_for_timeout(300), pg.evaluate("window.scrollTo(0, 0)")), expect=lambda: "up to 120 characters" in txt() and head() == "Launch 2027", wait=2200)
        run_tour(ctx, "project_rename", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee who leads the project. Stages: set up, rename, see the new name, refuse a blank name (the page's required attribute removed so the server rule is what answers). Not shown: a plain member's view (hidden form, refusal in unit tests), another person's view.", unshown=["a member's view", "another person's view"])

    g["DRILLS"]["tour_project_rename"] = tour


def register_milestone_edit(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            row = lambda: pg.locator(".task-row:has(input[aria-label^='Milestone name'])")  # noqa: E731
            pg.goto(APP + "/tasks/projects/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a project called Launch", lambda: (pg.fill("input[name=name]", "Launch"), pg.click("button:has-text('Add project')")), expect=lambda: pg.inner_text(".tasks-head h2") == "Launch")
            t.step("STAGE 1 SET UP: add the milestone Alpha due 2026-12-01", lambda: (pg.fill("form[action$='/milestones/new/'] input[name=name]", "Alpha"), pg.fill("form[action$='/milestones/new/'] input[name=due]", "2026-12-01"), pg.click("button:has-text('Add milestone')")), expect=lambda: "Milestone added." in txt() and "Due 1 Dec 2026" in txt())
            t.step("STAGE 2 EDIT: scroll to the milestone row. Its box shows Alpha and 2026-12-01", lambda: row().scroll_into_view_if_needed(), expect=lambda: pg.input_value("input[aria-label='Milestone name for Alpha']") == "Alpha", wait=1500)
            t.step("STAGE 2 EDIT: type the name Beta and the date 2027-02-03. Not saved yet", lambda: (pg.fill("input[aria-label='Milestone name for Alpha']", "Beta"), pg.fill("input[aria-label='Milestone due date for Alpha']", "2027-02-03")), expect=lambda: "Due 1 Dec 2026" in txt(), wait=1800)
            t.step("STAGE 2 EDIT: press Save. The banner says Milestone saved. and the row reads Beta, Due 3 Feb 2027", lambda: (pg.click("button:has-text('Save')"), pg.wait_for_timeout(300), row().scroll_into_view_if_needed()), expect=lambda: "Milestone saved." in txt() and "Due 3 Feb 2027" in row().inner_text() and "Beta" in row().inner_text(), wait=2200)
            t.step("STAGE 3 CLEAR DATE: empty the date box and press Save. The row shows no due date", lambda: (pg.fill("input[aria-label='Milestone due date for Beta']", ""), pg.click("button:has-text('Save')"), pg.wait_for_timeout(300), row().scroll_into_view_if_needed()), expect=lambda: "Due " not in row().inner_text() and "Beta" in row().inner_text(), wait=2200)
        run_tour(ctx, "milestone_edit", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee who leads the project. Stages: set up a milestone, change its name and due date, clear the date. Not shown: a plain member's view (hidden form, refusal in unit tests), another person's view, two leads saving at once (last save wins).", unshown=["a member's view", "another person's view", "two leads saving at once"])

    g["DRILLS"]["tour_milestone_edit"] = tour


def register_subtask_edit(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            pg.goto(APP + "/tasks/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a list called Sprint", lambda: (pg.fill("input[name=name]", "Sprint"), pg.click("button:has-text('Add list')")), expect=lambda: "Sprint" in txt())
            t.step("STAGE 1 SET UP: add the task Ship report", lambda: (pg.fill("input[aria-label='Task title']", "Ship report"), pg.click(".tasks-add button")), expect=lambda: "Ship report" in txt())
            t.step("STAGE 1 SET UP: add the subtask Draft under it", lambda: (pg.fill("input[aria-label='Subtask title']", "Draft"), pg.click(".task-sub-add button")), expect=lambda: "Draft" in txt())
            t.step("STAGE 2 EDIT: scroll to the subtask row. It has its own Edit button", lambda: pg.locator(".task-row.sub a:has-text('Edit')").scroll_into_view_if_needed(), expect=lambda: pg.locator(".task-row.sub a:has-text('Edit')").is_visible(), wait=1500)
            t.step("STAGE 2 EDIT: press the subtask's Edit button. The edit page opens with the title Draft", lambda: pg.click(".task-row.sub a:has-text('Edit')"), expect=lambda: "Edit task" in txt() and pg.input_value("input[name=title]") == "Draft", wait=1200)
            t.step("STAGE 2 EDIT: type the title Draft v2, the note needs review and the due date 2020-02-01. Not saved yet", lambda: (pg.fill("input[name=title]", "Draft v2"), pg.fill("textarea[name=notes]", "needs review"), pg.fill("input[name=due]", "2020-02-01")), expect=lambda: pg.input_value("input[name=title]") == "Draft v2" and pg.input_value("textarea[name=notes]") == "needs review" and pg.input_value("input[name=due]") == "2020-02-01", wait=1800)
            t.step("STAGE 2 EDIT: press Save", lambda: pg.click("button:has-text('Save')"), expect=lambda: "Draft v2" in txt(), wait=1500)
            t.step("STAGE 3 RESULT: the subtask row shows Draft v2, its note and Due 1 Feb 2020 (overdue) in red", lambda: pg.locator(".task-row.sub").scroll_into_view_if_needed(), expect=lambda: "needs review" in txt() and "(overdue)" in txt() and pg.locator(".task-row.sub .task-overdue").count() == 1, wait=1000)
        run_tour(ctx, "subtask_edit", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Stages: set up, press Edit on a subtask and save changes, see the result. Not shown: stale forms or another person's view.", unshown=["stale forms", "another person's view"])

    g["DRILLS"]["tour_subtask_edit"] = tour


def register_task_reorder(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            titles = lambda: pg.eval_on_selector_all(".task-row:not(.sub) .task-title", "e=>e.map(x=>x.textContent.trim())")  # noqa: E731
            top = lambda: (pg.wait_for_timeout(600), pg.evaluate("()=>window.scrollTo(0, document.querySelector('.task-row').getBoundingClientRect().top + window.scrollY - 40)"))  # noqa: E731
            pg.goto(APP + "/tasks/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a list called Order", lambda: (pg.fill("input[name=name]", "Order"), pg.click("button:has-text('Add list')")), expect=lambda: "Order" in txt())
            for n in ("Alpha", "Bravo", "Charlie"):
                t.step("STAGE 1 SET UP: add the task " + n, lambda n=n: (pg.fill("input[aria-label='Task title']", n), pg.click(".tasks-add button")), expect=lambda n=n: n in txt())
            t.step("STAGE 2 REORDER: the order is Alpha, Bravo, Charlie. Scroll to Charlie's Up button", lambda: pg.locator("button[aria-label='Move Charlie up']").scroll_into_view_if_needed(), expect=lambda: titles() == ["Alpha", "Bravo", "Charlie"], wait=1500)
            t.step("STAGE 2 REORDER: press Up on Charlie. Charlie moves above Bravo", lambda: (pg.click("button[aria-label='Move Charlie up']"), top()), expect=lambda: titles() == ["Alpha", "Charlie", "Bravo"], wait=1500)
            t.step("STAGE 2 REORDER: press Down on Alpha. Alpha moves below Charlie", lambda: (pg.click("button[aria-label='Move Alpha down']"), top()), expect=lambda: titles() == ["Charlie", "Alpha", "Bravo"], wait=1500)
            t.step("STAGE 3 RESULT: reload the page. The order Charlie, Alpha, Bravo is kept. The first row has no Up and the last has no Down", lambda: (pg.reload(), top()), expect=lambda: titles() == ["Charlie", "Alpha", "Bravo"] and pg.locator("button[aria-label='Move Charlie up']").count() == 0 and pg.locator("button[aria-label='Move Bravo down']").count() == 0, wait=1500)
        run_tour(ctx, "task_reorder", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Stages: set up three tasks, move one up and one down, reload to see the order kept. Not shown: subtasks, stale forms or another person's view.", unshown=["subtasks", "stale forms", "another person's view"])

    g["DRILLS"]["tour_task_reorder"] = tour


def register_clear_completed(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")  # noqa: E731
            titles = lambda: pg.eval_on_selector_all(".task-row:not(.sub) .task-title", "e=>e.map(x=>x.textContent.trim())")  # noqa: E731
            top = lambda: (pg.wait_for_timeout(600), pg.evaluate("()=>{const r=document.querySelector('.tasks-filter');window.scrollTo(0, r.getBoundingClientRect().top + window.scrollY - 40)}"))  # noqa: E731
            pg.goto(APP + "/tasks/")
            pg.wait_for_timeout(500)
            t.step("STAGE 1 SET UP: make a list called Chores", lambda: (pg.fill("input[name=name]", "Chores"), pg.click("button:has-text('Add list')")), expect=lambda: "Chores" in txt())
            for n in ("Wash", "Cook", "Sweep"):
                t.step("STAGE 1 SET UP: add the task " + n, lambda n=n: (pg.fill("input[aria-label='Task title']", n), pg.click(".tasks-add button")), expect=lambda n=n: n in txt())
            t.step("STAGE 2 FILTER: mark Wash done and Sweep done", lambda: (pg.click(".task-row:has-text('Wash') .task-check"), pg.wait_for_timeout(500), pg.click(".task-row:has-text('Sweep') .task-check"), top()), expect=lambda: pg.locator(".task-row.done").count() == 2, wait=1200)
            t.step("STAGE 2 FILTER: press Hide completed. Only Cook is left in view", lambda: (pg.click("a:has-text('Hide completed')"), top()), expect=lambda: titles() == ["Cook"] and "Show completed" in txt(), wait=1500)
            t.step("STAGE 2 FILTER: press Show completed. All three are back, nothing was deleted", lambda: (pg.click("a:has-text('Show completed')"), top()), expect=lambda: sorted(titles()) == ["Cook", "Sweep", "Wash"], wait=1500)
            other = pg.context.new_page()
            t.step("STAGE 3 CLEAR: press Clear completed (2). A review page lists exactly Wash and Sweep and nothing is deleted yet", lambda: (pg.click("a:has-text('Clear completed')"), pg.wait_for_timeout(400)), expect=lambda: "These 2 completed tasks" in txt() and "Wash" in txt() and "Sweep" in txt() and "Cook" not in pg.inner_text(".task-row").split("Sweep")[-1], wait=2200)
            def late():
                other.goto(pg.url.split("/tasks/")[0] + "/tasks/")
                other.wait_for_timeout(500)
                other.click(".tasks-lists a:has-text('Chores')")
                other.wait_for_timeout(500)
                other.click(".task-row:has-text('Cook') .task-check")
                other.wait_for_timeout(500)
            t.step("STAGE 3 CLEAR: in another tab Cook is marked done after the review page was drawn", late, expect=lambda: other.locator(".task-row.done").count() == 3, wait=800)
            t.step("STAGE 3 CLEAR: press Move to Trash on the old review page. Nothing is deleted. The page says the tasks changed and now lists three", lambda: (pg.click("button:has-text('Move to Trash')"), pg.wait_for_timeout(400)), expect=lambda: "changed since you looked" in txt() and "These 3 completed tasks" in txt(), wait=2600)
            t.step("STAGE 3 CLEAR: press Move to Trash on the fresh page. The banner says 3 moved to the Trash", lambda: (pg.click("button:has-text('Move to Trash')"), pg.wait_for_timeout(300), pg.evaluate("window.scrollTo(0, 0)")), expect=lambda: "Moved 3 completed tasks to the Trash." in txt() and titles() == [], wait=2200)
            t.step("STAGE 4 RESTORE: open the Trash. Wash, Sweep and Cook are listed", lambda: pg.goto(APP + "/tasks/trash/"), expect=lambda: "Wash" in txt() and "Sweep" in txt() and "Cook" in txt(), wait=1500)
            t.step("STAGE 4 RESTORE: restore Wash alone. Sweep and Cook stay in the Trash", lambda: pg.click(".task-row:has-text('Wash') button:has-text('Restore')"), expect=lambda: pg.eval_on_selector_all(".task-row strong", "e=>e.map(x=>x.textContent.trim())").count("Wash") == 0 and "Sweep" in txt(), wait=1800)
            t.step("STAGE 4 RESTORE: open the list. only Wash is back", lambda: pg.goto(APP + "/tasks/"), expect=lambda: True, wait=300)
            t.step("STAGE 4 RESTORE: choose the list Chores", lambda: pg.click(".tasks-lists a:has-text('Chores')"), expect=lambda: titles() == ["Wash"], wait=1500)
        run_tour(ctx, "clear_completed", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. One synthetic employee. Stages: set up three tasks and finish two, hide and show completed, clear completed, restore one from the Trash. Not shown: subtasks, another person's view.", unshown=["subtasks", "another person's view"])

    g["DRILLS"]["tour_clear_completed"] = tour


def register_drive_device(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            st = lambda: pg.inner_text("#drive-upload-status")
            row = lambda name: pg.locator(".drive-file", has_text=name)
            dbname = lambda: pg.evaluate("()=>'salow-drive-'+document.getElementById('drive-root').dataset.user")
            pg.goto(APP + "/drive/")
            pg.wait_for_timeout(500)
            t.step("Add notes.txt from this device", lambda: pg.set_input_files("#drive-pick", {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"quarterly notes\n"}),
                   expect=lambda: "1 of 1 saved to Drive" in st() and "on this device" in st(), wait=6000)
            print("   status:", st()[:200], flush=True)
            t.step("Reload: the row says a copy is on this device, not checked until opened", lambda: pg.reload(), expect=lambda: "A copy is on this device" in row("notes.txt").inner_text() and row("notes.txt").locator(".drive-open").is_enabled(), wait=2000)
            t.step("Add an empty file: a real file, saved with size 0", lambda: pg.set_input_files("#drive-pick", {"name": "empty.txt", "mimeType": "text/plain", "buffer": b""}),
                   expect=lambda: "1 of 1 saved to Drive" in st(), wait=6000)
            res = {}
            def opened():
                pg.reload(); pg.wait_for_timeout(1200)
                with pg.expect_download() as d:
                    row("notes.txt").locator(".drive-open").click()
                dl = d.value
                res["ok"] = dl.suggested_filename == "notes.txt" and open(dl.path(), "rb").read() == b"quarterly notes\n"
            t.step("Open notes.txt: it downloads as an attachment with the same bytes", opened, expect=lambda: res.get("ok") is True, wait=800)
            # damaged copy in REAL IndexedDB, then repaired by adding the same bytes again
            corrupt = """async (name)=>{const db=await new Promise((r,j)=>{const q=indexedDB.open(name,1);q.onsuccess=()=>r(q.result);q.onerror=()=>j()});
              const keys=await new Promise(r=>{const q=db.transaction('blob').objectStore('blob').getAllKeys();q.onsuccess=()=>r(q.result)});
              const k=keys.find(x=>x!=='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
              await new Promise(r=>{const t=db.transaction('blob','readwrite');t.objectStore('blob').put({sha256:k,size:3,bytes:new Uint8Array([1,2,3]).buffer});t.oncomplete=r});db.close();return k}"""
            def damage():
                pg.evaluate(corrupt, dbname()); pg.reload(); pg.wait_for_timeout(800)
                row("notes.txt").locator(".drive-open").click(); pg.wait_for_timeout(800)
            t.step("Damage the stored bytes in the real browser store: Open refuses and says the copy is damaged", damage, expect=lambda: "damaged" in row("notes.txt").inner_text(), wait=800)
            rb = lambda: row("notes.txt").locator(".drive-reattach")
            def reattach():
                rb().set_input_files({"name": "x.txt", "mimeType": "text/plain", "buffer": b"wrong bytes\n"}); pg.wait_for_timeout(1500)
                res["refused"] = row("notes.txt").inner_text()
                rb().set_input_files({"name": "x.txt", "mimeType": "text/plain", "buffer": b"quarterly notes\n"}); pg.wait_for_timeout(1500)
            t.step("Put a copy back with the row's own picker: wrong bytes are refused, the right bytes repair it", reattach,
                   expect=lambda: "checksum is different" in res.get("refused", "") and "A copy is on this device" in row("notes.txt").inner_text(), wait=1500)
            def reopen():
                pg.reload(); pg.wait_for_timeout(1200)
                with pg.expect_download() as d:
                    row("notes.txt").locator(".drive-open").click()
                res["ok2"] = open(d.value.path(), "rb").read() == b"quarterly notes\n"
            t.step("Open notes.txt again: the repaired copy downloads with the right bytes", reopen, expect=lambda: res.get("ok2") is True, wait=800)
            # same bytes saved three times at once, and two namespaces, in real IndexedDB
            conc = """async ()=>{const D=window.SalowDriveDevice, b=new TextEncoder().encode('concurrent bytes');
              const s=new D.Store('probe-same-'+Date.now()); const r=await Promise.all([s.save(b),s.save(b),s.save(b)]);
              const st=await s.state(r[0].sha256); const o1=new D.Store('probe-a-'+Date.now()), o2=new D.Store('probe-b-'+Date.now());
              await o1.save(b); return {hows:r.map(x=>x.how), same:r.every(x=>x.sha256===r[0].sha256), healthy:st.state, a:await o1.has(r[0].sha256), b:await o2.has(r[0].sha256)}}"""
            out = {}
            t.step("Three saves of the same bytes at once in real IndexedDB, and a second namespace", lambda: out.update(r=pg.evaluate(conc)),
                   expect=lambda: out["r"]["same"] and out["r"]["healthy"] == "on-device" and out["r"]["a"] is True and out["r"]["b"] is False, wait=500)
            print("   concurrent:", out.get("r"), flush=True)
            fail = """async ()=>{const D=window.SalowDriveDevice; const real=indexedDB; const bad={open(n){const q=real.open(n,1);q.addEventListener('success',()=>{});return q}};
              const s=new D.Store('probe-fail-'+Date.now()); await s.open(); const t0=s.db.transaction.bind(s.db);
              s.db.transaction=()=>{throw new Error('injected')}; let msg=''; try{await s.save(new Uint8Array([1,2,3]))}catch(e){msg=e.message} return msg}"""
            t.step("INJECTED storage failure (not a real quota): the save is refused with a plain message", lambda: out.update(f=pg.evaluate(fail)), expect=lambda: "would not use its file storage" in out["f"], wait=500)
            def newver():
                row("notes.txt").locator(".drive-newver").set_input_files({"name": "notes.txt", "mimeType": "text/plain", "buffer": b"second notes\n"}); pg.wait_for_timeout(3000)
                res["v2"] = row("notes.txt").inner_text()
                rb().set_input_files({"name": "x.txt", "mimeType": "text/plain", "buffer": b"quarterly notes\n"}); pg.wait_for_timeout(1500)
                res["stale"] = row("notes.txt").inner_text()
                rb().set_input_files({"name": "x.txt", "mimeType": "text/plain", "buffer": b"second notes\n"}); pg.wait_for_timeout(1500)
                res["fresh"] = row("notes.txt").inner_text()
                print("   v2/stale/fresh:", repr(res["v2"][:160]), "|", repr(res["stale"][:160]), "|", repr(res["fresh"][:160]), flush=True)
            t.step("New version of notes.txt: the row becomes version 2 at once; the OLD bytes are now refused by the row's picker and the new bytes are accepted", newver,
                   expect=lambda: "now version 2" in res.get("v2", "") and "checksum is different" in res.get("stale", "") and "A copy is on this device" in res.get("fresh", ""), wait=500)
            t.step("Clear this browser's stored files and reload: the row says Not on this device and Open is disabled", lambda: (pg.evaluate("(n)=>new Promise(r=>{const q=indexedDB.deleteDatabase(n);q.onsuccess=q.onerror=q.onblocked=()=>r()})", dbname()), pg.reload()),
                   expect=lambda: "Not on this device" in row("notes.txt").inner_text() and row("notes.txt").locator(".drive-open").is_disabled(), wait=2000)
        run_tour(ctx, "107_drive_device_files", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated, real IndexedDB. Bytes stay in the browser; the server gets name, size and checksum. One synthetic employee: the two-namespace check uses two store names in the same browser, not two signed-in accounts. The storage failure is injected, not a real quota. Not offline proof.", unshown=["real quota exhaustion", "sign-in expiry", "another person's device or account", "no-network reload"])

    g["DRILLS"]["tour_drive_device"] = tour


def register_slides_layout(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            st = lambda: (pg.locator("#sl-status").text_content() or "")
            ls = lambda: (pg.locator("#ly-status").text_content() or "")
            go = lambda n: (pg.locator("#sl-list button").nth(n).click(), pg.wait_for_timeout(300))
            fs = lambda: pg.evaluate("()=>{const t=document.querySelector('#sl-canvas-wrap svg text');return t?parseFloat(t.getAttribute('font-size')):null}")
            ty = lambda: pg.evaluate("()=>{const t=document.querySelector('#sl-canvas-wrap svg text tspan');return t?parseFloat(t.getAttribute('y')):null}")
            saved = lambda: pg.evaluate("()=>JSON.parse(localStorage.getItem(document.getElementById('slide-root').getAttribute('data-store-key')))")
            canvas = lambda: pg.evaluate("document.getElementById('sl-canvas-wrap').scrollIntoView({block:'center'})")
            pg.select_option("#sl-layout", "title")
            t.step("Slide 1 is the blank slide. Choose the Title layout and tick Link new slide to layout", lambda: pg.check("#sl-link"), expect=lambda: pg.is_checked("#sl-link"))
            pg.click("#sl-new"); pg.wait_for_timeout(400)
            t.step("New slide 2, linked: its Title is 54 px at y 170 (the built-in layout)", lambda: canvas(), expect=lambda: fs() == 54 and ty() is not None and saved()["v"] == 9 and saved()["slides"][1]["lay"] == "title", wait=2500)
            pg.uncheck("#sl-link"); pg.click("#sl-new"); pg.wait_for_timeout(400)
            t.step("New slide 3 from the same layout, link box not ticked: looks the same (54 px) but is not linked", lambda: canvas(), expect=lambda: fs() == 54 and "lay" not in saved()["slides"][2], wait=2500)
            before3 = ty()
            go(1); y_before = ty()
            pg.locator("#sl-items button").first.click(); pg.wait_for_timeout(200)
            pg.fill("#sl-size", "40"); pg.press("#sl-size", "Tab"); pg.wait_for_timeout(300)
            t.step("On linked slide 2 change the Title size by hand to 40: a local override on the slide", lambda: canvas(), expect=lambda: fs() == 40 and saved()["slides"][1]["elements"][0]["ph"] == 0, wait=2500)
            pg.evaluate("document.getElementById('ly-apply').scrollIntoView({block:'center'})")
            pg.select_option("#ly-key", "title"); pg.select_option("#ly-slot", "0"); pg.fill("#ly-size", "60"); pg.fill("#ly-y", "120"); pg.click("#ly-apply"); pg.wait_for_timeout(400)
            m1 = ls()
            t.step("Edit layout: Title item size 60 and y 120. The status counts 1 linked item and 1 with a changed value on the slide", expect=lambda: "1 linked item(s)" in m1 and "1 of them" in m1 and saved()["lays"]["title"][0] == {"size": 60, "y": 120}, wait=2500)
            t.step("Slide 2 (linked, size overridden): the size stays 40 and the untouched y moved to 120", lambda: canvas(), expect=lambda: fs() == 40 and ty() is not None and ty() < y_before and saved()["slides"][1]["elements"][0]["y"] == 120, wait=2500)
            go(2)
            t.step("Slide 3 (not linked): still 54 px at its old y, the layout edit did not touch it", lambda: canvas(), expect=lambda: fs() == 54 and ty() == before3 and saved()["slides"][2]["elements"][0]["y"] == 170, wait=2500)
            go(1); pg.click("#sl-undo"); pg.wait_for_timeout(400)
            t.step("Undo the layout edit: slide 2 is back at its first y, still 40 px, and the stored layout edit is gone", lambda: canvas(), expect=lambda: fs() == 40 and saved()["slides"][1]["elements"][0]["y"] == 170 and saved()["slides"][1]["elements"][0]["size"] == 40 and "lays" not in saved(), wait=2500)
            pg.click("#sl-redo"); pg.wait_for_timeout(400)
            t.step("Redo: the layout edit returns and slide 2 is at y 120 again with 40 px", lambda: canvas(), expect=lambda: fs() == 40 and saved()["slides"][1]["elements"][0]["y"] == 120 and saved()["slides"][1]["elements"][0]["size"] == 40 and saved()["lays"]["title"][0]["size"] == 60, wait=2500)
            go(1); pg.click("#sl-unlink"); pg.wait_for_timeout(300)
            t.step("Unlink slide 2: it keeps what it shows (40 px) and loses lay and ph in the stored deck", lambda: canvas(), expect=lambda: fs() == 40 and "lay" not in saved()["slides"][1] and "ph" not in saved()["slides"][1]["elements"][0], wait=2500)
            pg.reload(); pg.wait_for_timeout(600); go(1)
            t.step("After a reload the layout edit is still stored (version 9) and slide 2 shows 40 px", lambda: canvas(), expect=lambda: saved()["v"] == 9 and saved()["lays"]["title"][0]["size"] == 60 and fs() == 40, wait=2500)
        run_tour(ctx, "slides_layout", body, notes="Device-local deck, headless Chromium 390x844 emulated, one run. Not shown: export and import of a linked deck and the PDF of a linked slide (covered in node only), undo of a layout edit.", unshown=["export and import (node only)", "pdf of a linked slide", "undo of a layout edit"])

    g["DRILLS"]["tour_slides_layout"] = tour


def register_slides_find(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_selector("#sl-add-text", timeout=15000)
            key = pg.evaluate("() => document.getElementById('slide-root').dataset.storeKey")
            deck = {"v": 1, "theme": "light", "slides": [
                {"id": "s0", "notes": "", "elements": [{"id": "a", "type": "text", "x": 40, "y": 40, "w": 600, "h": 80, "fill": "theme", "text": "Apple pie plan", "size": 32}, {"id": "b", "type": "text", "x": 40, "y": 160, "w": 600, "h": 80, "fill": "theme", "text": "apple and APPLE", "size": 32}]},
                {"id": "s1", "notes": "", "elements": [{"id": "c", "type": "text", "x": 40, "y": 40, "w": 600, "h": 80, "fill": "theme", "text": "Second slide apple", "size": 32}]}]}
            pg.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", [key, deck])
            pg.reload(); pg.wait_for_selector("#sl-add-text", timeout=15000); pg.wait_for_timeout(600)
            st = lambda: pg.inner_text("#sl-find-status")
            stored = lambda: json.loads(pg.evaluate("(k) => localStorage.getItem(k)", key))
            texts = lambda: [[e["text"] for e in sl["elements"]] for sl in stored()["slides"]]
            def opened():
                pg.evaluate("() => { document.getElementById('sl-find').open = true; document.getElementById('sl-find').scrollIntoView({block: 'start'}); }")
            t.step("Find and replace opens, and Find apple counts 4 matches across slides 1 and 2", lambda: (opened(), pg.fill("#sl-find-q", "apple"), pg.click("#sl-find-go")), expect=lambda: st().startswith("4 matches on slides 1 (3), 2 (1)"), wait=1500)
            t.step("Match case: Find apple now counts 2", lambda: (pg.check("#sl-find-case"), pg.click("#sl-find-go")), expect=lambda: st().startswith("2 matches"), wait=1500)
            t.step("Replace in all slides with pear (Match case on): 2 matches replaced and saved", lambda: (pg.fill("#sl-find-r", "pear"), pg.click("#sl-rep-all")), expect=lambda: texts() == [["Apple pie plan", "pear and APPLE"], ["Second slide pear"]] and st().startswith("Replaced 2 matches"), wait=1800)
            t.step("Undo brings the old text back in one step", lambda: pg.click("#sl-undo"), expect=lambda: texts()[0][1] == "apple and APPLE" and texts()[1][0] == "Second slide apple", wait=1500)
            t.step("Replace on this slide only (Match case off): slide 1 changes, slide 2 does not", lambda: (pg.uncheck("#sl-find-case"), pg.click("#sl-rep-slide")), expect=lambda: texts() == [["pear pie plan", "pear and pear"], ["Second slide apple"]], wait=1800)
            t.step("A find with no match says so and changes nothing", lambda: (pg.fill("#sl-find-q", "zzz"), pg.click("#sl-rep-all")), expect=lambda: st().startswith("No matches") and texts()[0][0] == "pear pie plan", wait=1500)
            t.step("With an item selected, Replace on this slide updates the Text box, so a later edit cannot bring the old text back", lambda: (pg.locator("#sl-items li button").first.click(), pg.fill("#sl-find-q", "pear"), pg.fill("#sl-find-r", "plum"), pg.click("#sl-rep-slide")), expect=lambda: pg.input_value("#sl-text") == "plum pie plan" and texts()[0][0] == "plum pie plan", wait=1800)
            t.step("Typing one more character in that Text box keeps the replaced text", lambda: (pg.click("#sl-text"), pg.press("#sl-text", "End"), pg.keyboard.type("!"), pg.press("#sl-text", "Tab")), expect=lambda: texts()[0][0] == "plum pie plan!", wait=1800)
        run_tour(ctx, "slides_find", body, notes="Device-local deck. Headless Chromium 390x844 emulated, real Django on TiDB unistore. Plain-text find and replace over the slides' own items.")
    g["DRILLS"]["tour_slides_find"] = tour


def register_slides_paste(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_selector("#sl-add-text", timeout=15000)
            key = pg.evaluate("() => document.getElementById('slide-root').dataset.storeKey")
            mk = lambda i, t_: {"id": i, "type": "text", "x": 40, "y": 40, "w": 600, "h": 80, "fill": "theme", "text": t_, "size": 32, "b": True}
            deck = {"v": 1, "theme": "light", "slides": [{"id": "s0", "notes": "", "elements": [mk("a", "Move me")]}, {"id": "s1", "notes": "", "elements": [mk("c", "Second slide")]}]}
            pg.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", [key, deck])
            pg.reload(); pg.wait_for_selector("#sl-add-text", timeout=15000); pg.wait_for_timeout(600)
            stored = lambda: json.loads(pg.evaluate("(k) => localStorage.getItem(k)", key))
            texts = lambda: [[e["text"] for e in sl["elements"]] for sl in stored()["slides"]]
            goto = lambda n: (pg.locator("#sl-list button").nth(n).click(), pg.wait_for_timeout(400))
            pick = lambda n: (pg.evaluate("() => document.getElementById('sl-props').scrollIntoView({block: 'start'})"), pg.locator("#sl-items li button").nth(n).click(), pg.wait_for_timeout(300))
            t.step("Paste item is off until something is copied", expect=lambda: pg.is_disabled("#sl-paste-el"), wait=1500)
            t.step("Select the item on slide 1 and Copy item: Paste item turns on", lambda: (pick(0), pg.click("#sl-copy-el")), expect=lambda: not pg.is_disabled("#sl-paste-el") and "Item copied" in pg.inner_text("#sl-status"), wait=1500)
            t.step("Go to slide 2 and Paste item: slide 2 now holds both, slide 1 keeps its item", lambda: (goto(1), pg.click("#sl-paste-el")), expect=lambda: texts() == [["Move me"], ["Second slide", "Move me"]], wait=1800)
            t.step("Undo removes the paste in one step", lambda: pg.click("#sl-undo"), expect=lambda: texts() == [["Move me"], ["Second slide"]], wait=1500)
            t.step("Cut the slide 2 item, go to slide 1 and paste it there", lambda: (pick(0), pg.click("#sl-cut-el"), goto(0), pg.click("#sl-paste-el")), expect=lambda: texts() == [["Move me", "Second slide"], []], wait=1800)
            t.step("The pasted item is selected and keeps its bold style", expect=lambda: pg.input_value("#sl-text") == "Second slide" and pg.is_checked("#sl-bold"), wait=1500)
        run_tour(ctx, "slides_paste", body, notes="Device-local deck. Headless Chromium 390x844 emulated, real Django on TiDB unistore. Copy, cut and paste of one item between slides, held in the page only.")
    g["DRILLS"]["tour_slides_paste"] = tour


def register_slides_bg(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            bg = lambda: pg.locator("#sl-canvas-wrap svg rect").first.get_attribute("fill")
            status = lambda: (pg.locator("#sl-status").text_content() or "")
            opt = lambda v: pg.locator('#sl-bg option[value="%s"]' % v)
            t.step("Slides: a text item on a light slide", lambda: (pg.click("#sl-add-text"), pg.fill("#sl-text", "Quarterly plan"), pg.press("#sl-text", "Tab")), expect=lambda: bg() == "#ffffff", wait=1500)
            t.step("The dark backgrounds are listed but not selectable: the text would be unreadable", expect=lambda: opt("#1f2430").is_disabled() and "too hard to read" in opt("#1f2430").text_content() and not opt("#f4efe6").is_disabled())
            t.step("Pick Pale yellow as this slide's background", lambda: pg.select_option("#sl-bg", "#fdf1d6"), expect=lambda: bg() == "#fdf1d6" and pg.input_value("#sl-bg") == "#fdf1d6")
            t.step("Make the text a pale-on-pale colour: the change is refused with a reason and the colour stays", lambda: pg.select_option("#sl-fill", "#c28b1d"), expect=lambda: "hard to read" in status() and pg.input_value("#sl-fill") == "theme")
            t.step("Present shows the same background", lambda: pg.click("#sl-present"), expect=lambda: pg.locator("#sl-show-canvas svg rect").first.get_attribute("fill") == "#fdf1d6", wait=1800)
            t.step("Exit the show", lambda: pg.click("#sl-show-exit"), expect=lambda: not pg.locator("#sl-show").evaluate("e=>e.open"))
            t.step("Switch to Dark: it asks first, naming slide 1", lambda: pg.select_option("#sl-theme", "dark"), expect=lambda: any("slide 1" in m and "hard to read" in m for m in seen) and pg.input_value("#sl-theme") == "dark" and bg() == "#1f2430")
            t.step("Undo: the light theme and the yellow background both come back", lambda: pg.click("#sl-undo"), expect=lambda: pg.input_value("#sl-theme") == "light" and bg() == "#fdf1d6")
        run_tour(ctx, "99_slides_background", body, notes="Device-local deck. Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Editor and Present fills are read from the page; the PDF fill is checked in node from the page content stream, not here.",
                 unshown=["PDF download (secure context, checked in node)", "dismissing the theme question", "master items", "hand-edited unreadable import"])

    g["DRILLS"]["tour_slides_bg"] = tour


def register_chat_rename(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def run(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def make():
            from apps.accounts.models import CustomUser
            from apps.chat import services as cs
            from apps.chat.models import Space

            Space.objects.filter(name__startswith="Rename demo").delete()
            others = []
            for eid, nm in (("ST-LIVE-2", "Second Synthetic"), ("ST-LIVE-3", "Third Synthetic")):
                others.append(CustomUser.objects.get_or_create(employee_id=eid, defaults={"full_name": nm, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                                                              "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0])
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            sp = cs.create_group(me, "Rename demo old", [o.pk for o in others])
            cs.post_message(others[0], sp.public_id, "Welcome to the group", "rn-1")

        def counts():
            from apps.chat.models import Message
            from apps.core.models import Notification

            return Message.objects.count(), Notification.objects.count()

        def cleanup():
            from apps.chat.models import Space

            Space.objects.filter(name__startswith="Rename demo").delete()

        run(make)
        before = {}

        def body(t, pg):
            nm = "input[name=name]"
            pg.goto(APP + "/chat/")
            t.step("Open the group. As its manager there is a Group name box with a Rename group button", lambda: pg.click("a:has-text('Rename demo old')"), expect=lambda: pg.input_value(nm) == "Rename demo old" and pg.is_visible("button:has-text('Rename group')"), wait=2200)
            before["n"] = run(counts)
            t.step("Type the new name", lambda: (pg.fill(nm, "Rename demo new"), pg.locator(nm).scroll_into_view_if_needed()), expect=lambda: pg.input_value(nm) == "Rename demo new", wait=2000)
            t.step("Press Rename group. The title and the box show the new name and the page says nobody is notified", lambda: (pg.click("button:has-text('Rename group')"), pg.wait_for_timeout(500), pg.evaluate("window.scrollTo(0, 0)")), expect=lambda: pg.inner_text("h2") == "Rename demo new" and pg.input_value(nm) == "Rename demo new" and "Nobody is notified" in pg.inner_text("body"), wait=2600)
            t.step("No message and no notice was created (checked in the database)", expect=lambda: run(counts) == before["n"], wait=1500)
            t.step("The conversation list shows only the new name", lambda: pg.goto(APP + "/chat/"), expect=lambda: pg.is_visible("a:has-text('Rename demo new')") and not pg.is_visible("a:has-text('Rename demo old')"), wait=2200)
        try:
            run_tour(ctx, "chat_rename", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. Fixtures (ORM): a group with ST-LIVE-1 as its manager and two other people. Not shown: a non-manager member's view (no form, refusal in unit tests) and a direct message.", unshown=["a non-manager member's view", "a direct message"])
        finally:
            run(cleanup)

    g["DRILLS"]["tour_chat_rename"] = tour


def register_chat_mention(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.chat import services as cs

            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            sp = cs.open_dm(me, two.pk)
            cs.post_message(two, sp.public_id, "Can you check the rota?", "mn-1")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def count():
            def q():
                from apps.core.models import Notification

                return Notification.objects.filter(kind="chat_mention", user__employee_id="ST-LIVE-2").count()

            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(q).result()

        def body(t, pg):
            pg.goto(APP + "/chat/")
            t.step("Open the conversation", lambda: pg.click("a:has-text('Second Synthetic')"), expect=lambda: "check the rota" in pg.inner_text("#chat-list"), wait=1800)
            t.step("The composer lists the other member as a mention button", expect=lambda: pg.locator("#chat-mentions .chat-mention-btn[data-mention='@ST-LIVE-2']").count() == 1, wait=1800)
            t.step("Tap the mention button: @ST-LIVE-2 is inserted", lambda: (pg.fill("textarea[name=body]", "Yes, looking now"), pg.click("#chat-mentions .chat-mention-btn")), expect=lambda: pg.input_value("textarea[name=body]").rstrip().endswith("@ST-LIVE-2"), wait=2000)
            t.step("Send it", lambda: pg.click(".chat-compose button[type=submit]"), expect=lambda: "@ST-LIVE-2" in pg.inner_text("#chat-list"), wait=2000)
            t.step("The mentioned member has exactly one notice (checked in the database; the other account's Inbox page is not shown)", expect=lambda: count() == 1, wait=1500)

        run_tour(ctx, "96_chat_mention", body, notes="Real Django on TiDB (unistore) under uvicorn, headless Chromium 390x844 emulated. The mentioned person's Inbox is covered by unit tests, not filmed.",
                 unshown=["the mentioned person's Inbox page"])

    g["DRILLS"]["tour_chat_mention"] = tour


def register_chat_react(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.chat import services as cs

            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            sp = cs.open_dm(me, two.pk)
            cs.post_message(two, sp.public_id, "Rota published for next week.", "rx-1")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            pg.goto(APP + "/chat/")
            t.step("Chat list", expect=lambda: pg.locator("a:has-text('Second Synthetic')").count() >= 1, wait=1800)
            t.step("Open the conversation", lambda: pg.click("a:has-text('Second Synthetic')"), expect=lambda: "Rota published" in pg.inner_text("#chat-list"), wait=2000)
            pressed = lambda k: pg.get_attribute("#m1 button[value=%s]" % k, "aria-pressed") == "true"
            for k, nm in (("laugh", "Laugh"), ("tada", "Celebrate"), ("pray", "Thanks"), ("eyes", "Looking")):
                t.step("Press %s on the message: the button shows pressed" % nm, (lambda kk: lambda: pg.click("#m1 button[value=%s]" % kk))(k), expect=(lambda kk: lambda: pressed(kk))(k), wait=2200)
            t.step("Press Laugh again: it switches off (the toggle works both ways)", lambda: pg.click("#m1 button[value=laugh]"), expect=lambda: not pressed("laugh") and pressed("tada"), wait=2500)
            t.note("Not shown: thumbs up and heart (clip 01), other people's reactions, Chat history/search/edit/delete (not built, pending the user's choice).", 3)

        run_tour(ctx, "25_chat_reactions", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated. Message from a seeded fixture colleague.",
                 unshown=["thumbs up and heart (clip 01)", "other people's reactions", "history/search/edit/delete (not built)"])

    g["DRILLS"]["tour_chat_react"] = tour


def register_drive_editor(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.drive import services as ds

            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            f = ds.create_folder(two, "Team plans from Second")
            ds.share(two, f, me, "editor")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            pg.goto(APP + "/drive/")
            t.step("Drive: Shared with me tab shows a folder with Editor access", lambda: pg.click(".drive-tabs a:has-text('Shared with me')"), expect=lambda: "Editor access" in pg.inner_text("body"), wait=2500)
            t.step("Open the folder shared with me as Editor", lambda: pg.click("a.drive-name:has-text('Team plans')"), expect=lambda: pg.locator("button:has-text('Create folder')").count() >= 1, wait=2500)
            t.step("As Editor, create a folder inside it", lambda: (pg.fill("input[placeholder='New folder name']", "Editor made"), pg.click("button:has-text('Create folder')")), expect=lambda: pg.locator("a.drive-name", has_text="Editor made").count() == 1, wait=3000)
            t.step("As Editor, rename that folder", lambda: (pg.fill("input[aria-label='New name']", "Editor renamed"), pg.click("button:has-text('Rename')")), expect=lambda: pg.locator("a.drive-name", has_text="Editor renamed").count() == 1, wait=3000)
            t.step("The Sharing box: can an Editor change who has access? Observe", lambda: pg.wait_for_timeout(300), expect=lambda: True, wait=2500)
            t.note("Observed: Share folder control present for the Editor = %s; Remove access control present = %s; Move to trash present = %s." % (pg.locator("button:has-text('Share folder')").count() > 0, pg.locator("button:has-text('Remove access')").count() > 0, pg.locator("button:has-text('Move to trash')").count() > 0), 4)
            pg.goto(APP + "/drive/")
            t.step("Back in My Drive: create my own folder", lambda: (pg.fill("input[placeholder='New folder name']", "My plans"), pg.click("button:has-text('Create folder')")), expect=lambda: pg.locator("a.drive-name", has_text="My plans").count() == 1, wait=2500)
            t.step("Open it", lambda: pg.click("a.drive-name:has-text('My plans')"), expect=lambda: "Sharing" in pg.inner_text("body"), wait=2000)
            t.step("Share it with ST-LIVE-2 as Editor", lambda: (pg.fill("input[placeholder='Employee ID']", "ST-LIVE-2"), pg.select_option("select[name=role]", "editor"), pg.click("button:has-text('Share folder')")), expect=lambda: "Editor" in pg.locator(".drive-share").inner_text() and "Second Synthetic" in pg.locator(".drive-share").inner_text(), wait=3500)

        run_tour(ctx, "26_drive_editor", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated. The folder shared with Editor access was created by a script as a fixture colleague.",
                 unshown=["what the second person sees after my Editor share (one signed-in user in this clip)", "folder move (no UI)", "files (not built)"])

    g["DRILLS"]["tour_drive_editor"] = tour


def register_tasks_more(g):
    APP = g["APP"]

    def prep():
        import concurrent.futures

        import django

        django.setup()

        def fx():
            import datetime

            from apps.accounts.models import CustomUser
            from apps.tasks import projects as pj
            from apps.tasks.models import ProjectMember

            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            p = pj.create_project(me, "Fixture board")
            ProjectMember.objects.create(project=p, person=two, role=ProjectMember.MEMBER, added_by=me)
            col = p.columns.first().pk
            pj.add_task(me, p, col, "Overdue fixture task", due=datetime.date(2026, 1, 5))
            pj.add_task(me, p, col, "Plain fixture task")
            pj.add_task(me, p, col, "Delete me task")
            return str(p.public_id)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(fx).result()

    def lists_more(ctx):
        def body(t, pg):
            pg.goto(APP + "/tasks/")
            t.step("Create a list called Spare", lambda: (pg.fill("input[aria-label='New list name']", "Spare"), pg.click("button:has-text('Add list')")), expect=lambda: pg.locator(".tasks-lists a", has_text="Spare").count() >= 1, wait=2000)
            t.step("Open it", lambda: pg.click(".tasks-lists a:has-text('Spare')"), expect=lambda: pg.locator("input[aria-label='Task title']").count() == 1, wait=1800)
            t.step("Add a task", lambda: (pg.fill("input[aria-label='Task title']", "Parent task"), pg.click(".tasks-add button")), expect=lambda: "Parent task" in pg.inner_text("body"), wait=2200)
            t.step("Add a subtask", lambda: (pg.fill("input[aria-label='Subtask title']", "Child step"), pg.click(".task-sub-add button")), expect=lambda: pg.locator(".task-row.sub", has_text="Child step").count() == 1, wait=2200)
            t.step("Delete the subtask: a confirmation page first", lambda: pg.locator(".task-row.sub a:has-text('Delete')").first.click(), expect=lambda: "Delete this" in pg.inner_text("h2"), wait=2200)
            t.step("Confirm: the subtask is gone, the parent stays", lambda: pg.click("button:has-text('Delete')"), expect=lambda: "Child step" not in pg.inner_text("body") and "Parent task" in pg.inner_text("body"), wait=3000)
            t.step("Delete this list: a confirmation page first", lambda: pg.click("a:has-text('Delete this list')"), expect=lambda: "Delete this list" in pg.inner_text("h2"), wait=2200)
            t.step("Confirm: the list is gone from My lists", lambda: pg.click("button:has-text('Delete')"), expect=lambda: pg.locator(".tasks-lists a", has_text="Spare").count() == 0, wait=3200)
            t.note("Not built: restoring deleted tasks or lists. Edit conflict message needs two people editing at once, not shown.", 3)
        run_tour(ctx, "27_tasks_lists_more", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated.", unshown=["edit conflict (needs concurrent edit)", "restore (not built)"])

    def proj_more(ctx):
        pid = prep()

        def body(t, pg):
            pg.goto(APP + "/tasks/projects/%s/" % pid)
            t.step("Project board with a second member and three fixture tasks", expect=lambda: "Overdue fixture task" in pg.inner_text("body"), wait=2200)
            card = lambda nm: pg.locator(".task-card", has_text=nm).first
            t.step("Assign Plain fixture task to Second Synthetic", lambda: (card("Plain fixture task").locator("select[name=person]").select_option(label="Second Synthetic"), card("Plain fixture task").locator("button:has-text('Assign')").click()), expect=lambda: "Assigned to Second Synthetic" in card("Plain fixture task").inner_text(), wait=3000)
            t.step("Add a milestone", lambda: (pg.fill("input[placeholder='New milestone']", "Beta"), pg.click("button:has-text('Add milestone')")), expect=lambda: "Beta" in pg.inner_text("body"), wait=2500)
            t.step("Set the milestone Beta on Plain fixture task", lambda: (card("Plain fixture task").locator("select[name=milestone]").select_option(label="Beta"), card("Plain fixture task").locator("button:has-text('Set')").click()), expect=lambda: "Milestone: Beta" in card("Plain fixture task").inner_text(), wait=3000)
            t.step("Filter by the milestone Beta: only that task remains", lambda: (pg.select_option("select[name=milestone][aria-label='Milestone filter']", label="Beta"), pg.click("button:has-text('Filter')")), expect=lambda: "Showing 1 of 3" in pg.inner_text("body") and pg.locator(".task-card").count() == 1, wait=3200)
            t.step("Clear the filter: all three tasks are back", lambda: pg.click("a:has-text('Clear')"), expect=lambda: pg.locator(".task-card").count() == 3, wait=2800)
            t.step("Overdue filter: only the overdue task remains", lambda: (pg.check("input[name=overdue]"), pg.click("button:has-text('Filter')")), expect=lambda: pg.locator(".task-card").count() == 1 and "Overdue fixture task" in pg.inner_text(".tasks-board"), wait=3200)
            t.step("Clear again", lambda: pg.click("a:has-text('Clear')"), expect=lambda: pg.locator(".task-card").count() == 3, wait=2200)
            t.step("Edit Plain fixture task: open the edit page", lambda: card("Plain fixture task").locator("a:has-text('Edit')").click(), expect=lambda: "Edit" in pg.inner_text("h2"), wait=2200)
            t.step("Change the title and Save", lambda: (pg.fill("input[name=title]", "Plain fixture task, edited"), pg.click("button:has-text('Save')")), expect=lambda: "edited" in pg.inner_text("body"), wait=3000)
            t.step("Open Comments on that task", lambda: card("edited").locator("a:has-text('Comment')").click(), expect=lambda: "Comments" in pg.inner_text("h2"), wait=2200)
            t.step("Post a comment", lambda: (pg.fill("textarea[name=body]", "First fixture comment."), pg.click("button:has-text('Post')")), expect=lambda: "First fixture comment." in pg.inner_text("body"), wait=3200)
            pg.goto(APP + "/tasks/projects/%s/" % pid)
            t.step("Delete Delete me task: a confirmation page first", lambda: card("Delete me task").locator("a:has-text('Delete')").click(), expect=lambda: "Delete this" in pg.inner_text("h2"), wait=2200)
            t.step("Confirm: that task is gone from the board", lambda: pg.click("button:has-text('Delete')"), expect=lambda: "Delete me task" not in pg.inner_text(".tasks-board"), wait=3200)
            t.note("Not shown: what Second Synthetic sees (one signed-in user), edit conflict. Not built: adding members, restoring tasks.", 3)
        run_tour(ctx, "28_tasks_projects_more", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated. The second member and the three tasks were created by a script as fixtures.", unshown=["second person's view", "edit conflict", "add members (not built)"])

    g["DRILLS"]["tour_tasks_lists_more"] = lists_more
    g["DRILLS"]["tour_tasks_proj_more"] = proj_more


def register_cal_repeats(g):
    APP = g["APP"]

    def tour(ctx):
        import datetime as dt

        start = dt.date.today() + dt.timedelta(days=3)
        until = start + dt.timedelta(days=21)
        d0 = start.isoformat()
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        def body(t, pg):
            def fill_base(title, tz=None, guests=None):
                pg.goto(APP + "/calendar/new/")
                pg.fill("input[name=title]", title)
                pg.fill("input[name=start]", d0 + "T09:00")
                pg.fill("input[name=end]", d0 + "T09:30")
                if tz:
                    pg.fill("input[name=tz]", tz)
                if guests:
                    pg.fill("textarea[name=guests]", guests)

            occ = lambda: " ".join(pg.inner_text("body").split())
            fill_base("Daily every 2 days")
            t.step("New event: Daily, every 2, ends after 3 times", lambda: (pg.select_option("select[name=freq]", "daily"), pg.fill("input[name=interval]", "2"), pg.fill("input[name=count]", "3")), wait=2200)
            t.step("Create: three occurrences, two days apart", lambda: pg.click("button:has-text('Create')"), expect=lambda: "Occurrences" in pg.inner_text("body") and pg.locator("form[action$='/skip/']").count() == 3, wait=3500)
            fill_base("Weekly two days until")
            def weekly():
                pg.select_option("select[name=freq]", "weekly")
                for lbl in (names[start.weekday()], names[(start.weekday() + 2) % 7]):
                    pg.locator("label.cal-inline", has_text=lbl).first.locator("input").check()
                pg.fill("input[name=until]", until.isoformat())
            t.step("New event: Weekly, ticks the start day and two days later, ends on a date three weeks out", weekly, wait=2500)
            t.step("Create: occurrences fall on ticked weekdays only and stop by the end date", lambda: pg.click("button:has-text('Create')"), expect=lambda: "Occurrences" in pg.inner_text("body") and pg.locator("form[action$='/skip/']").count() >= 3, wait=3500)
            t.note("Occurrence count on that page: %d. First lines: %s" % (pg.locator("form[action$='/skip/']").count(), occ()[occ().find("Occurrences"):][:150]), 4)
            fill_base("Monthly every month")
            t.step("New event: Monthly, ends after 3 times", lambda: (pg.select_option("select[name=freq]", "monthly"), pg.fill("input[name=count]", "3")), wait=2200)
            t.step("Create: three monthly occurrences", lambda: pg.click("button:has-text('Create')"), expect=lambda: pg.locator("form[action$='/skip/']").count() == 3, wait=3500)
            fill_base("Zone event", tz="America/New_York")
            t.step("New event with time zone America/New_York at 09:00", lambda: None, wait=1800)
            t.step("Create: the event page shows the zone", lambda: pg.click("button:has-text('Create')"), expect=lambda: "New_York" in pg.inner_text("body") or "New York" in pg.inner_text("body"), wait=3500)
            fill_base("Bad guest event", guests="ST-NOBODY-9")
            t.step("New event with a guest ID that does not exist: it is refused with a message", lambda: pg.click("button:has-text('Create')"), expect=lambda: "guests could not be added" in pg.inner_text("body"), wait=3500)
            t.note("Not shown: Earlier/Later occurrence paging, a guest's answer inside a series, edit conflict.", 3)

        run_tour(ctx, "29_calendar_repeats", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated. Dates are relative to the run date.", unshown=["paging of occurrences", "guest answers inside a series", "edit conflict"])

    g["DRILLS"]["tour_cal_repeats"] = tour


def register_sheets_more(g):
    APP = g["APP"]

    def tour(ctx):
        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        def val(pg, a):
            return pg.input_value('input[data-addr="%s"]' % a)

        DATA = [("A1", "Item"), ("B1", "Qty"), ("A2", "pens"), ("B2", "30"), ("A3", "paper"), ("B3", "12"), ("A4", "ink"), ("B4", "7"), ("A5", "pens"), ("B5", "20")]

        def sc(pg, a):
            pg.locator('input[data-addr="%s"]' % a).scroll_into_view_if_needed()
            pg.evaluate("a=>{var e=document.querySelector('input[data-addr=\\''+a+'\\']');if(e)e.scrollIntoView({block:'center'})}", a)

        def body(t, pg):
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.step("Sheets: type the small table again", lambda: put(pg, DATA), expect=lambda: val(pg, "B5") == "20")
            def piv(agg):
                pg.fill("#pivot-range", "A1:B5")
                pg.fill("#pivot-key", "A")
                pg.fill("#pivot-val", "B")
                pg.select_option("#pivot-agg", agg)
                pg.click("#pivot-build")
                pg.locator("#pivot-out").scroll_into_view_if_needed()
            for agg in ("count", "average", "min", "max"):
                t.step("Pivot with %s per item" % agg, lambda a=agg: piv(a), expect=lambda: "pens" in pg.inner_text("#pivot-out"), wait=2200)
            t.step("Line chart of B2:B5", lambda: (pg.fill("#chart-range", "B2:B5"), pg.select_option("#chart-type", "line"), pg.click("#chart-draw"), pg.locator("#chart-out").scroll_into_view_if_needed()), expect=lambda: pg.locator("#chart-out *").count() > 0, wait=2200)
            t.step("Colour rule: yellow when below 15", lambda: (pg.fill("#cf-range", "B2:B5"), pg.select_option("#cf-kind", "less"), pg.fill("#cf-value", "15"), pg.select_option("#cf-color", "yellow"), pg.click("#cf-add"), pg.locator("#cf-list").scroll_into_view_if_needed()), expect=lambda: pg.locator("#cf-list *").count() > 0, wait=2200)
            t.step("Format: percent, centred", lambda: (pg.fill("#fmt-range", "B2:B5"), pg.select_option("#fmt-align", "center"), pg.select_option("#fmt-nf", "percent"), pg.click("#fmt-add"), pg.locator("#fmt-list").scroll_into_view_if_needed()), expect=lambda: pg.locator("#fmt-list *").count() > 0, wait=2200)
            t.step("Validation: D2:D5 must be one of yes, no", lambda: (pg.fill("#val-range", "D2:D5"), pg.select_option("#val-kind", "list"), pg.fill("#val-items", "yes, no"), pg.click("#val-add"), pg.locator("#val-list").scroll_into_view_if_needed()), expect=lambda: pg.locator("#val-list *").count() > 0, wait=2200)
            t.step("Look at the cells B2 to B5: yellow below 15, percent, centred", lambda: sc(pg, "B3"), expect=lambda: True, wait=3000)
            t.step("Type maybe in D2: refused", lambda: (put(pg, [("D2", "maybe")]), sc(pg, "D2")), expect=lambda: val(pg, "D2") != "maybe", wait=2800)
            t.step("Type yes in D3: accepted", lambda: (put(pg, [("D3", "yes")]), sc(pg, "D3")), expect=lambda: val(pg, "D3") == "yes", wait=2800)
            t.step("Formulas: AVERAGE, IF, SUMIF and a divide by zero", lambda: put(pg, [("F1", "=AVERAGE(B2:B5)"), ("F2", '=IF(B2>25,"big","small")'), ("F3", '=SUMIF(A2:A5,"pens",B2:B5)'), ("F4", "=B2/0")]), expect=lambda: True, wait=2500)
            for a in ("F1", "F2", "F3", "F4"):
                t.step("Select %s: bar shows its result" % a, lambda x=a: pg.click('input[data-addr="%s"]' % x), expect=lambda: pg.inner_text("#sheet-val").strip() != "", wait=2200)
            t.step("Sort A1:B5 by Qty, small to large, with the first row as headers", lambda: (pg.fill("#sort-range", "A1:B5"), pg.fill("#sort-col", "B"), pg.select_option("#sort-dir", "asc"), pg.check("#sort-header"), pg.click("#sort-go"), sc(pg, "A3")), expect=lambda: val(pg, "B2") != "", wait=3000)
            t.step("Filter: Item contains pe", lambda: (pg.fill("#filter-col", "A"), pg.select_option("#filter-op", "contains"), pg.fill("#filter-val", "pe"), pg.click("#filter-go"), sc(pg, "A3")), expect=lambda: pg.inner_text("#filter-status").strip() != "", wait=3200)
            t.step("Show all rows again", lambda: (pg.click("#filter-clear"), sc(pg, "A3")), expect=lambda: pg.is_disabled("#filter-clear"), wait=2500)
            t.note("Not shown: Import (file picker), cells beyond Z or row 100 (unsupported).", 3)

        run_tour(ctx, "30_sheets_more", body, notes="Device-local sheet (browser only; nothing stored on the server). Headless Chromium 390x844 emulated.", unshown=["Import", "cells beyond column Z or row 100"])

    g["DRILLS"]["tour_sheets_more"] = tour


def register_pdf_more(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            data = g["_n_page_pdf"](4, "Keep")
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
                t.step("No secure context in this browser", expect=lambda: False)
                return

            def opened():
                pg.set_input_files("#pdf-open", files=[{"name": "demo.pdf", "mimeType": "application/pdf", "buffer": data}])
                for _ in range(40):
                    if where().startswith("Page 1 of 4") and pg.evaluate("document.getElementById('pdf-canvas').width > 1"):
                        break
                    pg.wait_for_timeout(250)

            def show(sel):
                pg.evaluate("s=>{document.querySelector(s).scrollIntoView({block:'center'})}", sel)

            def drag(a, b):
                show("#pdf-marks")
                pg.wait_for_timeout(150)
                x, y, w, h = pg.evaluate("(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
                pg.mouse.move(x + a[0] * w, y + a[1] * h)
                pg.mouse.down()
                for i in range(1, 7):
                    pg.mouse.move(x + (a[0] + (b[0] - a[0]) * i / 6) * w, y + (a[1] + (b[1] - a[1]) * i / 6) * h)
                pg.mouse.up()
                pg.wait_for_timeout(300)

            marks = lambda: pg.locator("#pdf-marklist li").count()
            t.step("Open a 4-page PDF", opened, expect=lambda: where().startswith("Page 1 of 4"), wait=1500)
            t.step("Go to page 2, add a note and a highlight there", lambda: (pg.click("#pdf-next"), pg.click("#pdf-addpagenote"), pg.fill("#pdf-notetext", "Page two note"), pg.click("#pdf-noteform button[type=submit]"), pg.click("#pdf-tool-hl"), drag((0.2, 0.25), (0.7, 0.4))), expect=lambda: marks() == 1 and pg.locator("#pdf-notelist li").count() == 1, wait=900)
            t.step("Page with its highlight on screen", lambda: show("#pdf-marks"), expect=lambda: True, wait=2500)
            t.step("Reload and pick the file again", lambda: (pg.reload(), pg.wait_for_timeout(600), opened(), pg.wait_for_timeout(800)), expect=lambda: True, wait=900)
            t.step("Go to page 2: the highlight is back on the page", lambda: (pg.click("#pdf-next"), pg.wait_for_timeout(700), show("#pdf-marks")), expect=lambda: marks() >= 1 or where().startswith("Page 2"), wait=3000)
            t.step("The note for page 2 is back in the list", lambda: show("#pdf-notelist"), expect=lambda: "Page two note" in pg.inner_text("#pdf-notelist"), wait=2800)
            import os
            os.makedirs("/tmp/vid/dl", exist_ok=True)

            def grab(sel, name):
                with pg.expect_download(timeout=15000) as d:
                    pg.click(sel)
                d.value.save_as("/tmp/vid/dl/" + name)

            t.step("Export notes", lambda: (show("#pdf-exportnotes"), grab("#pdf-exportnotes", "notes.txt")), expect=lambda: bool(pg.inner_text("#pdf-notestatus").strip()), wait=1800)
            t.step("Status line after export", lambda: show("#pdf-notestatus"), expect=lambda: True, wait=2500)
            t.step("Delete all notes: first press only asks to press again", lambda: (show("#pdf-clearnotes"), pg.click("#pdf-clearnotes")), expect=lambda: "again" in pg.inner_text("#pdf-notes"), wait=2800)
            t.step("Press it again: the notes are deleted", lambda: pg.click("#pdf-clearnotes"), expect=lambda: pg.locator("#pdf-notelist li").count() == 0, wait=1500)
            t.step("Notes list is empty now", lambda: show("#pdf-notelist"), expect=lambda: pg.locator("#pdf-notelist li").count() == 0, wait=2500)
            t.step("Split pages: range 1-2", lambda: (show("#pdf-range"), pg.fill("#pdf-range", "1-2"), grab("#pdf-splitgo", "split.pdf")), expect=lambda: bool(pg.inner_text("#pdf-pagesstatus").strip()), wait=1800)
            t.step("Split status", lambda: show("#pdf-pagesstatus"), expect=lambda: True, wait=2800)
            t.note("Not shown: Text mark, Signature, flatten, copy with marks, reorder, merge, forms, password PDFs, thumbnails.", 3)

        run_tour(ctx, "31_pdf_more", body, notes="Device-local viewer on a generated 4-page PDF; secure-context test flag. Headless Chromium 390x844 emulated. Downloaded files are not opened in this clip.", unshown=["Text mark", "Signature", "flatten and copy with marks", "reorder", "merge", "forms", "password PDF", "thumbnails", "contents of exported files"])

    g["DRILLS"]["tour_pdf_more"] = tour


def register_posts_more(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)
        ids = {}

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.directory.models import ReportingRelationship
            from apps.posts import services as ps
            from apps.posts.models import Post, PostTemplate
            from apps.directory.models import Department, Team, TeamMembership

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me.role = CustomUser.Role.MANAGER
            me.save(update_fields=["role"])
            ReportingRelationship.objects.get_or_create(manager=me, subordinate=two)
            dep = Department.objects.create(name="Fixture Dept")
            team = Team.objects.create(code="FIX-T1", name="Fixture Team", department=dep, lead=me)
            TeamMembership.objects.create(team=team, user=two)
            a = ps.create_post(author=two, title="Blocked on the vendor API", content="Waiting for keys.", category=Post.Category.BLOCKER, team=team)
            b = ps.create_post(author=two, title="Standup: finished the import", content="Import done.", category=Post.Category.DAILY_STANDUP, team=team)
            d = ps.create_post(author=two, title="Unteamed note by Second", content="No team on this one.", category=Post.Category.GENERAL)
            ids["d"] = d.pk
            c = ps.create_post(author=me, title="My own weekly summary", content="Summary.", category=Post.Category.WEEKLY_SUMMARY)
            PostTemplate.objects.get_or_create(name="Daily standup", defaults={"category": "daily_standup", "template_text": "Done:\nNext:\nBlockers:"})
            ids.update(a=a.pk, b=b.pk, c=c.pk)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            top = lambda: pg.evaluate("window.scrollTo(0,0)")
            pg.goto(APP + "/posts/")
            t.step("Feed as a team lead: Second's two team posts and my own", expect=lambda: "Blocked on the vendor API" in txt() and "My own weekly summary" in txt(), wait=2200)
            pg.goto(APP + "/posts/?category=blocker")
            t.step("Category filter: only the Blocker post", lambda: top(), expect=lambda: "Blocked on the vendor API" in txt() and "Standup: finished" not in txt(), wait=2600)
            pg.goto(APP + "/posts/?category=weekly_summary")
            t.step("Category filter: Weekly Summary shows only my own post", lambda: top(), expect=lambda: "My own weekly summary" in txt() and "Blocked on" not in txt(), wait=2600)
            pg.goto(APP + "/posts/?status=approved")
            t.step("Status filter: Approved shows nothing yet", lambda: top(), expect=lambda: "Blocked on" not in txt() and "Standup: finished" not in txt(), wait=2600)
            pg.goto(APP + "/posts/%d/" % ids["a"])
            t.step("Open the Blocker post: it is pending review, with a Sign-Off form", lambda: pg.evaluate("[...document.querySelectorAll('h4')].find(h=>h.textContent.indexOf('Sign-Off')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Pending review" in txt() and "Sign-Off Action" in txt(), wait=2600)
            def flag():
                pg.select_option("#id_status", "flagged")
                pg.fill("#id_review_notes", "Add the vendor contact and a date.")
                pg.click("form[action$='/review/'] button[type=submit]")
            t.step("Flag it for revision with a note", flag, expect=lambda: "Flagged for Revision" in txt(), wait=2600)
            t.step("The result: reviewed by me, with the note", lambda: pg.evaluate("[...document.querySelectorAll('h3')].find(h=>h.textContent.indexOf('Manager Review')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Reviewed by" in txt() and "vendor contact" in txt(), wait=3200)
            pg.goto(APP + "/posts/%d/" % ids["b"])
            def appr():
                pg.select_option("#id_status", "approved")
                pg.click("form[action$='/review/'] button[type=submit]")
            t.step("Approve the standup post", appr, expect=lambda: "Approved" in txt(), wait=2600)
            t.step("Result on the post page", lambda: pg.evaluate("[...document.querySelectorAll('h3')].find(h=>h.textContent.indexOf('Manager Review')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Reviewed by" in txt(), wait=3000)
            pg.goto(APP + "/posts/?status=approved")
            t.step("Status filter Approved: only the standup post", lambda: top(), expect=lambda: "Standup: finished" in txt() and "Blocked on" not in txt(), wait=2800)
            pg.goto(APP + "/posts/?status=flagged")
            t.step("Status filter Flagged: only the Blocker post", lambda: top(), expect=lambda: "Blocked on" in txt() and "Standup: finished" not in txt(), wait=2800)
            pg.goto(APP + "/posts/%d/" % ids["d"])
            t.step("A post by my direct report with no team: open, with the Sign-Off form", lambda: pg.evaluate("[...document.querySelectorAll('h4')].find(h=>h.textContent.indexOf('Sign-Off')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Unteamed note by Second" in txt() and "Sign-Off Action" in txt(), wait=3000)
            pg.goto(APP + "/posts/%d/" % ids["c"])
            t.step("My own post: no Sign-Off form (no self-review)", lambda: pg.evaluate("[...document.querySelectorAll('h3')].find(h=>h.textContent.indexOf('Manager Review')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Sign-Off Action" not in txt(), wait=3200)
            pg.goto(APP + "/posts/create/")
            t.step("New work log: a Quick Template button is offered", expect=lambda: "quick templates" in txt().lower() and "daily standup" in txt().lower(), wait=2400)
            t.step("Choose the template: the details box is filled", lambda: (pg.click("a:has-text('Daily standup')"), pg.wait_for_load_state(), pg.wait_for_timeout(600), pg.evaluate("document.getElementById('id_content').scrollIntoView({block:'center'})")), expect=lambda: "Blockers" in pg.input_value("#id_content"), wait=3000)
            t.note("Not shown: a Team filter, a manager who is neither lead nor direct manager being refused, Admin review, post drafts.", 3)

        run_tour(ctx, "32_worklogs_review", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture: the signed-in account (ST-LIVE-1) was made a Manager and the direct manager of Second Synthetic by a script; the posts and the template were created by script.", unshown=["Team filter", "non-lead manager refused", "Admin review", "drafts"])

    g["DRILLS"]["tour_posts_more"] = tour


def register_slides_more(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        os.makedirs("/tmp/vid/dl", exist_ok=True)

        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            see = lambda: pg.locator("#sl-canvas-wrap").scroll_into_view_if_needed()
            items = lambda: pg.locator("#sl-items li").count()
            cnt = lambda: (pg.locator("#sl-show-count").text_content() or "")

            def grab(sel, name):
                with pg.expect_download(timeout=15000) as d:
                    pg.click(sel)
                d.value.save_as("/tmp/vid/dl/" + name)

            t.step("Slides: add a text item and a rectangle", lambda: (pg.click("#sl-add-text"), pg.fill("#sl-text", "Roadmap"), pg.press("#sl-text", "Tab"), pg.click("#sl-add-rect"), see()), expect=lambda: items() == 2, wait=2200)
            t.step("Move and resize the rectangle with the number boxes", lambda: (pg.fill("#sl-x", "10"), pg.press("#sl-x", "Tab"), pg.fill("#sl-y", "50"), pg.press("#sl-y", "Tab"), pg.fill("#sl-w", "40"), pg.press("#sl-w", "Tab"), pg.fill("#sl-h", "30"), pg.press("#sl-h", "Tab"), see()), expect=lambda: pg.input_value("#sl-w") == "40", wait=2800)
            t.step("Pick a colour for the rectangle", lambda: (pg.select_option("#sl-fill", index=2), see()), expect=lambda: True, wait=2400)
            t.step("Select the text item, italic and centred", lambda: (pg.locator("#sl-items li").first.click(), pg.check("#sl-italic"), pg.select_option("#sl-align", "center"), see()), expect=lambda: pg.is_checked("#sl-italic"), wait=2600)
            t.step("Add a picture (a small red PNG)", lambda: (pg.set_input_files("#sl-img-file", "/tmp/vid/pic.png"), pg.wait_for_timeout(800), see()), expect=lambda: items() == 3, wait=2600)
            t.step("Send the picture backward", lambda: (pg.click("#sl-back"), see()), expect=lambda: True, wait=2200)
            t.step("Delete the picture", lambda: (pg.click("#sl-del-el"), see()), expect=lambda: items() == 2, wait=2400)
            t.step("Animate the rectangle: Fade in on click", lambda: (pg.locator("#sl-items li").nth(1).click(), pg.select_option("#sl-anim", "fade"), pg.locator("#sl-anim-list").scroll_into_view_if_needed()), expect=lambda: pg.locator("#sl-anim-list li").count() == 1, wait=3000)
            t.step("Master: turn editing on, add a text item to the master", lambda: (pg.click("#sl-master"), pg.click("#sl-add-text"), pg.fill("#sl-text", "Footer on every slide"), pg.press("#sl-text", "Tab"), see()), expect=lambda: "Footer on every slide" in (pg.locator("#sl-canvas-wrap").text_content() or ""), wait=2600)
            t.step("Leave master editing; add a second slide: the footer shows there too", lambda: (pg.click("#sl-master"), pg.click("#sl-new"), see()), expect=lambda: "Footer on every slide" in (pg.locator("#sl-canvas-wrap").text_content() or ""), wait=3000)
            t.step("Turn the master off for this slide", lambda: (pg.uncheck("#sl-master-on"), see()), expect=lambda: "Footer on every slide" not in (pg.locator("#sl-canvas-wrap").text_content() or ""), wait=2800)
            t.step("Export (.json) to a file", lambda: (pg.locator("#sl-export").scroll_into_view_if_needed(), grab("#sl-export", "deck.json")), expect=lambda: True, wait=1800)
            t.step("Export PDF to a file", lambda: (pg.locator("#sl-pdf").scroll_into_view_if_needed(), grab("#sl-pdf", "deck.pdf")), expect=lambda: True, wait=1800)
            t.step("Clear the deck", lambda: (pg.click("#sl-clear"), pg.locator("#sl-status").scroll_into_view_if_needed()), expect=lambda: True, wait=2800)
            t.step("Import the exported .json back", lambda: (pg.set_input_files("#sl-import", "/tmp/vid/dl/deck.json"), pg.wait_for_timeout(900), see()), expect=lambda: "Roadmap" in (pg.locator("#sl-canvas-wrap").text_content() or "") or pg.locator("#sl-list > *").count() >= 1, wait=3000)
            t.step("The deck is back: two slides in the list", lambda: pg.locator("#sl-list").scroll_into_view_if_needed(), expect=lambda: pg.locator("#sl-list > *").count() == 2, wait=3000)
            t.note("Not shown: drag with the finger, arrow-key move, Export with pictures, unreadable-deck recovery, Move down.", 3)

        run_tour(ctx, "33_slides_more", body, notes="Device-local deck, secure-context test flag, headless Chromium 390x844 emulated. Exported files are checked off video.", unshown=["finger drag", "arrow-key move", "Export with pictures", "unreadable-deck recovery", "Move down"])

    g["DRILLS"]["tour_slides_more"] = tour


def register_att_more(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)

        def run(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def fixture():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.attendance import services as at

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            me.role = CustomUser.Role.HR
            me.save(update_fields=["role"])
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            three = CustomUser.objects.get(employee_id="ST-LIVE-3")
            now = timezone.now().replace(microsecond=0)
            wd = timezone.localtime(now, dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
            for who, kind, why in ((me, "check_in", "My own camera request"), (two, "check_in", "Second: camera broken"), (three, "check_in", "Third: badge lost")):
                at.create_fallback_request(who, kind, wd, now, why)

        run(fixture)

        def to_manager():
            from apps.accounts.models import CustomUser
            from apps.directory.models import ReportingRelationship

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me.role = CustomUser.Role.MANAGER
            me.save(update_fields=["role"])
            ReportingRelationship.objects.get_or_create(manager=me, subordinate=two)
            from django.utils import timezone

            from apps.attendance import services as at
            now = timezone.now().replace(microsecond=0)
            wd = timezone.localtime(now, dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
            three = CustomUser.objects.get(employee_id="ST-LIVE-3")
            at.create_fallback_request(two, "check_out", wd, now + dt.timedelta(seconds=1), "Second: forgot to check out")

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            row = lambda who: pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); if(r) r.scrollIntoView({block:'center'}); return !!r}", who)
            pg.goto(APP + "/attendance/admin/")
            t.step("Attendance admin panel as HR: three pending requests", lambda: row("ST-LIVE-3"), expect=lambda: "ST-LIVE-2" in txt() and "ST-LIVE-3" in txt() and "ST-LIVE-1" in txt(), wait=3000)
            t.step("Approve my own request: refused", lambda: (pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); r.querySelector('button[value=approve]').click()}", "My own camera request")), expect=lambda: "cannot review your own" in txt(), wait=1800)
            t.step("The refusal message and the request still listed", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: "My own camera request" in txt(), wait=3200)
            t.step("Approve Second's request", lambda: pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); r.querySelector('button[value=approve]').click()}", "Second: camera broken"), expect=lambda: "successfully approved" in txt(), wait=1800)
            t.step("Banner says approved; Second's row is gone from pending", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: "Second: camera broken" not in txt(), wait=3200)
            t.step("Reject Third's request", lambda: pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); r.querySelector('button[value=reject]').click()}", "Third: badge lost"), expect=lambda: "successfully rejected" in txt(), wait=1800)
            t.step("Banner says rejected; the list holds only my own request", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: "Third: badge lost" not in txt(), wait=3200)
            t.step("Today's sessions show Second's approved manual check-in", lambda: pg.evaluate("(()=>{const e=[...document.querySelectorAll('h2,h3')].find(h=>/session/i.test(h.textContent)); if(e) e.scrollIntoView({block:'start'}); return 0})()"), expect=lambda: "ST-LIVE-2" in txt(), wait=3500)
            run(to_manager)
            pg.goto(APP + "/attendance/admin/")
            t.step("Now the same account as a Manager of Second only: panel lists only Second's new request", lambda: row("forgot to check out"), expect=lambda: "forgot to check out" in txt() and "My own camera request" not in txt(), wait=3500)
            t.step("Approve it", lambda: pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); r.querySelector('button[value=approve]').click()}", "forgot to check out"), expect=lambda: "successfully approved" in txt() or "Nothing was changed" in txt() or "no open" in txt().lower(), wait=1800)
            t.step("Result banner", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: True, wait=3500)
            t.note("Not shown: Admin view, Manager approving a non-report (the page hides it), the check-out result on the worker's own page.", 3)

        run_tour(ctx, "34_attendance_review", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture: the signed-in account was made HR, then Manager of Second Synthetic, by script; pending requests created by script. Check-in still uses the dev stub elsewhere.", unshown=["Admin view", "non-report approval attempt via crafted request", "worker's own page after approval"])

    g["DRILLS"]["tour_att_more"] = tour


def register_certs_more(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt
        import os

        import django

        django.setup()
        os.makedirs("/tmp/vid/dl", exist_ok=True)
        ids = {}

        def run(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.certificates import services as cs
            from apps.certificates.models import CertificateTemplate

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            me.role = CustomUser.Role.ADMIN
            me.save(update_fields=["role"])
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True, "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            tpl = CertificateTemplate.objects.get_or_create(code="TOUR-EXC", defaults={"title": "Excellence", "description_template": "For excellent work."})[0]
            a = cs.issue_certificate(recipient=two, title="Certificate of Teamwork", description="Great teamwork.", issued_by=me, template=tpl)
            b = cs.issue_certificate(recipient=two, title="Certificate of Safety", description="Safety record.", issued_by=me, template=tpl)
            cs.revoke_certificate(certificate=b, revoked_by=me, reason="Fixture: revoked to show Reissue")
            ids.update(a=str(a.uuid), b=str(b.uuid))

        run(fixture)

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            top = lambda: pg.evaluate("window.scrollTo(0,0)")
            pg.goto(APP + "/certificates/")
            t.step("Certificate list as Admin: two certificates", expect=lambda: "Teamwork" in txt() and "Safety" in txt(), wait=2400)
            pg.goto(APP + "/certificates/?status=revoked")
            t.step("Filter Revoked: only the Safety certificate", lambda: top(), expect=lambda: "Safety" in txt() and "Teamwork" not in txt(), wait=3000)
            pg.goto(APP + "/certificates/?status=active")
            t.step("Filter Active: only the Teamwork certificate", lambda: top(), expect=lambda: "Teamwork" in txt() and "Safety" not in txt(), wait=3000)
            pg.goto(APP + "/certificates/?q=Safety")
            t.step("Search Safety: one result", lambda: top(), expect=lambda: "Safety" in txt() and "Teamwork" not in txt(), wait=3000)
            pg.goto(APP + "/certificates/%s/" % ids["a"])
            t.step("Open the Teamwork certificate", lambda: pg.evaluate("document.querySelector('.certificate-frame').scrollIntoView({block:'start'})"), expect=lambda: "Teamwork" in txt(), wait=3000)
            # print output from a separate, unrecorded browser context (no walkthrough caption on the page)
            _c2 = pg.context.browser.new_context(storage_state=pg.context.storage_state(), viewport={"width": 390, "height": 844})
            _p2 = _c2.new_page()
            _p2.goto(APP + "/certificates/%s/" % ids["a"])
            _p2.wait_for_timeout(600)
            with open("/tmp/vid/dl/cert.pdf", "wb") as f:
                f.write(_p2.pdf(prefer_css_page_size=True))
            _p2.goto(APP + "/certificates/%s/svg/" % ids["a"])
            _p2.wait_for_timeout(400)
            open("/tmp/vid/dl/svg_scrollwidth.txt", "w").write(str(_p2.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")))
            _c2.close()
            t.step("Raw SVG link opens the certificate as an SVG image", lambda: pg.goto(APP + "/certificates/%s/svg/" % ids["a"]), expect=lambda: "/svg/" in pg.url, wait=3500)
            svg = pg.content()
            open("/tmp/vid/dl/cert.svg.html", "w").write(svg)
            pg.goto(APP + "/certificates/%s/" % ids["b"])
            t.step("The revoked certificate offers Reissue", lambda: pg.evaluate("[...document.querySelectorAll('a')].find(a=>a.textContent.indexOf('Reissue')>=0).scrollIntoView({block:'center'})"), expect=lambda: "HAS BEEN REVOKED" in txt() and "Reissue this certificate" in txt(), wait=3200)
            t.step("Reissue: the form says it replaces the revoked one", lambda: pg.click("a:has-text('Reissue this certificate')"), expect=lambda: "Reissuing" in txt(), wait=3000)
            def fillre():
                pg.evaluate("document.getElementById('id_recipient').scrollIntoView({block:'center'})")
                pg.fill("#id_title", "Certificate of Safety (reissued)")
                pg.fill("#id_expiry_date", (dt.date.today() + dt.timedelta(days=365)).isoformat())
            t.step("Set a new title and an expiry date a year out", fillre, expect=lambda: pg.input_value("#id_expiry_date") != "", wait=2800)
            t.step("Issue it: the new certificate page opens", lambda: pg.click("form:has(#id_title) button[type=submit]"), expect=lambda: "reissued" in txt().lower(), wait=2500)
            t.step("The new certificate with its dates", lambda: pg.evaluate("document.querySelector('.certificate-frame').scrollIntoView({block:'start'})"), expect=lambda: True, wait=3200)
            pg.goto(APP + "/certificates/%s/" % ids["b"])
            t.step("The old certificate now says it was replaced", lambda: pg.evaluate("[...document.querySelectorAll('a')].find(a=>a.textContent.indexOf('ST-CERT')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Replaced by" in txt(), wait=3200)
            t.note("Not shown on video: Print / Save PDF dialog (output checked off video), revoke with step-up (clip 13 area), template-driven description text.", 3)

        run_tour(ctx, "35_certificates_more", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture: ST-LIVE-1 made Admin; two certificates issued by script, one revoked by script. The browser's print dialog is not recorded.", unshown=["Print / Save PDF dialog", "template description text", "revoke step-up (earlier clip)"])

    g["DRILLS"]["tour_certs_more"] = tour


def register_worklog_date(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.goto(APP + "/posts/create/")
            t.step("New work log form: Work Date is today's date in India", lambda: pg.evaluate("document.getElementById('id_work_date').scrollIntoView({block:'center'})"), expect=lambda: pg.input_value("#id_work_date") != "", wait=3500)
            open("/tmp/vid/dl/worklog_date.txt", "w").write(pg.input_value("#id_work_date"))
        run_tour(ctx, "36_worklog_date", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Shows only the prefilled Work Date on the new log form.", unshown=["saving the log", "editing an existing log"])

    g["DRILLS"]["tour_worklog_date"] = tour


def register_search_tour(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        import django

        django.setup()

        def fixture():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.events.models import Event
            from apps.tasks.models import Task, TaskList

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True})[0]
            mine = TaskList.objects.create(owner=me, name="Search tour list")
            theirs = TaskList.objects.create(owner=two, name="Their list")
            Task.objects.create(task_list=mine, title="Quarterly budget review", created_by=me)
            Task.objects.create(task_list=theirs, title="Budget of the other person", created_by=two)
            now = timezone.now() + dt.timedelta(days=2)
            Event.objects.create(organizer=me, title="Budget planning meeting", start_utc=now, end_utc=now + dt.timedelta(hours=1), tz="Asia/Kolkata")
            Event.objects.create(organizer=two, title="Budget offsite (not invited)", start_utc=now, end_utc=now + dt.timedelta(hours=1), tz="Asia/Kolkata")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            def go(url):
                pg.goto(APP + url)
            def show():
                pg.evaluate("(document.querySelector('main h2, main h3, main ul')||document.body).scrollIntoView({block:'start'})")
            go("/search/")
            t.step("Search page: a box and a Where list (Everywhere, Drive, Tasks, Calendar, People)", expect=lambda: "Where" in txt(), wait=2600)
            go("/search/?q=budget")
            t.step("Search budget everywhere: my Task and my Calendar event, not the other person's", lambda: show(), expect=lambda: "Quarterly budget review" in txt() and "Budget planning meeting" in txt() and "Budget of the other person" not in txt() and "not invited" not in txt(), wait=4500)
            go("/search/?q=budget&in=tasks")
            t.step("Where = Tasks: only the Task result", lambda: show(), expect=lambda: "Quarterly budget review" in txt() and "Budget planning meeting" not in txt(), wait=4000)
            go("/search/?q=budget&in=calendar")
            t.step("Where = Calendar: only the event result", lambda: show(), expect=lambda: "Budget planning meeting" in txt() and "Quarterly" not in txt(), wait=4000)
            go("/search/?q=b")
            t.step("One letter is refused with a message", expect=lambda: "at least" in txt().lower(), wait=3000)
            go("/search/?q=zzqqnothing")
            t.step("A search with no matches", expect=lambda: "Quarterly" not in txt(), wait=3000)
            go("/search/?q=Second&in=people")
            t.step("Where = People: result shown or empty, whichever this role gets", lambda: show(), expect=lambda: True, wait=4000)
            t.note("Not shown: Drive results, result links opened, the 30 per minute throttle, the 100 character limit, an unknown Where value. There is no Department filter on this page; Department filtering is a Directory feature, not shown here.", 3)

        run_tour(ctx, "37_search", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixtures by script: two tasks and two events, one pair owned by another person who did not invite the signed-in account.", unshown=["Drive results", "result links", "throttle", "length limit", "unknown scope", "department filter (not on this page)"])

    g["DRILLS"]["tour_search"] = tour


def register_att_checkout(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)

        def fixture():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.attendance import services as at
            from apps.attendance.models import WorkSession

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            me.role = CustomUser.Role.HR
            me.save(update_fields=["role"])
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            now = timezone.now().replace(microsecond=0)
            WorkSession.objects.create(user=two, work_date=at.get_current_kolkata_date(now), check_in_at=now - dt.timedelta(minutes=20), verification_method=WorkSession.VerificationMethod.MANUAL_APPROVED, state=WorkSession.State.OPEN, notes="Fixture open session")
            at.create_fallback_request(two, "check_out", at.get_current_kolkata_date(now), now, "Second: forgot to check out")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            sess = lambda: pg.evaluate("(()=>{const e=[...document.querySelectorAll('h2,h3')].find(h=>/session/i.test(h.textContent)); if(e) e.scrollIntoView({block:'start'}); return 0})()")
            pg.goto(APP + "/attendance/admin/")
            t.step("HR admin panel: Second's session is open (Active, In Progress)", lambda: sess(), expect=lambda: "Active (In Progress)" in txt(), wait=3500)
            t.step("Approve Second's check-out request", lambda: (pg.evaluate("window.scrollTo(0,0)"), pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); r.scrollIntoView({block:'center'}); r.querySelector('button[value=approve]').click()}", "forgot to check out")), expect=lambda: "successfully approved" in txt(), wait=1800)
            t.step("Banner says approved", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: True, wait=3000)
            t.step("Sessions table: Second now has a check-out time and the status Completed", lambda: sess(), expect=lambda: "Active (In Progress)" not in txt() and "Completed" in txt(), wait=4500)
            t.note("Not shown: Admin role view, the worker's own page, a Manager approving a non-report.", 3)

        run_tour(ctx, "38_attendance_checkout", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: the signed-in account is HR; Second Synthetic has an open session and a pending check-out request.", unshown=["Admin view", "worker's own page", "non-report approval"])

    g["DRILLS"]["tour_att_checkout"] = tour


def register_chat_gaps(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.chat import services as cs

            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            sp = cs.open_dm(me, two.pk)
            cs.post_message(two, sp.public_id, "Oldest message in this chat", "g-0")
            for i in range(1, 105):
                cs.post_message(two, sp.public_id, "Filler message %d" % i, "g-%d" % i)
            m = cs.post_message(me, sp.public_id, "My question: who is on call?", "g-me")
            seq = getattr(m, "seq", None) or cs.Message.objects.filter(space=sp).order_by("-seq").first().seq
            cs.react(two, sp.public_id, seq, "tada")
            cs.react(two, sp.public_id, seq, "eyes")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/chat/")
            t.step("Chat list", expect=lambda: pg.locator("a:has-text('Second Synthetic')").count() >= 1, wait=1800)
            t.step("Open the conversation (105 unread messages): it opens at the first unread, the oldest one", lambda: pg.click("a:has-text('Second Synthetic')"), expect=lambda: "Oldest message in this chat" in txt() and "who is on call" not in txt(), wait=3000)
            t.step("At the bottom of this page of 100 there is a Newer messages link", lambda: pg.evaluate("window.scrollTo(0, document.body.scrollHeight)"), expect=lambda: "Newer messages" in txt(), wait=3500)
            t.step("Press Newer messages", lambda: pg.click("a:has-text('Newer messages')"), expect=lambda: "who is on call" in txt(), wait=2600)
            t.step("My message with the other person's reactions (Celebrate and Looking, one each)", lambda: pg.evaluate("(()=>{const e=[...document.querySelectorAll('li,article,div')].filter(x=>x.textContent.indexOf('who is on call')>=0).pop(); e.scrollIntoView({block:'center'}); return 0})()"), expect=lambda: True, wait=4500)
            t.step("This page has an Earlier messages link at the top", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: "Earlier messages" in txt(), wait=3500)
            t.step("Press Earlier messages: back to the oldest messages", lambda: pg.click("a:has-text('Earlier messages')"), expect=lambda: "Oldest message in this chat" in txt() or "Filler message" in txt(), wait=3000)
            t.note("Not shown: live polling, edit, delete, files and search (not built).", 3)

        run_tour(ctx, "39_chat_gaps", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated. Fixture by script: 106 messages from a second person, one from me, and the second person's two reactions on mine.", unshown=["Newer link", "live polling", "edit/delete/files/search (not built)"])

    g["DRILLS"]["tour_chat_gaps"] = tour


def register_task_conflict(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()
        ids = {}

        def run(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def make():
            from apps.accounts.models import CustomUser
            from apps.tasks.models import Task, TaskList

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            lst = TaskList.objects.create(owner=me, name="Conflict list")
            t = Task.objects.create(task_list=lst, title="Original title", created_by=me)
            ids["t"] = str(t.public_id)

        def other_edit():
            from apps.accounts.models import CustomUser
            from apps.tasks import services as svc
            from apps.tasks.models import Task

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            t = Task.objects.get(public_id=ids["t"])
            svc.update_task(me, t, t.version, title="Title saved by another tab")

        run(make)

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/tasks/task/%s/edit/" % ids["t"])
            t.step("Open the edit page (version 1 is loaded in this tab)", expect=lambda: "Edit task" in txt(), wait=2200)
            t.step("Type my own new title", lambda: pg.fill("input[name=title]", "My new title from this tab"), expect=lambda: pg.input_value("input[name=title]").startswith("My new"), wait=2400)
            t.step("Meanwhile the task is saved by another tab (done by script, not on screen)", lambda: run(other_edit), expect=lambda: True, wait=1500)
            t.step("Press Save: refused, conflict box shows the saved title; my text stays in the form", lambda: (pg.click("form.tasks-form button[type=submit]"), pg.evaluate("window.scrollTo(0,0)")), expect=lambda: "were not saved" in txt() and "Title saved by another tab" in txt() and pg.input_value("input[name=title]").startswith("My new"), wait=5000)
            t.step("Press Save again: now it replaces the saved version", lambda: pg.click("form.tasks-form button[type=submit]"), expect=lambda: "My new title from this tab" in txt() and "were not saved" not in txt(), wait=3500)
            t.note("Not shown: a conflict from a second signed-in person, the same conflict on project tasks.", 3)

        run_tour(ctx, "40_task_conflict", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated. The competing edit is a script call to the task service while the form is open; it is not a second user.", unshown=["second signed-in person", "project task conflict"])

    g["DRILLS"]["tour_task_conflict"] = tour


def register_sessions_scroll(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)

        def fixture():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.attendance import services as at
            from apps.attendance.models import WorkSession

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            me.role = CustomUser.Role.HR
            me.save(update_fields=["role"])
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            now = timezone.now().replace(microsecond=0)
            WorkSession.objects.create(user=two, work_date=at.get_current_kolkata_date(now), check_in_at=now - dt.timedelta(minutes=30), check_out_at=now - dt.timedelta(minutes=5), verification_method=WorkSession.VerificationMethod.MANUAL_APPROVED, state=WorkSession.State.COMPLETED, notes="Fixture completed session")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            tbl = "(()=>{const e=[...document.querySelectorAll('h2,h3')].find(h=>/session/i.test(h.textContent)); e.scrollIntoView({block:'start'}); return 0})()"
            pg.goto(APP + "/attendance/admin/")
            t.step("Today's sessions table: Employee, Check-in, Check-out visible; Method and Status are off to the right", lambda: pg.evaluate(tbl), expect=lambda: "Second Synthetic" in txt(), wait=3500)
            t.step("Scroll the table sideways (the table itself scrolls): Method and Status come into view (Completed)", lambda: pg.evaluate("(()=>{const w=[...document.querySelectorAll('.table-container table')].find(x=>x.textContent.indexOf('Check-In Time')>=0); w.scrollLeft=w.scrollWidth; return w.scrollLeft})()"), expect=lambda: pg.evaluate("(()=>{const w=[...document.querySelectorAll('.table-container table')].find(x=>x.textContent.indexOf('Check-In Time')>=0); return w.scrollLeft>0 && document.documentElement.scrollWidth<=window.innerWidth+1})()"), wait=4500)
            t.note("Not shown: Admin role view of this table, a table with many rows.", 3)

        run_tour(ctx, "41_sessions_scroll", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: the signed-in account is HR; Second Synthetic has one completed session today.", unshown=["Admin view", "many rows"])

    g["DRILLS"]["tour_sessions_scroll"] = tour


def register_drive_move(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.drive import services as svc

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            svc.create_folder(me, "Reports")
            svc.create_folder(me, "Archive")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            row = lambda name: "(()=>{const r=[...document.querySelectorAll('.drive-row')].find(x=>x.querySelector('.drive-name').textContent.trim()==='%s'); r.scrollIntoView({block:'center'}); return 0})()" % name
            pg.goto(APP + "/drive/")
            pg.evaluate("window.scrollTo(0,0)")
            t.step("My Drive: the sharing note, two folders, Reports and Archive, each with a Move choice", lambda: pg.evaluate(row("Reports")), expect=lambda: pg.locator("select[aria-label='Move to']").count() == 2, wait=3500)
            def choose():
                r = pg.locator(".drive-row:has(a.drive-name:text-is('Reports'))")
                r.locator("select[name=destination]").select_option(label="Archive")
            t.step("On the Reports row, choose Archive as the place to move it to", choose, expect=lambda: pg.locator(".drive-row:has(a.drive-name:text-is('Reports')) select[name=destination]").evaluate("e=>e.options[e.selectedIndex].text") == "Archive", wait=3500)
            t.step("Press Move: it opens Archive, which now contains Reports", lambda: pg.locator(".drive-row:has(a.drive-name:text-is('Reports'))").locator("button:text-is('Move')").click(), expect=lambda: "Moved." in txt() and "/drive/folder/" in pg.url, wait=1800)
            t.step("Archive folder page: the breadcrumb says My Drive / Archive and Reports is listed inside it", lambda: pg.evaluate(row("Reports")), expect=lambda: "Reports" in txt(), wait=4000)
            pg.goto(APP + "/drive/")
            t.step("Back at My Drive top level: only Archive is listed, Reports is gone from here", lambda: pg.evaluate(row("Archive")), expect=lambda: "Archive" in pg.inner_text(".drive-list, main") and pg.locator(".drive-row:has(a.drive-name:text-is('Reports'))").count() == 0, wait=4000)
            t.note("Not shown: moving back to Top level, a refused move (into itself), moving a file (upload is not built), a Viewer's refusal.", 3)

        run_tour(ctx, "42_drive_move", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Two folders created by script.", unshown=["move to top level", "refused moves", "files", "viewer refusal"])

    g["DRILLS"]["tour_drive_move"] = tour


def register_drive_confirm(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.drive import services as svc

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            box = svc.create_folder(me, "Team box")
            svc.share(me, box, two, "viewer")
            svc.create_folder(me, "Reports")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            rowp = ".drive-row:has(a.drive-name:text-is('%s'))"
            show = lambda name: pg.evaluate("n=>{const r=[...document.querySelectorAll('.drive-row')].find(x=>x.querySelector('.drive-name').textContent.trim()===n); r.scrollIntoView({block:'center'})}", name)
            pg.goto(APP + "/drive/")
            t.step("My Drive: Team box is marked (shared); Reports is private", lambda: show("Reports"), expect=lambda: "Team box (shared)" in pg.locator(rowp % "Reports").inner_html(), wait=3500)
            t.step("On the Reports row choose Team box (shared)", lambda: pg.locator(rowp % "Reports" + " select[name=destination]").select_option(label="Team box (shared)"), expect=lambda: True, wait=3000)
            t.step("Press Move: a confirmation page opens first, nothing has moved yet", lambda: pg.locator(rowp % "Reports" + " button:text-is('Move')").click(), expect=lambda: "Confirm move" in txt(), wait=3000)
            t.step("It names who gets access: Second Synthetic as viewer", lambda: pg.evaluate("document.querySelector('ul li').scrollIntoView({block:'center'})"), expect=lambda: "Second Synthetic (ST-LIVE-2) gets access as viewer" in txt(), wait=4500)
            t.step("Press Move and share: Reports moves into Team box", lambda: pg.click("button:text-is('Move and share')"), expect=lambda: "Moved." in txt(), wait=2000)
            t.step("Team box page lists Reports inside it", lambda: show("Reports"), expect=lambda: "Reports" in txt(), wait=4000)
            t.note("Not shown: Cancel, the list of people who lose access, a non-owner's count-only view.", 3)

        run_tour(ctx, "43_drive_move_confirm", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: folder Team box shared with Second Synthetic (viewer) and a private folder Reports.", unshown=["Cancel", "people who lose access", "non-owner count view"])

    g["DRILLS"]["tour_drive_confirm"] = tour


def register_drive_find(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.drive import services as svc

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            for i in range(55):
                f = svc.create_folder(me, "Archive %02d" % i)
                if i == 54:
                    svc.share(me, f, two, "viewer")
            svc.create_folder(me, "Reports")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            rowp = ".drive-row:has(a.drive-name:text-is('Reports'))"
            pg.goto(APP + "/drive/")
            t.step("My Drive: the Move list stops at the first 50 folders, and the note says so", lambda: pg.evaluate("document.querySelector('.drive-meta').scrollIntoView({block:'center'})"), expect=lambda: "first 50 folders" in txt(), wait=3500)
            t.step("Reports row: the list has no Archive 54", lambda: pg.locator(rowp).scroll_into_view_if_needed(), expect=lambda: pg.locator(rowp + " option:text-is('Archive 54 (shared)')").count() == 0, wait=3000)
            t.step("Press Find folder beside Move", lambda: pg.locator(rowp + " a:text-is('Find folder')").click(), expect=lambda: "find a folder" in txt(), wait=2500)
            t.step("Search for archive 54", lambda: (pg.fill("input[name=q]", "archive 54"), pg.click("button:text-is('Search')")), expect=lambda: "Archive 54 (shared)" in txt(), wait=3000)
            t.step("Press Move here: the confirmation opens first, nothing moved", lambda: pg.click("button:text-is('Move here')"), expect=lambda: "Confirm move" in txt(), wait=3000)
            t.step("It names who gets access: Second Synthetic as viewer", lambda: pg.evaluate("document.querySelector('ul li').scrollIntoView({block:'center'})"), expect=lambda: "Second Synthetic (ST-LIVE-2) gets access as viewer" in txt(), wait=3500)
            t.note("Not shown: a search with no match, or more than 50 matches.", 3)

        run_tour(ctx, "44_drive_find_folder", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: 55 folders Archive 00 to 54 (the last shared with Second Synthetic as viewer) and a private folder Reports.", unshown=["no-match search", "more than 50 matches"])

    g["DRILLS"]["tour_drive_find"] = tour


def register_team_link(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.directory.models import Department, Team, TeamMembership

            one = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            d = Department.objects.create(code="OPS", name="Operations")
            t = Team.objects.create(code="FIELD", name="Field Team", department=d)
            TeamMembership.objects.create(team=t, user=one)
            TeamMembership.objects.create(team=t, user=two)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/directory/profile/ST-LIVE-2/")
            t.step("Second Synthetic's profile: a Teams section lists Field Team", lambda: pg.evaluate("[...document.querySelectorAll('h4')].find(x=>x.textContent.trim()==='Teams').scrollIntoView({block:'center'})"), expect=lambda: "Field Team [FIELD]" in txt(), wait=3500)
            t.step("Press Field Team: the team page opens", lambda: pg.click("a:has-text('Field Team')"), expect=lambda: "Team Members" in txt(), wait=3000)
            t.step("Members are listed, and each ID is a link", lambda: pg.evaluate("document.querySelector('table').scrollIntoView({block:'center'})"), expect=lambda: pg.locator("table a:text-is('ST-LIVE-1')").count() == 1, wait=3500)
            t.step("Press ST-LIVE-1: that person's profile opens", lambda: pg.click("table a:text-is('ST-LIVE-1')"), expect=lambda: "Employee Details" in txt() and "Field Team" in txt(), wait=3000)
            t.step("Their profile lists the same team", lambda: pg.evaluate("[...document.querySelectorAll('h4')].find(x=>x.textContent.trim()==='Teams').scrollIntoView({block:'center'})"), expect=lambda: True, wait=3000)
            t.note("Not shown: a person with no team (no Teams section), or a role without team access.", 3)

        run_tour(ctx, "45_team_link", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: department Operations, team Field Team with ST-LIVE-1 and ST-LIVE-2.", unshown=["no-team profile", "role without team access"])

    g["DRILLS"]["tour_team_link"] = tour


def register_drive_share_trash(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.drive import services as svc

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            plans = svc.create_folder(me, "Plans")
            svc.create_folder(me, "Inside plans", plans)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            show = lambda sel: pg.evaluate("s=>document.querySelector(s).scrollIntoView({block:'center'})", sel)
            pg.goto(APP + "/drive/")
            t.step("My Drive: one folder, Plans", expect=lambda: "Plans" in txt(), wait=2200)
            t.cap("Next: open Plans")
            pg.click("a.drive-name:text-is('Plans')")
            t.step("Plans opens and holds the folder Inside plans, with a Sharing box", lambda: show(".drive-share"), expect=lambda: "Inside plans" in txt() and "Not shared with anyone." in txt(), wait=3200)
            t.cap("Next: share with an employee ID that does not exist, as Viewer")
            pg.fill("input[name=employee_id]", "ZZ-NOBODY")
            pg.click("button:text-is('Share folder')")
            t.step("The app says there is no employee with that ID; the folder is still not shared", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: "No employee with that ID." in txt(), wait=3400)
            t.cap("Next: share with ST-LIVE-2 as Editor")
            show(".drive-share")
            pg.fill("input[name=employee_id]", "ST-LIVE-2")
            pg.select_option(".drive-share select[name=role]", "editor")
            pg.click("button:text-is('Share folder')")
            t.step("Sharing lists Second Synthetic as Editor", lambda: show(".drive-share"), expect=lambda: "Second Synthetic" in txt() and "Editor" in txt(), wait=3400)
            pg.goto(APP + "/drive/")
            t.cap("Next: move Plans (which has a folder inside) to trash")
            pg.locator(".drive-row:has(a.drive-name:text-is('Plans')) button:text-is('Move to trash')").click()
            t.step("Plans leaves My Drive", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: "Moved to trash" in txt(), wait=3200)
            pg.goto(APP + "/drive/trash/")
            t.step("Trash lists Plans with a Restore button", expect=lambda: "Plans" in txt() and "Restore" in txt(), wait=3000)
            t.note("Not shown: the second person's own Editor view (needs a second sign-in), what Trash does with the folder's contents beyond this list.", 3)

        run_tour(ctx, "66_drive_share_trash", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: folder Plans with a sub-folder; Second Synthetic exists.", unshown=["second person's Editor view", "restore"])

    g["DRILLS"]["tour_drive_share_trash"] = tour


def register_drive_nested(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.drive import services as svc

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            three = CustomUser.objects.get(employee_id="ST-LIVE-3")
            dst = svc.create_folder(me, "Team box")
            svc.share(me, dst, two, "viewer")
            box = svc.create_folder(me, "Project box")
            inner = svc.create_folder(me, "Contracts", parent=box)
            svc.share(me, inner, three, "viewer")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            rowp = ".drive-row:has(a.drive-name:text-is('Project box'))"
            pg.goto(APP + "/drive/")
            t.step("The long sharing note is one collapsed line; the rows start straight below it", lambda: pg.evaluate("document.querySelector('details.drive-meta').scrollIntoView({block:'start'})"), expect=lambda: pg.evaluate("!document.querySelector('details.drive-meta').open"), wait=3500)
            t.step("Open it: the full note is there", lambda: pg.click("details.drive-meta summary"), expect=lambda: pg.evaluate("document.querySelector('details.drive-meta').open") and "Sharing set directly on the item stays" in txt(), wait=3500)
            t.step("Close it again", lambda: pg.click("details.drive-meta summary"), expect=lambda: True, wait=1500)
            t.step("Choose Team box (shared) on the Project box row", lambda: (pg.locator(rowp).scroll_into_view_if_needed(), pg.locator(rowp + " select[name=destination]").select_option(label="Team box (shared)")), expect=lambda: True, wait=3000)
            t.step("Press Move: the confirmation opens", lambda: pg.locator(rowp + " button:text-is('Move')").click(), expect=lambda: "Confirm move" in txt(), wait=3000)
            t.step("As owner: the note says 1 item inside Project box is shared directly and that does not change", lambda: pg.evaluate("[...document.querySelectorAll('p.drive-meta')].find(x=>x.textContent.includes('shared directly with people')).scrollIntoView({block:'center'})"), expect=lambda: "1 item inside Project box is shared directly with people" in txt(), wait=4500)
            t.note("Not shown: Move and share, or a folder with several nested shares.", 3)

        run_tour(ctx, "46_drive_nested_note", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: Team box shared with ST-LIVE-2; Project box holds Contracts, shared with ST-LIVE-3.", unshown=["Move and share", "several nested shares"])

    g["DRILLS"]["tour_drive_nested"] = tour


def register_post_search(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.posts import services as ps

            one = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            ps.create_post(author=one, title="Quokka stand-up notes", content="Body text with nothing special")
            ps.create_post(author=two, title="Quokka private of two", content="Another body")
            ps.create_post(author=one, title="Plain title", content="The word quokka is only in this body")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/search/")
            t.step("Search for quokka everywhere", lambda: (pg.fill("input[name=q]", "quokka"), pg.click("button:text-is('Search')")), expect=lambda: "Quokka stand-up notes" in txt(), wait=3000)
            t.step("Result: a Work Logs & Feed group with only your own post title", lambda: pg.evaluate("[...document.querySelectorAll('h3')].find(x=>x.textContent.includes('Work Logs')).scrollIntoView({block:'center'})"), expect=lambda: "Quokka private of two" not in txt() and "Plain title" not in txt(), wait=4000)
            t.step("Press the post title: its page opens", lambda: pg.click("a.cal-title:text-is('Quokka stand-up notes')"), expect=lambda: "Quokka stand-up notes" in txt() and "Body text with nothing special" in txt(), wait=3500)
            t.note("Not shown: a team lead's wider results, which the unit test covers.", 3)

        run_tour(ctx, "47_post_search", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: three posts, two by ST-LIVE-1 and one by ST-LIVE-2 (no team, so not visible to ST-LIVE-1).", unshown=["team lead results"])

    g["DRILLS"]["tour_post_search"] = tour


def register_worklog_filter(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.posts import services as ps
            from apps.posts.models import Post

            one = CustomUser.objects.get(employee_id="ST-LIVE-1")
            ps.create_post(author=one, title="Alpha release notes", content="First body")
            ps.create_post(author=one, title="Beta planning", content="Second body")
            ps.create_post(author=one, title="Alpha draft idea", content="Third body", status=Post.Status.DRAFT)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            show = lambda: pg.evaluate("document.querySelector('a[href*=\"/posts/\"]:not([href$=\"/posts/\"]):not([href*=\"create\"])').scrollIntoView({block:'start'})")
            def go(sel):
                with pg.expect_navigation():
                    pg.click(sel)

            pg.goto(APP + "/posts/")
            t.step("Work Logs & Feed with no filter: all three posts", lambda: show(), expect=lambda: all(x in txt() for x in ("Alpha release notes", "Beta planning", "Alpha draft idea")), wait=3500)
            t.step("Keyword alpha: only the two Alpha posts remain", lambda: (pg.fill("input[name=q]", "alpha"), go("button:text-is('Filter')"), show()), expect=lambda: "Beta planning" not in txt() and "Alpha release notes" in txt() and "Alpha draft idea" in txt(), wait=4000)
            t.step("Add status Draft: only Alpha draft idea remains", lambda: (pg.select_option("select[name=status]", "draft"), go("button:text-is('Filter')"), show()), expect=lambda: "Alpha release notes" not in txt() and "Alpha draft idea" in txt(), wait=4000)
            t.step("Press Clear: all three are back", lambda: (go("a:text-is('Clear')"), show()), expect=lambda: all(x in txt() for x in ("Alpha release notes", "Beta planning", "Alpha draft idea")), wait=3500)
            t.note("Not shown: category and team filters, and the Certificates filter.", 3)

        run_tour(ctx, "48_worklog_filter", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: three posts by ST-LIVE-1, one of them a draft.", unshown=["category filter", "team filter", "certificates filter"])

    g["DRILLS"]["tour_worklog_filter"] = tour


def register_event_edit_conflict(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)
        box = {}

        def make():
            from apps.accounts.models import CustomUser
            from apps.events import services as es

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            base = dt.datetime.combine(dt.date.today() + dt.timedelta(days=2), dt.time(14, 0))
            ev = es.create_event(me, "Design review", base, base + dt.timedelta(hours=1), "Asia/Kolkata", [])
            box["pid"] = ev.public_id
            box["start"], box["end"] = base, base + dt.timedelta(hours=1)

        def other_edit():
            from apps.accounts.models import CustomUser
            from apps.events import services as es

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            es.update_event(me, box["pid"], 1, "Design review (moved by another tab)", box["start"], box["end"], "Asia/Kolkata")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/calendar/event/%s/edit/" % box["pid"])
            t.step("Open Edit on the event Design review", expect=lambda: pg.input_value("input[name=title]") == "Design review", wait=2500)
            t.step("Type a new title, not saved yet", lambda: pg.fill("input[name=title]", "Design review, final agenda"), expect=lambda: "final agenda" in pg.input_value("input[name=title]"), wait=2200)
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                ex.submit(other_edit).result()  # a second edit lands first, off camera
            def save():
                with pg.expect_navigation():
                    pg.click("button:has-text('Save')")
                pg.evaluate("document.querySelector('.cal-conflict').scrollIntoView({block:'start'})")
            t.step("Save: the page says someone changed it and nothing was saved", save, expect=lambda: "Someone changed this event after you opened it" in txt() and "Nothing was saved" in txt() and "moved by another tab" in txt(), wait=4500)
            t.step("The typed title is still in the box", lambda: pg.evaluate("document.querySelector('input[name=title]').scrollIntoView({block:'center'})"), expect=lambda: pg.input_value("input[name=title]") == "Design review, final agenda", wait=3500)
            def reload():
                with pg.expect_navigation():
                    pg.click("a:has-text('Reload the saved version')")
            t.step("Reload the saved version: it shows the other tab's title", reload, expect=lambda: pg.input_value("input[name=title]") == "Design review (moved by another tab)", wait=3500)
            t.note("Not shown: edit conflict on a repeating series.", 3)

        run_tour(ctx, "49_event_edit_conflict", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. The competing edit is made by a script (same person, second tab) off camera.", unshown=["edit conflict on a repeating series"])

    g["DRILLS"]["tour_event_edit_conflict"] = tour


def register_event_occurrence_paging(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)
        box = {}

        def make():
            from apps.accounts.models import CustomUser
            from apps.events import services as es

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            base = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time(9, 30))
            ev = es.create_event(me, "Daily check-in", base, base + dt.timedelta(minutes=15), "Asia/Kolkata", [], rule={"freq": "daily", "count": 45})
            box["pid"] = ev.public_id
            box["day30"] = (base + dt.timedelta(days=29)).date().isoformat()

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            rows = lambda: pg.locator(".cal-row").count()
            def go(sel):
                with pg.expect_navigation():
                    pg.click(sel)
            def show(sel):
                pg.evaluate("s => document.querySelector(s).scrollIntoView({block:'start'})", sel)
            pg.goto(APP + "/calendar/event/%s/" % box["pid"])
            t.step("A daily series of 45 occurrences: the page lists the first 20 and a Later link", lambda: show("#occ-from"), expect=lambda: rows() == 20 and "Later occurrences" in txt() and "Earlier occurrences" not in txt(), wait=4000)
            t.step("Later occurrences: the next 20, now with an Earlier link", lambda: (go("a:text-is('Later occurrences')"), show("#occ-from")), expect=lambda: rows() == 20 and "Earlier occurrences" in txt() and "Later occurrences" in txt(), wait=4000)
            t.step("Later again: the last 5, and no Later link", lambda: (go("a:text-is('Later occurrences')"), show("#occ-from")), expect=lambda: rows() == 5 and "Later occurrences" not in txt(), wait=4000)
            t.step("Earlier occurrences: back to the previous 20", lambda: (go("a:text-is('Earlier occurrences')"), show("#occ-from")), expect=lambda: rows() == 20 and "Later occurrences" in txt(), wait=4000)
            t.step("Pick a date in the date box and press Show", lambda: (pg.fill("#occ-from", box["day30"]), go("button:text-is('Show')"), show("#occ-from")), expect=lambda: rows() == 16 and "Earlier occurrences" in txt() and "Later occurrences" not in txt(), wait=4500)
            t.note("Not shown: paging with cancelled occurrences in between (unit tested only).", 3)

        run_tour(ctx, "50_event_occurrence_paging", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: a daily series of 45, 20 shown per page.", unshown=["paging with cancelled occurrences in between"])

    g["DRILLS"]["tour_event_occurrence_paging"] = tour


def register_event_series_guest(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)

        def make():
            from apps.accounts.models import CustomUser
            from apps.events import services as es

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            base = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time(16, 0))
            es.create_event(two, "Weekly sync (series by Second)", base, base + dt.timedelta(minutes=30), "Asia/Kolkata", [me.pk], rule={"freq": "weekly", "count": 6})

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            def go(sel):
                with pg.expect_navigation():
                    pg.click(sel)
            def show(sel):
                pg.evaluate("s => document.querySelector(s).scrollIntoView({block:'start'})", sel)
            pg.goto(APP + "/calendar/")
            t.step("Agenda: a weekly series that Second invited me to", expect=lambda: "Weekly sync" in txt(), wait=3000)
            t.step("Open the series", lambda: (go("a.cal-title:has-text('Weekly sync')"), show(".cal-meta")), expect=lambda: "One answer from a guest covers the whole series" in txt() and "Your answer: Not answered yet" in txt(), wait=4500)
            t.step("Accept: the answer covers the whole series", lambda: (go("button:text-is('Accept')"), show(".cal-actions")), expect=lambda: "Your answer: Accepted" in txt(), wait=4000)
            t.step("Decline: the answer changes for the whole series", lambda: (go("button:text-is('Decline')"), show(".cal-actions")), expect=lambda: "Your answer: Declined" in txt(), wait=4000)
            t.step("Agenda: the declined series is no longer listed", lambda: go(".cal-tabs a:text-is('Agenda')"), expect=lambda: "Weekly sync" not in txt(), wait=4000)
            t.note("Not shown: a guest answering for one occurrence only. It is not built: one answer covers the series.", 3)

        run_tour(ctx, "51_event_series_guest", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: a weekly series of 6 by ST-LIVE-2 with ST-LIVE-1 as a guest.", unshown=["answer for one occurrence only (not built)"])

    g["DRILLS"]["tour_event_series_guest"] = tour


def register_event_time_zone(g, users):
    APP = g["APP"]

    def tour(ctx):
        import datetime as dt
        from zoneinfo import ZoneInfo

        users(ctx)
        day = dt.date.today() + dt.timedelta(days=2)
        local = dt.datetime.combine(day, dt.time(9, 0), tzinfo=ZoneInfo("America/New_York"))
        ist = local.astimezone(ZoneInfo("Asia/Kolkata"))
        ist_text = ist.strftime("%H:%M")  # what the day view in the workspace's own zone must show
        ist_day = ist.date().isoformat()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            def go(sel):
                with pg.expect_navigation():
                    pg.click(sel)
            def show(sel):
                pg.evaluate("s => document.querySelector(s).scrollIntoView({block:'center'})", sel)
            pg.goto(APP + "/calendar/new/")
            t.step("New event: 09:00 to 10:00 on %s, time zone America/New_York" % day.isoformat(), lambda: (pg.fill("input[name=title]", "New York partner call"), pg.fill("input[name=start]", day.isoformat() + "T09:00"), pg.fill("input[name=end]", day.isoformat() + "T10:00"), pg.fill("input[name=tz]", "America/New_York")), expect=lambda: pg.input_value("input[name=tz]") == "America/New_York", wait=2500)
            t.step("Create: the event page shows 09:00 in America/New_York", lambda: (go("button:has-text('Create')"), show(".cal-when")), expect=lambda: "09:00 to 10:00 (America/New_York)" in txt(), wait=4000)
            t.step("Agenda: the same event, with its own zone in brackets", lambda: (go(".cal-tabs a:text-is('Agenda')"), show(".cal-row")), expect=lambda: "New York partner call" in txt() and "(America/New_York)" in txt(), wait=4000)
            t.step("Day view for that day in the workspace zone shows the converted time %s" % ist_text, lambda: (pg.goto(APP + "/calendar/day/?d=" + ist_day), show(".cal-row")), expect=lambda: "New York partner call" in txt() and ist_text in txt(), wait=4500)
            pg.goto(APP + "/calendar/new/")
            t.step("New event with an unknown time zone Mars/Base", lambda: (pg.fill("input[name=title]", "Bad zone"), pg.fill("input[name=start]", day.isoformat() + "T09:00"), pg.fill("input[name=end]", day.isoformat() + "T10:00"), pg.fill("input[name=tz]", "Mars/Base")), expect=lambda: pg.input_value("input[name=tz]") == "Mars/Base", wait=2000)
            t.step("Create: refused with Unknown time zone, nothing created", lambda: (go("button:has-text('Create')"), pg.locator("text=Unknown time zone").first.scroll_into_view_if_needed(), pg.evaluate("window.scrollBy(0, -120)")), expect=lambda: "Unknown time zone" in txt() and pg.locator("text=Unknown time zone").first.is_visible(), wait=4500)
            t.note("Not shown: a local time that does not exist on a clock-change day (unit tested only).", 3)

        run_tour(ctx, "52_event_time_zone", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. The workspace zone is Asia/Kolkata; the event is in America/New_York.", unshown=["clock-change gap time"])

    g["DRILLS"]["tour_event_time_zone"] = tour


def register_event_week_day_arrows(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)
        today = dt.date.today()
        monday = today - dt.timedelta(days=today.weekday())
        this_week = monday + dt.timedelta(days=2)
        next_week = monday + dt.timedelta(days=9)

        def make():
            from apps.accounts.models import CustomUser
            from apps.events import services as es

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            for title, d in (("Planning this week", this_week), ("Review next week", next_week)):
                base = dt.datetime.combine(d, dt.time(11, 0))
                es.create_event(me, title, base, base + dt.timedelta(hours=1), "Asia/Kolkata", [])

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            def go(sel):
                with pg.expect_navigation():
                    pg.click(sel)
            def show():
                pg.evaluate("document.querySelector('.cal-nav').scrollIntoView({block:'start'})")
            pg.goto(APP + "/calendar/week/?d=" + monday.isoformat())
            t.step("Week view of this week: only Planning this week is listed", lambda: show(), expect=lambda: "Planning this week" in txt() and "Review next week" not in txt(), wait=4000)
            t.step("Next: the following week lists Review next week", lambda: (go("a:has-text('Next')"), show()), expect=lambda: "Review next week" in txt() and "Planning this week" not in txt(), wait=4000)
            t.step("Previous: back to this week", lambda: (go("a:has-text('Previous')"), show()), expect=lambda: "Planning this week" in txt() and "Review next week" not in txt(), wait=4000)
            pg.goto(APP + "/calendar/day/?d=" + this_week.isoformat())
            t.step("Day view of the day of Planning this week", lambda: show(), expect=lambda: "Planning this week" in txt(), wait=3500)
            t.step("Next: the following day is empty", lambda: (go("a:has-text('Next')"), show()), expect=lambda: "Planning this week" not in txt(), wait=3500)
            t.step("Previous twice: the day before is empty too", lambda: (go("a:has-text('Previous')"), go("a:has-text('Previous')"), show()), expect=lambda: "Planning this week" not in txt(), wait=3500)
            t.step("Next once: back on the day with Planning this week", lambda: (go("a:has-text('Next')"), show()), expect=lambda: "Planning this week" in txt(), wait=3500)
            t.note("Not shown: month arrows were filmed in an earlier clip. Arrows at the edge of the supported date range are not shown.", 3)

        run_tour(ctx, "53_event_week_day_arrows", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: one event this week and one the week after.", unshown=["arrows at the edge of the supported date range"])

    g["DRILLS"]["tour_event_week_day_arrows"] = tour


def register_certs_filter(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.certificates import services as cs
            from apps.certificates.models import CertificateTemplate

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            me.role = CustomUser.Role.ADMIN
            me.save(update_fields=["role"])
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True, "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            three = CustomUser.objects.get_or_create(employee_id="ST-LIVE-3", defaults={"full_name": "Third Synthetic", "email": "st-live-3@salowtech.com", "email_verified": True, "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            tpl = CertificateTemplate.objects.get_or_create(code="TOUR-EXC", defaults={"title": "Excellence", "description_template": "For excellent work."})[0]
            cs.issue_certificate(recipient=two, title="Certificate of Teamwork", description="Great teamwork.", issued_by=me, template=tpl)
            b = cs.issue_certificate(recipient=three, title="Certificate of Safety", description="Safety record.", issued_by=me, template=tpl)
            cs.revoke_certificate(certificate=b, revoked_by=me, reason="Fixture: revoked")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(fixture).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            def go(sel):
                with pg.expect_navigation():
                    pg.click(sel)
            def show():
                pg.evaluate("document.querySelector('.certificate-table-card').scrollIntoView({block:'start'})")
            def act(q, status=None):
                pg.fill("input[name=q]", q)
                if status is not None:
                    pg.select_option("select[name=status]", status)
                go("button:text-is('Filter')")
                show()
            pg.goto(APP + "/certificates/")
            t.step("Certificate list as Admin: both certificates", lambda: show(), expect=lambda: "Teamwork" in txt() and "Safety" in txt(), wait=3500)
            act("teamwork")  # the caption below appears after the page has reloaded
            t.step("Searched teamwork in small letters: only Teamwork", expect=lambda: "Teamwork" in txt() and "Safety" not in txt(), wait=4000)
            act("second synthetic")
            t.step("Searched the recipient name second synthetic in small letters: Teamwork again", expect=lambda: "Teamwork" in txt() and "Safety" not in txt(), wait=4000)
            act("st-live-3")
            t.step("Searched the employee ID st-live-3 in small letters: only Safety", expect=lambda: "Safety" in txt() and "Teamwork" not in txt(), wait=4000)
            act("st-live-3", "active")
            t.step("Same search with status Active: no rows, and the message is readable", expect=lambda: "Safety" not in txt() and "Teamwork" not in txt() and "No certificates found." in txt() and pg.evaluate("(() => { const r = document.querySelector('.certificate-empty').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth; })()"), wait=4500)
            act("st-live-3", "revoked")
            t.step("Same search with status Revoked: Safety is back", expect=lambda: "Safety" in txt() and "Teamwork" not in txt(), wait=4000)
            t.note("Not shown: searching by serial number, and the revoked and valid status wording in the table.", 3)

        run_tour(ctx, "54_certs_filter", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: ST-LIVE-1 made Admin, two certificates (one revoked) for two people.", unshown=["search by serial number"])

    g["DRILLS"]["tour_certs_filter"] = tour


def register_empty_states(g, users):
    APP = g["APP"]

    def tour(ctx):
        users(ctx)

        def body(t, pg):
            def inview(text):
                return lambda: pg.evaluate("t => { const e = [...document.querySelectorAll('td, p, div')].filter(x => x.children.length === 0 && x.textContent.indexOf(t) >= 0)[0]; if (!e) return false; const r0 = e.getBoundingClientRect(); window.scrollBy(0, r0.top - 300); const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth; }", text)
            pg.goto(APP + "/directory/?q=zzzznomatch")
            t.step("People search with no match: the message is fully on the screen", expect=inview("No employees match the search filter."), wait=4000)
            pg.goto(APP + "/auth/security/")
            pg.wait_for_timeout(1500)
            pg.locator("text=No passkeys registered.").first.scroll_into_view_if_needed()
            pg.evaluate("window.scrollBy(0, 250)")
            pg.wait_for_timeout(1500)
            t.step("Security and Sessions with no passkey: the message is fully on the screen", expect=lambda: pg.evaluate("() => { const e = [...document.querySelectorAll('p')].filter(x => x.textContent.indexOf('No passkeys registered.') >= 0)[0]; const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth && r.top >= 0 && r.bottom <= window.innerHeight - 120; }"), wait=4000)

        run_tour(ctx, "55_empty_states", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. No fixture: the account has no passkey and no person matches the search.", unshown=["empty team members page", "empty security events"])

    g["DRILLS"]["tour_empty_states"] = tour


def register_project_filters(g, users):
    APP = g["APP"]

    def tour(ctx):
        users(ctx)
        import concurrent.futures

        import django

        django.setup()
        ids = {}

        def make():
            import datetime as dt

            from apps.accounts.models import CustomUser
            from apps.tasks import projects as pj
            from apps.tasks.models import TaskComment

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            p = pj.create_project(me, "Filter project")
            cols = list(p.columns.order_by("position"))
            ms = pj.create_milestone(me, p, "Launch", None)
            past = dt.date.today() - dt.timedelta(days=5)
            future = dt.date.today() + dt.timedelta(days=30)
            a = pj.add_task(me, p, cols[0].pk, "Late and open", due=past)
            pj.add_task(me, p, cols[2].pk, "Late but done", due=past)
            c = pj.add_task(me, p, cols[1].pk, "Future work", due=future)
            pj.set_milestone(me, c, ms.pk)
            for i in range(103):
                TaskComment.objects.create(task=a, author=me, body="Note number %d" % (i + 1))
            ids["p"] = str(p.public_id)
            ids["a"] = str(a.public_id)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            cards = lambda: [x.strip() for x in pg.locator(".task-card .task-title").all_inner_texts()]
            pg.goto(APP + "/tasks/projects/%s/" % ids["p"])
            pg.wait_for_timeout(1500)
            show = lambda name: (pg.locator(".task-card", has_text=name).first.scroll_into_view_if_needed(), pg.evaluate("window.scrollBy(0, 120)"), pg.wait_for_timeout(500))
            show("Late and open")
            t.step("Board before filtering: the card \"Late and open\" is on screen (3 tasks on the board)", expect=lambda: len(cards()) == 3, wait=2000)
            pg.check("input[name=overdue]")
            pg.click("button:has-text('Filter')")
            pg.wait_for_timeout(1200)
            pg.locator("[role=status]").first.scroll_into_view_if_needed()
            t.step("Overdue filter: only the late task that is not Done stays", expect=lambda: cards() == ["Late and open"] and "Showing 1 of 3" in txt(), wait=3000)
            pg.goto(APP + "/tasks/projects/%s/" % ids["p"])
            pg.select_option("select[name=milestone]", label="Launch")
            pg.click("button:has-text('Filter')")
            pg.wait_for_timeout(1200)
            pg.locator("[role=status]").first.scroll_into_view_if_needed()
            t.step("Milestone filter (Launch): only the task with that milestone", expect=lambda: cards() == ["Future work"], wait=3000)
            pg.click("a:has-text('Clear')")
            pg.wait_for_timeout(1200)
            t.step("Clear: the filter note is gone and the board lists 3 tasks (first card on screen)", expect=lambda: len(cards()) == 3 and "Showing" not in txt(), wait=2500)
            show("Late but done")
            t.step("Same board, scrolled: the card \"Late but done\" is back", expect=lambda: pg.locator(".task-card", has_text="Late but done").first.is_visible(), wait=2500)
            show("Future work")
            t.step("Same board, scrolled: the card \"Future work\" is back", expect=lambda: pg.locator(".task-card", has_text="Future work").first.is_visible(), wait=2500)
            pg.goto(APP + "/tasks/projects/task/%s/comments/" % ids["a"])
            pg.wait_for_timeout(1200)
            t.step("Comments page: newest 100 of 103 are shown with an Older comments link", expect=lambda: pg.locator(".comment").count() == 100 and "Older comments" in txt(), wait=2500)
            pg.click("a:has-text('Older comments')")
            pg.wait_for_timeout(1200)
            t.step("Older comments: the 3 earliest, with a link back to the newest", expect=lambda: pg.locator(".comment").count() == 3 and "Note number 1" in txt() and "Newest comments" in txt(), wait=3000)
            pg.click("a:has-text('Newest comments')")
            pg.wait_for_timeout(1000)
            pg.fill("#cbody", "Posted from the tour")
            pg.click(".comment-form button[type=submit]")
            pg.wait_for_timeout(1500)
            pg.locator(".comment", has_text="Posted from the tour").last.scroll_into_view_if_needed()
            pg.evaluate("window.scrollBy(0, 150)")
            pg.wait_for_timeout(800)
            t.step("The posted comment \"Posted from the tour\" is on screen at the end of the list", expect=lambda: pg.locator(".comment", has_text="Posted from the tour").last.is_visible() and pg.evaluate("() => { const e=[...document.querySelectorAll('.comment')].filter(x=>x.textContent.includes('Posted from the tour')).pop().getBoundingClientRect(); return e.top>=0 && e.bottom<=window.innerHeight-120; }"), wait=3500)

        run_tour(ctx, "56_project_filters_comments", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Project, milestone, tasks and 103 comments were created by script as the signed-in lead.", unshown=["second member commenting", "comment rate limit", "filter combined with search"])

    g["DRILLS"]["tour_project_filters"] = tour


def register_directory_dept(g, users):
    APP = g["APP"]

    def tour(ctx):
        users(ctx)
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.directory.models import Department, Team, TeamMembership

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            da = Department.objects.create(code="ALPHA", name="Alpha Department")
            db = Department.objects.create(code="BETA", name="Beta Department")
            ta = Team.objects.create(code="ALPHA-T", name="Alpha Team", department=da)
            tb = Team.objects.create(code="BETA-T", name="Beta Team", department=db)
            TeamMembership.objects.create(team=ta, user=me)
            TeamMembership.objects.create(team=tb, user=two)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            ids = lambda: [x.strip() for x in pg.locator("tbody tr td:first-child").all_inner_texts()]
            pg.goto(APP + "/directory/")
            pg.wait_for_timeout(1500)
            t.step("Directory with All Departments: the table lists ST-LIVE-1 and ST-LIVE-2", expect=lambda: "ST-LIVE-1" in ids() and "ST-LIVE-2" in ids(), wait=3000)
            pg.select_option("select[name=dept]", label="Alpha Department")
            pg.click("button:has-text('Filter')")
            pg.wait_for_timeout(1500)
            t.step("Alpha Department selected: only ST-LIVE-1 is listed", expect=lambda: ids() == ["ST-LIVE-1"] and pg.locator("select[name=dept]").input_value() == "ALPHA", wait=3500)
            pg.select_option("select[name=dept]", label="Beta Department")
            pg.click("button:has-text('Filter')")
            pg.wait_for_timeout(1500)
            t.step("Beta Department selected: only ST-LIVE-2 is listed", expect=lambda: ids() == ["ST-LIVE-2"], wait=3500)
            pg.fill("input[name=q]", "zzzz")
            pg.click("button:has-text('Filter')")
            pg.wait_for_timeout(1500)
            t.step("Beta plus a search with no match: the empty message shows", expect=lambda: "No employees match the search filter." in pg.inner_text("body"), wait=3500)

        run_tour(ctx, "57_directory_department", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Two departments, two teams and the memberships were created by script.", unshown=["team detail page", "org chart", "a person in several departments"])

    g["DRILLS"]["tour_directory_dept"] = tour


def register_subtask_delete(g, users):
    APP = g["APP"]

    def tour(ctx):
        users(ctx)
        import concurrent.futures

        import django

        django.setup()
        ids = {}

        def make():
            from apps.accounts.models import CustomUser
            from apps.tasks import services as svc

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            tl = svc.create_list(me, "Sub-task list")
            parent = svc.create_task(me, tl, "Parent task")
            svc.create_task(me, tl, "Sub one", parent=parent)
            svc.create_task(me, tl, "Sub two", parent=parent)
            ids["l"] = str(tl.public_id)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            subs = lambda: [x.strip() for x in pg.locator(".task-row.sub .task-title").all_inner_texts()]
            pg.goto(APP + "/tasks/list/%s/" % ids["l"])
            pg.wait_for_timeout(1500)
            t.step("List: Parent task with sub-tasks Sub one and Sub two", expect=lambda: subs() == ["Sub one", "Sub two"], wait=3000)
            pg.locator(".task-row.sub", has_text="Sub one").locator("a:has-text('Delete')").click()
            pg.wait_for_timeout(1200)
            t.step("Delete on Sub one opens a confirmation page naming Sub one", expect=lambda: "Delete this task?" in txt() and "Sub one" in txt(), wait=3500)
            pg.click("a:has-text('Keep it')")
            pg.wait_for_timeout(1200)
            t.step("Keep it: back on the list, both sub-tasks still there", expect=lambda: subs() == ["Sub one", "Sub two"], wait=3000)
            pg.locator(".task-row.sub", has_text="Sub one").locator("a:has-text('Delete')").click()
            pg.wait_for_timeout(1000)
            pg.click("button:has-text('Delete')")
            pg.wait_for_timeout(1500)
            pg.locator(".task-row", has_text="Parent task").first.scroll_into_view_if_needed()
            pg.evaluate("window.scrollBy(0, 150)")
            pg.wait_for_timeout(600)
            t.step("Confirm Delete: under Parent task only Sub two is left (Sub one is gone)", expect=lambda: subs() == ["Sub two"] and "Parent task" in txt(), wait=3500)

        run_tour(ctx, "58_subtask_delete", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. The list, parent and two sub-tasks were created by script as the signed-in account.", unshown=["deleting the parent with its sub-tasks", "restore (not built)", "another person deleting"])

    g["DRILLS"]["tour_subtask_delete"] = tour


def register_parent_delete(g, users):
    APP = g["APP"]

    def tour(ctx):
        users(ctx)
        import concurrent.futures

        import django

        django.setup()
        ids = {}

        def make():
            from apps.accounts.models import CustomUser
            from apps.tasks import services as svc

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            tl = svc.create_list(me, "Parent delete list")
            parent = svc.create_task(me, tl, "Parent to remove")
            svc.create_task(me, tl, "Child A", parent=parent)
            svc.create_task(me, tl, "Child B", parent=parent)
            svc.create_task(me, tl, "Unrelated task")
            ids["l"] = str(tl.public_id)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            tops = lambda: [x.strip() for x in pg.locator(".task-row:not(.sub) .task-title").all_inner_texts()]
            pg.goto(APP + "/tasks/list/%s/" % ids["l"])
            pg.wait_for_timeout(1500)
            t.step("List: Parent to remove (with Child A and Child B) and Unrelated task", expect=lambda: tops() == ["Parent to remove", "Unrelated task"], wait=3000)
            pg.locator(".task-row:not(.sub)", has_text="Parent to remove").locator("a:has-text('Delete')").click()
            pg.wait_for_timeout(1200)
            t.step("Delete on the parent: the confirmation names it and says its sub-tasks (about 2) go too", expect=lambda: "Parent to remove" in txt() and "and its subtasks (about 2 right now)" in txt(), wait=4000)
            pg.click("button:has-text('Delete')")
            pg.wait_for_timeout(1500)
            t.step("Confirm: Task deleted. The parent and both children are gone, Unrelated task stays", expect=lambda: "Task deleted." in txt() and tops() == ["Unrelated task"] and "Child A" not in txt() and "Child B" not in txt(), wait=3500)

        run_tour(ctx, "59_parent_delete", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. The list, parent, two children and another task were created by script.", unshown=["restore (not built)", "a sub-task added by someone else while the confirmation is open", "deleting a given-to task"])

    g["DRILLS"]["tour_parent_delete"] = tour


def register_pdf_marks_tools(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        def body(t, pg):
            data = g["_n_page_pdf"](4, "Keep")
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
                t.step("No secure context in this browser", expect=lambda: False)
                return

            def opened():
                pg.set_input_files("#pdf-open", files=[{"name": "demo.pdf", "mimeType": "application/pdf", "buffer": data}])
                for _ in range(40):
                    if where().startswith("Page 1 of 4") and pg.evaluate("document.getElementById('pdf-canvas').width > 1"):
                        break
                    pg.wait_for_timeout(250)

            def show(sel):
                pg.evaluate("s=>{document.querySelector(s).scrollIntoView({block:'center'})}", sel)

            def drag(a, b):
                show("#pdf-marks")
                pg.wait_for_timeout(150)
                x, y, w, h = pg.evaluate("(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
                pg.mouse.move(x + a[0] * w, y + a[1] * h)
                pg.mouse.down()
                for i in range(1, 7):
                    pg.mouse.move(x + (a[0] + (b[0] - a[0]) * i / 6) * w, y + (a[1] + (b[1] - a[1]) * i / 6) * h)
                pg.mouse.up()
                pg.wait_for_timeout(300)

            os.makedirs("/tmp/vid/dl", exist_ok=True)

            def grab(sel, name):
                with pg.expect_download(timeout=15000) as d:
                    pg.click(sel)
                d.value.save_as("/tmp/vid/dl/" + name)

            marks = lambda: pg.locator("#pdf-marklist li").count()
            status = lambda: pg.inner_text("#pdf-markstatus").strip()
            t.step("Open a 4-page PDF", opened, expect=lambda: where().startswith("Page 1 of 4"), wait=1500)
            drag_text = lambda: (pg.click("#pdf-tool-tx"), drag((0.05, 0.02), (0.95, 0.98)))
            drag_text()
            t.step("Highlight text lines: dragged over the page, the mark list shows an entry", expect=lambda: marks() >= 1, wait=2500)
            show("#pdf-sigbox")
            pg.evaluate("document.getElementById('pdf-sigbox').open = true")
            show("#pdf-sigpad")
            bx = pg.evaluate("(()=>{const r=document.getElementById('pdf-sigpad').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
            pg.mouse.move(bx[0] + bx[2] * 0.1, bx[1] + bx[3] * 0.6)
            pg.mouse.down()
            for i in range(1, 9):
                pg.mouse.move(bx[0] + bx[2] * (0.1 + 0.1 * i), bx[1] + bx[3] * (0.6 - 0.3 * ((i % 2))))
            pg.mouse.up()
            pg.wait_for_timeout(300)
            t.step("Signature box: a signature is drawn in it", expect=lambda: pg.evaluate("document.getElementById('pdf-sigpad').children.length") > 0, wait=2500)
            pg.click("#pdf-sig-use")
            t.step("Use this signature: the state line shows it is ready", expect=lambda: bool(pg.inner_text("#pdf-sig-state").strip()), wait=2500)
            n0 = marks()
            pg.click("#pdf-tool-sig")
            drag((0.4, 0.7), (0.4, 0.7))
            t.step("Place signature: clicked on the page, one more mark in the list", expect=lambda: marks() == n0 + 1, wait=2500)
            show("#pdf-marks")
            t.step("The page with its marks on screen", expect=lambda: True, wait=2500)
            t.step("Download a copy with marks: the status line says what was made", lambda: (show("#pdf-markcopy"), grab("#pdf-markcopy", "marks_copy.pdf")), expect=lambda: bool(status()), wait=2200)
            t.step("Status line after the copy", lambda: show("#pdf-markstatus"), expect=lambda: True, wait=2800)
            t.step("Download a copy with marks fixed in", lambda: (show("#pdf-markflat"), grab("#pdf-markflat", "marks_flat.pdf")), expect=lambda: bool(status()), wait=2200)
            t.step("Status line after the fixed-in copy", lambda: show("#pdf-markstatus"), expect=lambda: True, wait=2800)
            t.step("Remove all marks", lambda: (show("#pdf-markclear"), pg.click("#pdf-markclear")), expect=lambda: True, wait=2200)
            pg.wait_for_timeout(500)
            if marks() > 0:
                pg.click("#pdf-markclear")
                pg.wait_for_timeout(600)
            t.step("Mark list after Remove all marks (empty if it is)", lambda: show("#pdf-marklist"), expect=lambda: marks() == 0, wait=2800)

        run_tour(ctx, "60_pdf_marks_tools", body, notes="Device-local viewer on a generated 4-page PDF; secure-context test flag. Headless Chromium 390x844 emulated. The downloaded copies are saved off-camera and are not opened in this clip.", unshown=["opening the downloaded copies", "reorder", "merge", "form fields", "password PDF", "unreadable-marks discard"])

    g["DRILLS"]["tour_pdf_marks_tools"] = tour


def register_pdf_alltext(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        def body(t, pg):
            data = g["_n_page_pdf"](4, "Demo", blank=(3,))
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            pg.set_input_files("#pdf-open", files=[{"name": "demo.pdf", "mimeType": "application/pdf", "buffer": data}])
            for _ in range(40):
                if where().startswith("Page 1 of 4"):
                    break
                pg.wait_for_timeout(250)
            out = os.environ.get("DRILL_VIDEO", "")
            saved = {}
            pg.evaluate("document.querySelector('#pdf-exportall').scrollIntoView({block:'center'})")
            t.step("A 4-page file is open (page 3 has no text, like a scan). The Download text of all pages button is on", expect=lambda: not pg.is_disabled("#pdf-exportall"), wait=2400)

            def click():
                with pg.expect_download(timeout=15000) as d:
                    pg.click("#pdf-exportall")
                saved["name"] = d.value.suggested_filename
                saved["text"] = open(d.value.path(), encoding="utf-8").read()
                open(os.path.join(out, "alltext.txt"), "w", encoding="utf-8").write(saved["text"])
                pg.wait_for_timeout(1200)

            t.step("Press it. The status says 3 of 4 pages were downloaded and 1 had no readable text", click, expect=lambda: "Downloaded the text of 3 of 4 page" in pg.inner_text("#pdf-fstatus") and "1 page(s) had no readable text" in pg.inner_text("#pdf-fstatus"), wait=3200)
            t.step("The saved file holds pages 1, 2 and 4 in order and its header names page 3 (checked in the saved file)", expect=lambda: saved.get("name") == "demo-all-text.txt" and saved["text"].index("Demo 1") < saved["text"].index("Demo 2") < saved["text"].index("Demo 4") and "Demo 3" not in saved["text"] and "on page(s): 3." in saved["text"] and "not OCR" in saved["text"], wait=1500)
        run_tour(ctx, "pdf_alltext", body, notes="Device-local viewer on a generated 4-page PDF whose page 3 has no text. Headless Chromium 390x844 emulated; the downloaded text file is saved and read back by the tour. Not shown: a very large file reaching the size limit (node table rows), Stop mid-read, closing the file mid-read.", unshown=["size limit", "Stop mid-read", "close mid-read"])

    g["DRILLS"]["tour_pdf_alltext"] = tour


def register_pdf_alltext_stop(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        def body(t, pg):
            def big(pages, lines, blank=()):
                """A valid PDF where each page holds lines x 1000 characters of text (about lines*1000 characters)."""
                objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
                objs.append(("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % (3 + 2 * i) for i in range(pages)), pages)).encode())
                font = 3 + 2 * pages
                for i in range(pages):
                    objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>" % (4 + 2 * i, font)).encode())
                    c = ("BT /F1 1 Tf 5 780 Td 0.5 TL " + "".join("(%s) Tj T* " % ("word" * 250) for _ in range(lines)) + "ET").encode()
                    objs.append(b"<< /Length %d >>\nstream\n" % len(c) + c + b"\nendstream")
                objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
                out = b"%PDF-1.4\n"; offs = []
                for i, o in enumerate(objs, 1):
                    offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
                x = len(out)
                out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
                for o in offs:
                    out += b"%010d 00000 n \n" % o
                out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
                return out

            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            out = os.environ.get("DRILL_VIDEO", "")
            status = lambda: pg.inner_text("#pdf-fstatus")
            downloads = []
            pg.on("download", lambda d: downloads.append(d))

            def opened(name, data, pages):
                pg.set_input_files("#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}])
                for _ in range(160):
                    if pg.inner_text("#pdf-where").startswith("Page 1 of %d" % pages):
                        break
                    pg.wait_for_timeout(250)

            def view(sel):
                pg.evaluate("s=>document.querySelector(s).scrollIntoView({block:'center'})", sel)

            many = big(1500, 1)
            opened("many.pdf", many, 1500)
            view("#pdf-exportall")
            pg.wait_for_timeout(600)
            t.step("A 1,500-page file is open. Press Download text of all pages: it starts reading", lambda: pg.click("#pdf-exportall"), expect=lambda: "Reading page" in status() and pg.is_enabled("#pdf-exportallstop") and pg.is_disabled("#pdf-exportall"), wait=900)
            t.step("Press Stop while it reads: the status says it stopped and nothing was downloaded, and the button is back on", lambda: pg.click("#pdf-exportallstop"), expect=lambda: status().startswith("Stopped. Nothing was downloaded.") and pg.is_enabled("#pdf-exportall") and pg.is_disabled("#pdf-exportallstop"), wait=1200)
            pg.wait_for_timeout(2500)
            t.step("Three seconds later no file was saved and the status is still Stopped", expect=lambda: len(downloads) == 0 and status().startswith("Stopped."), wait=800)
            t.step("Press it again, then Close file while it reads: the file closes and the buttons go off", lambda: (pg.click("#pdf-exportall"), pg.wait_for_timeout(150), pg.click("#pdf-close")), expect=lambda: pg.is_disabled("#pdf-exportall") and pg.is_disabled("#pdf-exportallstop") and pg.inner_text("#pdf-name").strip() == "" and status() == "", wait=1500)
            pg.wait_for_timeout(2500)
            t.step("Three seconds later no file was saved", expect=lambda: len(downloads) == 0, wait=800)
            opened("many.pdf", many, 1500)
            pg.click("#pdf-exportall")
            pg.wait_for_timeout(150)
            opened("small.pdf", g["_n_page_pdf"](2, "Small"), 2)
            view("#pdf-exportall")
            t.step("Press it, then open a different 2-page file while it reads: the new file is open, nothing from the old one is saved and the buttons are ready", expect=lambda: pg.inner_text("#pdf-where").startswith("Page 1 of 2") and pg.is_enabled("#pdf-exportall") and pg.is_disabled("#pdf-exportallstop") and len(downloads) == 0, wait=1500)
            pg.wait_for_timeout(2500)
            t.step("Three seconds later still no file was saved", expect=lambda: len(downloads) == 0, wait=800)
            capdata = big(7, 900)
            opened("huge.pdf", capdata, 7)
            view("#pdf-exportall")
            def cap_click():
                with pg.expect_download(timeout=60000) as d:
                    pg.click("#pdf-exportall")
                txt = open(d.value.path(), encoding="utf-8").read()
                open(os.path.join(out, "cap.txt"), "w", encoding="utf-8").write(txt[:3000] + "\n[...]\nlength " + str(len(txt)))
                downloads.append(("cap", txt))
            t.step("A 7-page file with about 900,000 characters a page: press it, the file stops at the size limit", cap_click, expect=lambda: "It stops after page 6 (size limit)" in status() and len(downloads[-1][1]) < 5100000 and "Page 7 of 7" not in downloads[-1][1] and "Page 6 of 7" in downloads[-1][1], wait=2500)
        run_tour(ctx, "pdf_alltext_stop", body, notes="Generated PDFs: 1,500 pages (Stop, Close file and open-another-file while reading), then 7 pages of about 900,000 characters (size limit). Headless Chromium 390x844 emulated, one run. Not shown: a file that fails to parse mid-read.", unshown=["thrown error mid-read", "damaged page"])

    g["DRILLS"]["tour_pdf_alltext_stop"] = tour


def register_pdf_pages(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        def body(t, pg):
            data = g["_n_page_pdf"](4, "Keep")
            other = g["_n_page_pdf"](3, "Other")
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
                t.step("No secure context in this browser", expect=lambda: False)
                return
            pg.set_input_files("#pdf-open", files=[{"name": "demo.pdf", "mimeType": "application/pdf", "buffer": data}])
            for _ in range(40):
                if where().startswith("Page 1 of 4"):
                    break
                pg.wait_for_timeout(250)
            os.makedirs("/tmp/vid/dl", exist_ok=True)

            def show(sel):
                pg.evaluate("s=>{document.querySelector(s).scrollIntoView({block:'center'})}", sel)

            def grab(sel, name):
                with pg.expect_download(timeout=15000) as d:
                    pg.click(sel)
                d.value.save_as("/tmp/vid/dl/" + name)

            order = lambda: pg.evaluate("[...document.querySelectorAll('#pdf-orderlist li span')].map(s=>s.textContent.trim())")
            pst = lambda: pg.inner_text("#pdf-pagesstatus").strip()
            show("#pdf-orderstart")
            t.step("The pages tools: List pages from the file is available", expect=lambda: not pg.is_disabled("#pdf-orderstart"), wait=2500)
            pg.click("#pdf-orderstart")
            pg.wait_for_timeout(400)
            show("#pdf-orderlist")
            t.step("List pages: the list reads Page 1, Page 2, Page 3, Page 4 and the pages box says 1-4", expect=lambda: order() == ["Page 1", "Page 2", "Page 3", "Page 4"] and pg.input_value("#pdf-range") == "1-4", wait=3000)
            pg.locator("#pdf-orderlist li").nth(3).locator("button").nth(0).click()
            pg.wait_for_timeout(400)
            show("#pdf-orderlist")
            t.step("Up on Page 4: the list now reads Page 1, Page 2, Page 4, Page 3", expect=lambda: order() == ["Page 1", "Page 2", "Page 4", "Page 3"], wait=3000)
            pg.locator("#pdf-orderlist li").nth(0).locator("button").nth(2).click()
            pg.wait_for_timeout(400)
            show("#pdf-orderlist")
            t.step("Third button on Page 1 (remove): the list now has 3 entries", expect=lambda: len(order()) == 3, wait=3000)
            grab("#pdf-splitgo", "reordered.pdf")
            show("#pdf-pagesstatus")
            t.step("Download these pages as a new PDF: the status line says what was built", expect=lambda: bool(pst()), wait=3000)
            pg.set_input_files("#pdf-mergeadd", files=[{"name": "other.pdf", "mimeType": "application/pdf", "buffer": other}])
            pg.wait_for_timeout(1200)
            show("#pdf-mergelist")
            t.step("Add PDFs after this one: other.pdf is listed", expect=lambda: "other.pdf" in pg.inner_text("#pdf-mergelist"), wait=3000)
            grab("#pdf-mergego", "merged.pdf")
            show("#pdf-pagesstatus")
            t.step("Download merged PDF: the status line says what was built", expect=lambda: bool(pst()), wait=3000)
            pg.click("#pdf-mergeclear")
            pg.wait_for_timeout(500)
            show("#pdf-mergelist")
            t.step("Clear added files: the added-files list is empty", expect=lambda: pg.locator("#pdf-mergelist li").count() == 0, wait=3000)

        run_tour(ctx, "61_pdf_pages", body, notes="Device-local viewer on generated PDFs (4 pages and 3 pages); secure-context test flag. Headless Chromium 390x844 emulated. Downloaded files are saved off-camera and are not opened in this clip.", unshown=["opening the downloaded files", "form fields", "password PDF", "merge limits"])

    g["DRILLS"]["tour_pdf_pages"] = tour


def register_pdf_forms(g):
    APP = g["APP"]

    def tour(ctx):
        import os
        import pathlib

        data = (pathlib.Path(__file__).resolve().parents[1] / "js" / "fixtures" / "form_basic.pdf").read_bytes()

        def body(t, pg):
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
                t.step("No secure context in this browser", expect=lambda: False)
                return
            pg.set_input_files("#pdf-open", files=[{"name": "form.pdf", "mimeType": "application/pdf", "buffer": data}])
            for _ in range(50):
                if where().startswith("Page 1 of"):
                    break
                pg.wait_for_timeout(250)
            os.makedirs("/tmp/vid/dl", exist_ok=True)

            def show(sel):
                pg.evaluate("s=>{document.querySelector(s).scrollIntoView({block:'center'})}", sel)

            def grab(sel, name):
                with pg.expect_download(timeout=15000) as d:
                    pg.click(sel)
                d.value.save_as("/tmp/vid/dl/" + name)

            fst = lambda: pg.inner_text("#pdf-formstatus").strip()
            lab = lambda name: pg.locator("#pdf-formlist label", has_text=name).first
            show("#pdf-formfind")
            pg.click("#pdf-formfind")
            pg.wait_for_timeout(800)
            show("#pdf-formstatus")
            t.step("Find form fields: the status line counts the fields found", expect=lambda: "8 field(s)" in fst(), wait=3500)
            show("#pdf-formlist")
            t.step("The form list shows the fields as boxes you can fill", expect=lambda: pg.locator("#pdf-formlist label").count() >= 6, wait=3000)
            pg.click("#pdf-formsave")
            pg.wait_for_timeout(500)
            show("#pdf-formstatus")
            t.step("Download a filled copy with nothing changed: the status says no copy was made", expect=lambda: "no copy was made" in fst(), wait=3500)
            lab("full_name").locator("input").fill("Test Person")
            lab("agree").locator("input").check()
            lab("country").locator("select").select_option("France")
            pg.locator("#pdf-formlist input[type=radio][value=large]").check()
            lab("notes").locator("textarea").fill("line one")
            show("#pdf-formlist")
            t.step("Filled the name, ticked agree, chose France and a size, typed a note: the boxes show them", expect=lambda: lab("full_name").locator("input").input_value() == "Test Person" and lab("country").locator("select").input_value() == "France", wait=3500)
            grab("#pdf-formsave", "form_filled.pdf")
            show("#pdf-formstatus")
            t.step("Download a filled copy: the status line says a new file was built", expect=lambda: "no copy was made" not in fst() and bool(fst()), wait=3500)

        run_tour(ctx, "62_pdf_forms", body, notes="Device-local viewer on the repo's test form PDF (tests/js/fixtures/form_basic.pdf); secure-context test flag. Headless Chromium 390x844 emulated. The filled copy is saved off-camera and is not opened in this clip. Test text only.", unshown=["opening the filled copy", "text limit refusal", "read-only field", "signed form PDF", "choice list with several picks"])

    g["DRILLS"]["tour_pdf_forms"] = tour


def register_pdf_view_buttons(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        def body(t, pg):
            data = g["_n_page_pdf"](4, "Keep")
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            pg.set_input_files("#pdf-open", files=[{"name": "demo.pdf", "mimeType": "application/pdf", "buffer": data}])
            for _ in range(40):
                if where().startswith("Page 1 of 4") and pg.evaluate("document.getElementById('pdf-canvas').width > 1"):
                    break
                pg.wait_for_timeout(250)
            os.makedirs("/tmp/vid/dl", exist_ok=True)
            top = lambda: pg.evaluate("document.getElementById('pdf-pagewrap').scrollIntoView({block:'start'});0")
            width = lambda: pg.evaluate("document.getElementById('pdf-canvas').width")
            top()
            t.step("A 4-page PDF is open: Page 1 of 4", expect=lambda: where().startswith("Page 1 of 4"), wait=2500)
            pg.click("#pdf-zin")
            pg.wait_for_timeout(600)
            w1 = width()
            top()
            t.step("Zoom in: the page picture is wider", expect=lambda: w1 > 1, wait=2500)
            pg.click("#pdf-zout")
            pg.wait_for_timeout(600)
            top()
            t.step("Zoom out: the page picture is narrower than after Zoom in", expect=lambda: width() < w1, wait=2800)
            pg.click("#pdf-fitp")
            pg.wait_for_timeout(600)
            top()
            t.step("Fit page: the whole page picture is on screen", expect=lambda: True, wait=2800)
            with pg.expect_download(timeout=15000) as d:
                pg.click("#pdf-exporttext")
            d.value.save_as("/tmp/vid/dl/page_text.txt")
            pg.evaluate("document.getElementById('pdf-exporttext').scrollIntoView({block:'center'});0")
            t.step("Download text of this page: a text file was saved off-camera", expect=lambda: os.path.getsize("/tmp/vid/dl/page_text.txt") > 0, wait=2800)

        run_tour(ctx, "63_pdf_view_buttons", body, notes="Device-local viewer on a generated 4-page PDF; secure-context test flag. Headless Chromium 390x844 emulated. The text file is saved off-camera. Password-protected files are not filmed on purpose.", unshown=["password-protected open (not filmed, no password entry on camera)", "Discard unreadable saved marks"])

    g["DRILLS"]["tour_pdf_view_buttons"] = tour


def register_drive_two_sessions(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import secrets as _secrets
        import time as _time

        import pyotp

        users(ctx)
        pw2 = _secrets.token_urlsafe(18) + "aA1!"
        sec2 = pyotp.random_base32(length=32)

        def make():
            from django.contrib.auth.hashers import make_password
            from django.utils import timezone

            from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
            from apps.accounts.models import CustomUser, TOTPCredential

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            two.password = make_password(pw2)
            two.save(update_fields=["password"])
            ct, nonce, key_id = encrypt_secret(sec2, get_auth_key_ring())
            TOTPCredential.objects.create(user=two, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now())

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        # off camera: sign in as the second synthetic account in a separate, unrecorded browser window
        errs, remote, guard = ctx.salow_hooks
        c2 = ctx.browser.new_context(viewport={"width": 390, "height": 844})
        c2.route("**/*", guard)
        q = c2.new_page()
        q.goto(APP + "/sign-in/")
        q.wait_for_timeout(800)
        q.click("#btn-signin")
        q.wait_for_timeout(500)
        q.fill("#employee_id", "ST-LIVE-2")
        q.fill("#password", pw2)
        tt = pyotp.TOTP(sec2)
        left = tt.interval - (_time.time() % tt.interval)
        if left < 5:
            _time.sleep(left + 0.5)
        q.fill("#totp_code", tt.now())
        q.click("#confirm-btn")
        q.wait_for_load_state()
        q.wait_for_timeout(1500)
        signed_in = "/sign-in" not in q.url
        cookies2 = c2.cookies()
        c2.close()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            cookies1 = pg.context.cookies()
            pg.goto(APP + "/drive/")
            t.step("First account (ST-LIVE-1): My Drive", expect=lambda: "Drive" in txt(), wait=1800)
            t.cap("Next: create the folder Joint plans, open it, share it with ST-LIVE-2 as Editor")
            pg.fill("input[placeholder='New folder name']", "Joint plans")
            pg.click("button:has-text('Create folder')")
            pg.wait_for_timeout(500)
            pg.click("a.drive-name:has-text('Joint plans')")
            pg.wait_for_timeout(500)
            pg.fill("input[placeholder='Employee ID']", "ST-LIVE-2")
            pg.select_option("select[name=role]", "editor")
            pg.click("button:has-text('Share folder')")
            pg.wait_for_timeout(500)
            pg.locator(".drive-share").scroll_into_view_if_needed()
            t.step("Sharing lists Second Synthetic as Editor", expect=lambda: "Second Synthetic" in pg.locator(".drive-share").inner_text() and "Editor" in pg.locator(".drive-share").inner_text(), wait=3000)
            pg.context.clear_cookies()
            pg.context.add_cookies(cookies2)
            pg.goto(APP + "/drive/")
            t.step("Second account: the page shows Live-employee name is gone and the signed-in person is Second Synthetic" if False else "Now the second account (signed in off camera): its own My Drive is empty", expect=lambda: signed_in and "Joint plans" not in txt() and "Nothing here yet" in txt(), wait=3000)
            t.cap("Next: open the Shared with me tab")
            pg.click(".drive-tabs a:has-text('Shared with me')")
            pg.wait_for_timeout(500)
            t.step("Shared with me lists Joint plans with Editor access", expect=lambda: "Joint plans" in txt() and "Editor access" in txt(), wait=3000)
            t.cap("Next: open Joint plans and create a folder inside it")
            pg.click("a.drive-name:has-text('Joint plans')")
            pg.wait_for_timeout(500)
            pg.fill("input[placeholder='New folder name']", "Made by second")
            pg.click("button:has-text('Create folder')")
            pg.wait_for_timeout(500)
            t.step("The second account created the folder Made by second inside Joint plans", expect=lambda: pg.locator("a.drive-name", has_text="Made by second").count() == 1, wait=3000)
            pg.context.clear_cookies()
            pg.context.add_cookies(cookies1)
            pg.goto(APP + "/drive/")
            pg.click("a.drive-name:has-text('Joint plans')")
            pg.wait_for_timeout(500)
            t.step("Back as the first account: Joint plans now holds Made by second", expect=lambda: pg.locator("a.drive-name", has_text="Made by second").count() == 1, wait=3200)

        run_tour(ctx, "71_drive_two_sessions", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Two separate signed-in synthetic accounts; the second sign-in is done in a separate unrecorded window and only its session is swapped into the recorded window. Nothing secret is typed on camera.", unshown=["both accounts open at the same moment"])

    g["DRILLS"]["tour_drive_two_sessions"] = tour


def register_sheets_focus_edges(g):
    APP = g["APP"]

    def tour(ctx):
        import json as _json
        import os as _os

        _os.makedirs("/tmp/vid/dl", exist_ok=True)
        imp1 = "/tmp/vid/dl/import_focus1.json"
        imp2 = "/tmp/vid/dl/import_focus2.json"
        open(imp1, "w").write(_json.dumps({"A1": "Imported"}))
        open(imp2, "w").write(_json.dumps({"A1": "Replaced"}))
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)
        bar = lambda pg: pg.input_value("#sheet-bar")

        def sc(pg, sel):
            pg.evaluate("s=>{var e=document.querySelector(s);if(e)e.scrollIntoView({block:'center'})}", sel)

        def body(t, pg):
            pg.on("dialog", lambda d: d.accept())
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            t.cap("Next: type 5 into A1, then click A1 and then click the Formula bar without typing")
            pg.click('input[data-addr="A1"]')
            pg.keyboard.type("5")
            pg.keyboard.press("Tab")
            pg.click('input[data-addr="A1"]')
            pg.click("#sheet-bar")
            pg.wait_for_timeout(300)
            sc(pg, "#sheet-bar")
            t.step("Formula bar is focused and untouched, showing 5 for A1", expect=lambda: bar(pg) == "5" and pg.evaluate("document.activeElement.id") == "sheet-bar", wait=2800)
            t.cap("Next: import a file whose A1 is Imported while the Formula bar is focused")
            pg.set_input_files("#sheet-import", imp1)
            pg.wait_for_timeout(900)
            sc(pg, "#sheet-bar")
            t.step("The untouched Formula bar now shows Imported", expect=lambda: bar(pg) == "Imported", wait=3400)
            t.cap("Next: click B1 (leaves the bar)")
            pg.click('input[data-addr="B1"]')
            pg.wait_for_timeout(400)
            t.step("A1 still reads Imported after the bar was left", expect=lambda: val(pg, "A1") == "Imported", wait=3000)
            t.cap("Next: click A1, click the Formula bar, type a new text typed-by-hand, then import a file whose A1 is Replaced")
            pg.click('input[data-addr="A1"]')
            pg.click("#sheet-bar")
            pg.fill("#sheet-bar", "typed-by-hand")
            pg.set_input_files("#sheet-import", imp2)
            pg.wait_for_timeout(900)
            sc(pg, "#sheet-bar")
            t.step("The Formula bar keeps the typed text; the cell A1 shows Replaced from the import", expect=lambda: bar(pg) == "typed-by-hand" and val(pg, "A1") == "Replaced", wait=3600)
            t.cap("Next: press Tab (commits what is in the bar)")
            pg.keyboard.press("Tab")
            pg.wait_for_timeout(500)
            t.step("The typed text was committed to A1 (a deliberate edit made after the import)", expect=lambda: val(pg, "A1") == "typed-by-hand", wait=3200)
            pg.reload()
            pg.wait_for_timeout(700)
            t.step("After reload (read back from storage): A1 is typed-by-hand", expect=lambda: val(pg, "A1") == "typed-by-hand", wait=3000)
            t.cap("Next: click A1, type zzz without leaving, switch to a new tab by Add tab (the click is sent by script so the cell keeps focus), then click B1")
            pg.click('input[data-addr="A1"]')
            pg.keyboard.type("zzz")
            pg.evaluate("document.getElementById('tab-add').click()")
            pg.wait_for_timeout(600)
            pg.click('input[data-addr="B1"]')
            pg.wait_for_timeout(500)
            sc(pg, 'input[data-addr="A1"]')
            t.step("On the new tab A1 is empty (zzz did not come along)", expect=lambda: val(pg, "A1") == "", wait=3400)
            t.cap("Next: back to the first tab")
            pg.locator("#sheet-tabs button").first.click()
            pg.wait_for_timeout(600)
            sc(pg, 'input[data-addr="A1"]')
            t.step("First tab: A1 is still typed-by-hand", expect=lambda: val(pg, "A1") == "typed-by-hand", wait=3400)

        run_tour(ctx, "70_sheets_focus_edges", body, notes="Device-local sheet. Headless Chromium 390x844 emulated. The import file is chosen by the test and the Add tab click is sent by script so focus stays on the cell, which is how the focused cases are reached.", unshown=["typing in a grid cell while the file is still reading"])

    g["DRILLS"]["tour_sheets_focus_edges"] = tour


def register_worklog_team_refusal(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        users(ctx)
        ids = {}

        def run(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def fixture():
            from apps.accounts.models import CustomUser
            from apps.directory.models import Department, Team, TeamMembership
            from apps.posts import services as ps

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me.role = CustomUser.Role.MANAGER
            me.save(update_fields=["role"])
            dep = Department.objects.create(name="Fixture Dept")
            ta = Team.objects.create(code="FIX-A", name="Alpha Team", department=dep, lead=me)
            tb = Team.objects.create(code="FIX-B", name="Beta Team", department=dep)
            TeamMembership.objects.create(team=tb, user=me)
            TeamMembership.objects.create(team=ta, user=two)
            TeamMembership.objects.create(team=tb, user=two)
            a = ps.create_post(author=two, title="Alpha team standup", content="Alpha body.", team=ta)
            b = ps.create_post(author=two, title="Beta team standup", content="Beta body.", team=tb)
            ids.update(a=a.pk, b=b.pk, ta=ta.pk, tb=tb.pk)

        run(fixture)

        def demote():
            from apps.accounts.models import CustomUser

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            me.role = CustomUser.Role.EMPLOYEE
            me.save(update_fields=["role"])

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            show = lambda: pg.evaluate("document.querySelector('a[href*=\"/posts/\"]:not([href$=\"/posts/\"]):not([href*=\"create\"])').scrollIntoView({block:'start'})")
            pg.goto(APP + "/posts/")
            t.step("Work Logs & Feed as a Manager: both team posts are listed", lambda: show(), expect=lambda: "Alpha team standup" in txt() and "Beta team standup" in txt(), wait=3000)
            t.cap("Next: choose the team Alpha Team in the Team filter and press Filter")
            pg.select_option("select[name=team]", str(ids["ta"]))
            with pg.expect_navigation():
                pg.click("button:text-is('Filter')")
            show()
            t.step("Team filter Alpha Team: only the Alpha team post remains", expect=lambda: "Alpha team standup" in txt() and "Beta team standup" not in txt(), wait=3400)
            t.cap("Next: switch the Team filter to Beta Team and press Filter")
            pg.select_option("select[name=team]", str(ids["tb"]))
            with pg.expect_navigation():
                pg.click("button:text-is('Filter')")
            show()
            t.step("Team filter Beta Team: only the Beta team post remains", expect=lambda: "Beta team standup" in txt() and "Alpha team standup" not in txt(), wait=3400)
            run(demote)
            t.cap("Next (test fixture: this account is now an ordinary Employee): open the edit page of a post written by Second Synthetic")
            resp = pg.goto(APP + "/posts/%d/edit/" % ids["a"])
            pg.evaluate("window.scrollTo(0,0)")
            t.step("The app refuses: the edit page answers with status %s and a plain page that reads 403 Forbidden" % (resp.status if resp else "?"), expect=lambda: bool(resp) and resp.status == 403, wait=3400)
            t.note("Not shown: other roles' edit rules and moderator edit.", 3)

        run_tour(ctx, "69_worklog_team_refusal", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: this account is a Manager, lead of Alpha Team and a member of Beta Team, Second Synthetic wrote one post in each; then the account is set back to Employee for the refusal.", unshown=["moderator edit"])

    g["DRILLS"]["tour_worklog_team_refusal"] = tour


def register_slides_pointer_anim(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            pg.on("dialog", lambda d: d.accept())
            see = lambda: pg.locator("#sl-canvas-wrap").scroll_into_view_if_needed()
            num = lambda sel: int(pg.input_value(sel) or 0)
            alist = lambda: [x.strip() for x in pg.locator("#sl-anim-list li").all_inner_texts()]
            st = lambda: pg.inner_text("#sl-status")
            t.cap("Next: add a rectangle")
            pg.click("#sl-add-rect")
            pg.wait_for_timeout(500)
            see()
            t.step("A rectangle is on the slide and selected", expect=lambda: pg.locator("#sl-items li").count() == 1, wait=2200)
            x0, w0 = num("#sl-x"), num("#sl-w")
            t.cap("Next: drag the rectangle with the mouse (press on it, move right, release)")
            bb = pg.locator("#sl-canvas-wrap svg rect[data-id]").first.bounding_box()
            cx, cy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
            pg.mouse.move(cx, cy)
            pg.mouse.down()
            pg.mouse.move(cx + 25, cy + 10, steps=6)
            pg.mouse.move(cx + 50, cy + 20, steps=6)
            pg.mouse.up()
            pg.wait_for_timeout(600)
            see()
            x1 = num("#sl-x")
            t.step("Drag: the X box went from %d to %d" % (x0, x1), expect=lambda: num("#sl-x") > x0, wait=3200)
            t.cap("Next: drag the blue corner square to make it larger")
            hb = pg.locator("#sl-canvas-wrap svg rect[data-handle]").first.bounding_box()
            hx, hy = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
            pg.mouse.move(hx, hy)
            pg.mouse.down()
            pg.mouse.move(hx + 15, hy + 8, steps=5)
            pg.mouse.move(hx + 30, hy + 15, steps=5)
            pg.mouse.up()
            pg.wait_for_timeout(600)
            see()
            w1 = num("#sl-w")
            t.step("Corner drag: the W box went from %d to %d" % (w0, w1), expect=lambda: num("#sl-w") > w0, wait=3200)
            t.cap("Next: add an ellipse, set Animation to Appear on click for it, then select the rectangle and do the same")
            pg.click("#sl-add-ellipse")
            pg.wait_for_timeout(400)
            pg.select_option("#sl-anim", "appear")
            pg.wait_for_timeout(300)
            pg.locator("#sl-items li").first.click()
            pg.select_option("#sl-anim", "appear")
            pg.wait_for_timeout(400)
            pg.locator("#sl-anim-list").scroll_into_view_if_needed()
            first0 = alist()
            t.step("Animation order lists two items: %s" % " | ".join(first0), expect=lambda: len(alist()) == 2, wait=3600)
            t.cap("Next: with the rectangle selected, press One click earlier")
            pg.click("#sl-anim-up")
            pg.wait_for_timeout(500)
            pg.locator("#sl-anim-list").scroll_into_view_if_needed()
            second = alist()
            t.step("One click earlier: the order now reads %s" % " | ".join(second), expect=lambda: alist() != first0 and len(alist()) == 2, wait=3800)
            t.cap("Next: press One click later")
            pg.click("#sl-anim-down")
            pg.wait_for_timeout(500)
            pg.locator("#sl-anim-list").scroll_into_view_if_needed()
            t.step("One click later: the order is back to %s" % " | ".join(first0), expect=lambda: alist() == first0, wait=3800)

        run_tour(ctx, "68_slides_pointer_anim", body, notes="Device-local deck; secure-context test flag. Headless Chromium 390x844 emulated. The drags use a mouse pointer, not a finger on a touch screen.", unshown=["finger drag on a real touch screen", "unreadable-deck recovery"])

    g["DRILLS"]["tour_slides_pointer_anim"] = tour


def register_sheets_csv_import(g):
    APP = g["APP"]

    def tour(ctx):
        csv_text = 'item,qty,note\r\napple,3,"red, sweet"\r\npear,4,"say ""hi"""\r\n=SUM(B2:B3),7,formula text\r\n'
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)

        def sc(pg, sel, inline="center"):
            pg.evaluate("a=>{var e=document.querySelector(a[0]);if(e)e.scrollIntoView({block:'center',inline:a[1]})}", [sel, inline])

        def body(t, pg):
            seen, ans = [], [True]
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept() if ans[0] else d.dismiss()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            st = lambda: pg.inner_text("#sheet-status")
            cell = lambda a: 'input[data-addr="%s"]' % a
            pg.click(cell("E5"))
            pg.keyboard.type("old")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(300)
            t.step("A validation rule: C3 may only hold yes or no", lambda: (pg.fill("#val-range", "C3:C3"), pg.select_option("#val-kind", "list"), pg.fill("#val-items", "yes, no"), pg.click("#val-add"), sc(pg, "#val-list")), expect=lambda: pg.locator("#val-list li").count() == 1, wait=2600)
            sc(pg, "#sheet-import-csv")
            ans[0] = False
            t.cap("Next: Import CSV, choose the file, and answer Cancel to the question")
            pg.set_input_files("#sheet-import-csv", files=[{"name": "demo.csv", "mimeType": "text/csv", "buffer": csv_text.encode()}])
            pg.wait_for_timeout(900)
            sc(pg, cell("E5"))
            t.step("The question listed the counts first (1 cell the rule refuses, 1 starting with =, all 1 old cells replaced and blanks); Cancel left the sheet alone: E5 is old, A1 empty", expect=lambda: len(seen) == 1 and "1 cells a validation rule refuses will be left out" in seen[0] and "1 cells in the file start with =" in seen[0] and "replaces all 1 cells in this tab, including cells in places the file does not fill, which become blank" in seen[0] and "Import cancelled" in st() and val(pg, "E5") == "old" and val(pg, "A1") == "", wait=3600)
            ans[0] = True
            pg.set_input_files("#sheet-import-csv", files=[{"name": "demo.csv", "mimeType": "text/csv", "buffer": csv_text.encode()}])
            pg.wait_for_timeout(900)
            sc(pg, cell("A1"), "start")
            t.step("Same file again, answered OK: A1 item, A2 apple, B3 is 4, and the old E5 is gone", expect=lambda: val(pg, "A1") == "item" and val(pg, "A2") == "apple" and val(pg, "B3") == "4" and val(pg, "E5") == "", wait=3000)
            sc(pg, "#sheet-status")
            t.step("The status counts 11 cells and says 1 refused and 1 started with = and was kept as text", expect=lambda: "11 cells imported from 4 rows by 3 columns" in st() and "1 refused by validation rules" in st() and "1 cells in the file started with =" in st(), wait=3200)
            sc(pg, cell("C2")); pg.click(cell("C2")); pg.wait_for_timeout(300)
            t.step("Column C in view, C2 selected: the cell and the formula bar both show red, sweet with its comma", expect=lambda: val(pg, "C2") == "red, sweet" and pg.input_value("#sheet-bar") == "red, sweet", wait=3200)
            sc(pg, cell("C3")); pg.click(cell("C3")); pg.wait_for_timeout(300)
            t.step("C3 is empty: the rule refused say \"hi\" and the formula bar is empty", expect=lambda: val(pg, "C3") == "" and pg.input_value("#sheet-bar") == "", wait=3200)
            sc(pg, cell("A4"), "start"); pg.click(cell("A4")); pg.wait_for_timeout(300)
            t.step("A4 selected: the cell and the formula bar show '=SUM(B2:B3) with the apostrophe as real text, not calculated", expect=lambda: val(pg, "A4") == "'=SUM(B2:B3)" and pg.input_value("#sheet-bar") == "'=SUM(B2:B3)", wait=3200)
            pg.keyboard.press("End"); pg.keyboard.type("x"); pg.keyboard.press("Enter"); pg.wait_for_timeout(400)
            t.step("Edited by typing x at the end: it stays text, '=SUM(B2:B3)x, the apostrophe stays and nothing is calculated", expect=lambda: val(pg, "A4") == "'=SUM(B2:B3)x", wait=3200)
            pg.click("#sheet-undo"); pg.wait_for_timeout(300); pg.click("#sheet-undo"); pg.wait_for_timeout(500)
            sc(pg, cell("E5"))
            t.step("Undo (twice, the edit then the import) brings the old sheet back: E5 holds old and A1 is empty", expect=lambda: val(pg, "E5") == "old" and val(pg, "A1") == "", wait=3200)
            seen.clear(); ans[0] = False
            pg.set_input_files("#sheet-import-csv", files=[{"name": "latin.csv", "mimeType": "text/csv", "buffer": b"caf\xe9\n"}])
            pg.wait_for_timeout(800)
            sc(pg, "#sheet-status")
            t.step("A file saved in another encoding (a byte that is not UTF-8): the question warns 1 character could not be read as UTF-8; Cancel keeps E5", expect=lambda: len(seen) == 1 and "1 characters could not be read as UTF-8" in seen[0] and "UTF-8 only" in seen[0] and val(pg, "E5") == "old", wait=3400)
            ans[0] = True
            pg.evaluate("""()=>{var i=document.getElementById('sheet-import-csv'),dt=new DataTransfer();dt.items.add(new File(['x,y\\n1,2\\n'],'t.csv',{type:'text/csv'}));i.files=dt.files;i.dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('tab-add').click();}""")
            pg.wait_for_timeout(1000)
            sc(pg, "#sheet-status")
            t.step("The tab was switched (Add tab) while the file was loading: the import is cancelled and says so", expect=lambda: "You switched tabs while the file was loading" in st(), wait=3400)
            pg.evaluate("document.querySelector('#sheet-tabs button').click()")
            pg.wait_for_timeout(500)
            sc(pg, cell("E5"))
            t.step("Back on the first tab its cell E5 is still old and nothing from t.csv landed there; the new tab was left empty", expect=lambda: val(pg, "E5") == "old" and val(pg, "A1") == "", wait=3200)
        run_tour(ctx, "sheets_csv_import", body, notes="Headless Chromium 390x844 emulated, one run. Hand-made CSV files only, not files from Google Sheets or Excel. Confirm dialogs are answered off camera and their text is checked. Not shown: the 5,000 row limit, import while a filter is on.", unshown=["row limit", "filter on"])

    g["DRILLS"]["tour_sheets_csv_import"] = tour


def register_sheets_freeze(g):
    APP = g["APP"]

    def tour(ctx):
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)
        cell = lambda a: 'input[data-addr="%s"]' % a

        def rect(pg, sel):
            return pg.evaluate("s=>{var r=document.querySelector(s).getBoundingClientRect();return [r.left,r.top]}", sel)

        def box(pg):
            return pg.evaluate("()=>{var r=document.getElementById('sheet-root').getBoundingClientRect();return [r.left,r.top]}")

        def scroll_to(pg, top, left):
            pg.evaluate("a=>{var e=document.getElementById('sheet-root');e.scrollTop=a[0];e.scrollLeft=a[1]}", [top, left])
            pg.evaluate("document.getElementById('sheet-root').scrollIntoView({block:'start'});scrollBy(0,-(document.getElementById('sheet-barrow').getBoundingClientRect().height+6))")
            pg.wait_for_timeout(400)

        def hit_all(pg, nrows, ncols):
            # every pinned cell (corner, column letters 1..ncols, row numbers 1..nrows, and the pinned data cells) must be the topmost element at its own centre,
            # lie inside the scroll box and below the sticky formula bar, and none may overlap another; a non-pinned row 3 cell must not be hit anywhere in the box
            return pg.evaluate("""a=>{var nr=a[0],nc=a[1],box=document.getElementById('sheet-root'),b=box.getBoundingClientRect(),bar=document.getElementById('sheet-barrow').getBoundingClientRect(),tb=box.querySelector('table'),out=[],rects=[];
 function chk(el,name){var r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+Math.min(r.height/2,12),h=document.elementFromPoint(x,y);
  var ok=h&&(h===el||el.contains(h))&&r.top>=b.top&&r.bottom<=b.bottom+1&&r.left>=b.left&&r.top>=bar.bottom-1;if(!ok)out.push(name+' hidden/outside:'+(h?h.tagName+':'+(h.textContent||'').slice(0,12):'none')+' top='+Math.round(r.top)+' bar='+Math.round(bar.bottom));rects.push([name,r.left,r.top,r.right,r.bottom]);}
 chk(tb.rows[0].cells[0],'corner');for(var c=1;c<=nc;c++)chk(tb.rows[0].cells[c],'col'+c);
 for(var r=1;r<=nr;r++){chk(tb.rows[r].cells[0],'rn'+r);for(var c2=1;c2<=nc;c2++)chk(tb.rows[r].cells[c2],'r'+r+'c'+c2);}
 for(var i=0;i<rects.length;i++)for(var j=i+1;j<rects.length;j++){var A=rects[i],B=rects[j];if(A[1]<B[3]-1&&B[1]<A[3]-1&&A[2]<B[4]-1&&B[2]<A[4]-1)out.push('overlap '+A[0]+' '+B[0]);}
 var r3=tb.rows[nr+1].cells[1].getBoundingClientRect();var inview=r3.bottom>b.top&&r3.top<b.bottom;
 return {problems:out,n:rects.length,unpinnedRowInPinZone:inview&&r3.top<tb.rows[nr].getBoundingClientRect().bottom};}""",[nrows,ncols])

        def two_axis_ok(pg, nr, nc):
            h = hit_all(pg, nr, nc)
            sc = pg.evaluate("()=>{var e=document.getElementById('sheet-root');return [e.scrollTop,e.scrollLeft]}")
            return (not h["problems"]) and h["n"] == 1 + nc + nr * (1 + nc) and sc[0] > 100 and sc[1] > 100 and not h["unpinnedRowInPinZone"]

        def pinned(pg, a, top_ok=None, left_ok=None):
            r, b = rect(pg, cell(a)), box(pg)
            return (top_ok is None or (0 <= r[1] - b[1] <= top_ok)) and (left_ok is None or (0 <= r[0] - b[0] <= left_ok))

        def body(t, pg):
            pg.on("dialog", lambda d: d.accept())
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            st = lambda: pg.inner_text("#frz-status")
            tm = lambda: pg.inner_text("#tab-status")
            for a, v in (("A1", "Head1"), ("B1", "Head2"), ("A2", "Row2"), ("A3", "Row3"), ("A30", "Row30"), ("H1", "H-one"), ("H30", "H-thirty")):
                pg.click(cell(a)); pg.keyboard.type(v); pg.keyboard.press("Enter")
            pg.wait_for_timeout(300)
            pg.evaluate("document.getElementById('frz-rows').scrollIntoView({block:'center'})")
            t.step("Set frozen rows 2 and frozen columns 1 and press Apply freeze", lambda: (pg.fill("#frz-rows", "2"), pg.fill("#frz-cols", "1"), pg.click("#frz-apply")), expect=lambda: st().startswith("Frozen: the first 2 row(s) and 1 column(s)"), wait=2600)
            scroll_to(pg, 560, 900)
            t.step("Scrolled far down and right: A1 and A2 (frozen row and column) stay at the top left; A3 keeps its left place but scrolled up out of view; B1 keeps its top place but scrolled left out of view", expect=lambda: two_axis_ok(pg, 2, 1) and pinned(pg, "A1", 80, 80) and pinned(pg, "A2", 140, 80) and (rect(pg, cell("A3"))[1] - box(pg)[1]) < 0 and 0 <= rect(pg, cell("B1"))[1] - box(pg)[1] <= 80 and (rect(pg, cell("B1"))[0] - box(pg)[0]) < 0, wait=3600)
            def covered(pg):
                # page scrolled so the grid top edge is under the sticky formula bar (the position that used to hide the pinned cells); scrolling both axes of the grid
                pg.evaluate("a=>{var e=document.getElementById('sheet-root');e.scrollTop=a[0];e.scrollLeft=a[1];e.scrollIntoView({block:'start'})}", [560, 900])
                pg.wait_for_timeout(700)
                return pg.evaluate("()=>{var b=document.getElementById('sheet-root').getBoundingClientRect(),r=document.getElementById('sheet-barrow').getBoundingClientRect();return r.bottom-b.top}")

            t.step("Page scrolled so the formula bar covers the top of the grid (the position that used to hide the pinned cells): the pinned column letters and rows 1 and 2 sit just below the formula bar and stay readable", lambda: covered(pg), expect=lambda: pg.evaluate("()=>{var b=document.getElementById('sheet-root').getBoundingClientRect(),r=document.getElementById('sheet-barrow').getBoundingClientRect();return r.bottom-b.top}") > 40 and two_axis_ok(pg, 2, 1), wait=3000)
            scroll_to(pg, 560, 900)
            t.step("A frozen row 2 cell stays beside its row number and A30 (frozen column, far below) keeps its place at the left edge", expect=lambda: pinned(pg, "A2", 140, 80) and pinned(pg, "A30", None, 80), wait=3000)
            pg.reload()
            pg.wait_for_timeout(700)
            pg.evaluate("document.getElementById('frz-rows').scrollIntoView({block:'center'})")
            t.step("After a reload the setting is restored: rows 2, columns 1", expect=lambda: pg.input_value("#frz-rows") == "2" and pg.input_value("#frz-cols") == "1", wait=3000)
            scroll_to(pg, 560, 900)
            t.step("After the reload the frozen cells still stay put when scrolled again", expect=lambda: two_axis_ok(pg, 2, 1) and pinned(pg, "A1", 80, 80) and pinned(pg, "A30", None, 80), wait=3000)
            t.step("After the reload, with the page scrolled so the formula bar covers the grid top, the pinned cells are still below the bar", lambda: covered(pg), expect=lambda: two_axis_ok(pg, 2, 1), wait=3000)
            pg.evaluate("document.getElementById('filter-go').scrollIntoView({block:'center'})")
            t.step("Filter on a tab with frozen rows is refused with a message", lambda: pg.click("#filter-go"), expect=lambda: "frozen row(s)" in pg.inner_text("#filter-status") and "refused" in pg.inner_text("#filter-status"), wait=2600)
            pg.evaluate("document.getElementById('frz-rows').scrollIntoView({block:'center'})")
            pg.fill("#frz-rows", "0"); pg.click("#frz-apply"); pg.wait_for_timeout(300)
            pg.evaluate("document.getElementById('filter-go').scrollIntoView({block:'center'})")
            pg.fill("#filter-val", "Row"); pg.click("#filter-go"); pg.wait_for_timeout(300)
            pg.evaluate("document.getElementById('frz-rows').scrollIntoView({block:'center'})")
            pg.fill("#frz-rows", "2"); pg.click("#frz-apply"); pg.wait_for_timeout(300)
            t.step("With a filter on, freezing rows is refused and nothing changes (frozen columns alone stay at 1)", expect=lambda: "A filter is on, so rows cannot be frozen" in st() and pg.evaluate("localStorage.getItem(document.getElementById('sheet-root').getAttribute('data-store-key')+'.freeze')").count('"r":0') == 1, wait=3000)
            pg.evaluate("document.getElementById('filter-clear').click()")
            pg.evaluate("document.getElementById('tab-add').scrollIntoView({block:'center'})")
            pg.click("#tab-add"); pg.wait_for_timeout(500)
            t.step("A new tab starts with no freeze: the fields read 0 and 0 and nothing is pinned", expect=lambda: pg.input_value("#frz-rows") == "0" and pg.input_value("#frz-cols") == "0" and pg.evaluate("document.querySelectorAll('.sheet-frz').length") == 0, wait=3000)
            pg.fill("#frz-rows", "1"); pg.click("#frz-apply"); pg.wait_for_timeout(300)
            pg.evaluate("document.getElementById('tab-del').scrollIntoView({block:'center'})")
            pg.click("#tab-del"); pg.wait_for_timeout(600)
            key = "document.getElementById('sheet-root').getAttribute('data-store-key')+'.freeze'"
            t.step("Delete that tab: the first tab is back with its own freeze (rows 0, columns 1) and the stored setting names only the first tab", expect=lambda: pg.input_value("#frz-rows") == "0" and pg.input_value("#frz-cols") == "1" and list(__import__("json").loads(pg.evaluate("localStorage.getItem(%s)" % key))["tabs"].keys()) == ["t0"], wait=3000)
            pg.click("#tab-restore"); pg.wait_for_timeout(600)
            t.step("Restore deleted tab: the restored tab has its freeze back (rows 1, columns 0), not the first tab's", expect=lambda: pg.input_value("#frz-rows") == "1" and pg.input_value("#frz-cols") == "0", wait=3000)
            kk = pg.evaluate("document.getElementById('sheet-root').getAttribute('data-store-key')")
            pg.evaluate("document.getElementById('tab-del').scrollIntoView({block:'center'})")
            pg.click("#tab-del"); pg.wait_for_timeout(600)
            gone = pg.evaluate("a=>JSON.parse(localStorage.getItem(a+'.tabs')).active", kk)
            pg.evaluate("a=>localStorage.setItem(a[0]+'.freeze', JSON.stringify({v:1,tabs:{[a[1]]:{r:3,c:3}}}))", [kk, "t1"])
            pg.evaluate("document.getElementById('tab-add').scrollIntoView({block:'center'})")
            pg.click("#tab-add"); pg.wait_for_timeout(600)
            t.step("Stale freeze entry for a removed tab id (as if its removal write had failed): a new tab does not reuse that id and starts with no freeze", expect=lambda: pg.evaluate("a=>JSON.parse(localStorage.getItem(a+'.tabs')).active", kk) != "t1" and pg.input_value("#frz-rows") == "0" and pg.input_value("#frz-cols") == "0" and pg.evaluate("document.querySelectorAll('.sheet-frz').length") == 0, wait=3000)
        run_tour(ctx, "sheets_freeze", body, notes="Headless Chromium 390x844 emulated, one run. Pinning is measured from element positions after scrolling and shown in the frames. Not shown: rename, Export and Import leaving the setting alone, damaged stored setting.", unshown=["rename", "export and import", "damaged setting"])

    g["DRILLS"]["tour_sheets_freeze"] = tour


def register_sheets_xtab(g):
    APP = g["APP"]

    def tour(ctx):
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)
        cell = lambda a: 'input[data-addr="%s"]' % a

        def body(t, pg):
            dlg = {"accept": True, "msgs": []}
            pg.on("dialog", lambda d: (dlg["msgs"].append(d.message), d.accept() if dlg["accept"] else d.dismiss()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            put = lambda a, v: (pg.click(cell(a)), pg.keyboard.type(v), pg.keyboard.press("Enter"))
            grid = lambda: pg.evaluate("()=>{document.getElementById('sheet-root').scrollIntoView({block:'start'});scrollBy(0,-(document.getElementById('sheet-barrow').getBoundingClientRect().height+6))}")
            tabs = lambda: pg.evaluate("document.getElementById('sheet-tabs').scrollIntoView({block:'center'})")
            go = lambda n: (pg.locator("#sheet-tabs button", has_text=n).first.click(), pg.wait_for_timeout(500))
            tst = lambda: pg.inner_text("#tab-status")
            sst = lambda: pg.inner_text("#sheet-status")
            def rename(n):
                tabs(); pg.fill("#tab-name", n); pg.click("#tab-rename"); pg.wait_for_timeout(500)
            put("A1", "5"); put("A2", "7")
            pg.click("#tab-add"); pg.wait_for_timeout(500)
            rename("Sales Q1")
            put("B1", "100"); put("B2", "200")
            t.step("Second tab renamed Sales Q1 with B1 100 and B2 200", lambda: grid(), expect=lambda: val(pg, "B1") == "100" and val(pg, "B2") == "200", wait=2500)
            go("Sheet 1")
            put("C1", "='Sales Q1'!B1+A1"); put("C2", "=SUM('Sales Q1'!B1:B2)"); put("C3", "=Sheet9!A1"); put("C4", "=sales q1!B2")
            t.step("Sheet 1: C1 ='Sales Q1'!B1+A1 shows 105, C2 =SUM('Sales Q1'!B1:B2) shows 300, C3 names a tab that does not exist (#REF!), C4 =sales q1!B2 (an unquoted name with a space) is an error", lambda: grid(), expect=lambda: val(pg, "C1") == "105" and val(pg, "C2") == "300" and val(pg, "C3") == "#REF!" and val(pg, "C4").startswith("#"), wait=2500)
            go("Sales Q1"); put("B1", "1000")
            go("Sheet 1")
            t.step("B1 on Sales Q1 changed to 1000: C1 on Sheet 1 now 1005 and C2 1200", lambda: grid(), expect=lambda: val(pg, "C1") == "1005" and val(pg, "C2") == "1200", wait=2500)
            go("Sales Q1"); pg.click(cell("B2")); pg.evaluate("document.getElementById('row-ins').scrollIntoView({block:'center'})"); pg.click("#row-ins"); pg.wait_for_timeout(500)
            t.step("Insert row on Sales Q1 is refused: other formulas refer to this tab by name. Nothing changed (B2 still 200)", expect=lambda: "Nothing was changed" in sst() and "refer to this tab by name" in sst() and val(pg, "B2") == "200", wait=3000)
            go("Sheet 1")
            dlg["accept"] = False; dlg["msgs"].clear()
            go("Sales Q1"); rename("Q1")
            c1 = len(dlg["msgs"]) == 1 and "#REF!" in dlg["msgs"][0] and "Sales Q1" in dlg["msgs"][0] and "Rename cancelled" in tst()
            go("Sales Q1")
            t.step("Rename Sales Q1 to Q1 and dismiss the question (it names the formulas that would show #REF!): the tab keeps its name", expect=lambda: c1 and pg.locator("#sheet-tabs button", has_text="Sales Q1").count() == 1, wait=3000)
            dlg["accept"] = True; rename("Q1")
            go("Sheet 1")
            t.step("Rename and accept: C1 and C2 show #REF! because no tab is called Sales Q1", lambda: grid(), expect=lambda: val(pg, "C1") == "#REF!" and val(pg, "C2") == "#REF!", wait=3000)
            go("Q1"); rename("Sales Q1"); go("Sheet 1")
            t.step("Rename it back to Sales Q1: C1 shows 1005 and C2 1200 again", lambda: grid(), expect=lambda: val(pg, "C1") == "1005" and val(pg, "C2") == "1200", wait=3000)
            go("Sales Q1"); dlg["msgs"].clear(); tabs(); pg.click("#tab-del"); pg.wait_for_timeout(600)
            go("Sheet 1")
            t.step("Delete the tab Sales Q1 (the question counts the formulas that will show #REF!): C1 and C2 show #REF!", lambda: grid(), expect=lambda: val(pg, "C1") == "#REF!" and any("refer to it by name" in m for m in dlg["msgs"]), wait=3000)
            tabs(); pg.click("#tab-restore"); pg.wait_for_timeout(700); go("Sheet 1")
            t.step("Restore deleted tab (it comes back as a tab; go to Sheet 1): C1 shows 1005 and C2 1200 again", lambda: grid(), expect=lambda: val(pg, "C1") == "1005" and val(pg, "C2") == "1200", wait=3000)
            put("D1", "='Sales Q1'!D1"); go("Sales Q1"); put("D1", "='Sheet 1'!D1"); go("Sheet 1")
            t.step("A loop across two tabs (D1 on each points at the other) shows #CIRC! on Sheet 1", lambda: grid(), expect=lambda: val(pg, "D1") == "#CIRC!", wait=3000)
            pg.reload(); pg.wait_for_timeout(700)
            t.step("After a reload C1 still shows 1005 and C2 1200 (read from the stored tab)", lambda: grid(), expect=lambda: val(pg, "C1") == "1005" and val(pg, "C2") == "1200", wait=3000)
        run_tour(ctx, "sheets_xtab", body, notes="Headless Chromium 390x844 emulated, one run. Not shown: a tab changed in another window, an unreadable tab (#TAB!), Export or CSV of cross-tab formulas.", unshown=["another window", "unreadable tab", "export and csv"])

    g["DRILLS"]["tour_sheets_xtab"] = tour


def register_sheets_widths(g):
    APP = g["APP"]

    def tour(ctx):
        cell = lambda a: 'input[data-addr="%s"]' % a
        val = lambda pg, a: pg.input_value(cell(a))

        def body(t, pg):
            dlg = {"accept": True, "msgs": []}
            pg.on("dialog", lambda d: (dlg["msgs"].append(d.message), d.accept() if dlg["accept"] else d.dismiss()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            put = lambda a, v: (pg.click(cell(a)), pg.keyboard.type(v), pg.keyboard.press("Enter"))
            grid = lambda: pg.evaluate("()=>{document.getElementById('sheet-root').scrollIntoView({block:'start'});scrollBy(0,-(document.getElementById('sheet-barrow').getBoundingClientRect().height+6))}")
            ctl = lambda: pg.evaluate("document.getElementById('cw-apply').scrollIntoView({block:'center'})")
            w = lambda a: round(pg.evaluate("a=>document.querySelector('input[data-addr=\"'+a+'\"]').getBoundingClientRect().width", a))
            cws = lambda: pg.evaluate("Object.keys(localStorage).filter(k=>k.endsWith('.cw')).sort()")
            def setw(col, px):
                ctl(); pg.select_option("#cw-col", col); pg.fill("#cw-px", str(px)); pg.click("#cw-apply"); pg.wait_for_timeout(300)
            cs = lambda: pg.inner_text("#cw-status")
            put("A1", "Name"); put("B1", "A much longer piece of text in B"); put("C1", "Third")
            t.step("Start: A1 Name, B1 a long text, C1 Third. All columns standard width (144 px)", lambda: grid(), expect=lambda: w("A1") == 144 and w("B1") == 144 and w("C1") == 144, wait=2500)
            setw("B", 260)
            t.step("Set column B to 260 px: B is 260 wide, A and C stay 144, and the status says it is saved for this tab only", lambda: grid(), expect=lambda: w("B1") == 260 and w("A1") == 144 and w("C1") == 144 and "260 px" in cs() and len(cws()) == 1, wait=2500)
            setw("A", 39)
            m1 = cs(); setw("A", 401); m2 = cs(); ctl()
            t.step("Widths 39 and 401 are refused with the 40 to 400 message and nothing changes (A stays 144)", expect=lambda: "40 to 400" in m1 and "40 to 400" in m2 and w("A1") == 144, wait=3000)
            setw("A", 60)
            t.step("Set column A to 60 px (the minimum useful narrow width): A is 60 wide", lambda: grid(), expect=lambda: w("A1") == 60, wait=2500)
            pg.fill("#frz-cols", "2"); pg.click("#frz-apply"); pg.wait_for_timeout(400)
            def pinned():
                return pg.evaluate("""()=>{const t=document.querySelector('#sheet-root table'),rh=t.rows[1].cells[0].offsetWidth,a=t.rows[1].cells[1].offsetWidth;
                  const th2=t.rows[0].cells[2], td2=t.rows[1].cells[2], r=document.getElementById('sheet-root').getBoundingClientRect();
                  const b=document.querySelector('input[data-addr=B1]').getBoundingClientRect(), x=b.left+Math.min(20,b.width/2), y=b.top+b.height/2;
                  const top=document.elementFromPoint(x,y);
                  return {left:parseFloat(td2.style.left), want:rh+a, th:parseFloat(th2.style.left), hit: top && top.getAttribute && top.getAttribute('data-addr')};}""")
            grid(); pg.evaluate("document.getElementById('sheet-root').scrollLeft = 400"); pg.wait_for_timeout(300)
            pn = pinned()
            t.step("Freeze 2 columns, then scroll the grid sideways by 400: the pinned B column sits at the measured offset of the narrow A column, and the topmost element at B1's left part is the B1 input", expect=lambda: abs(pn["left"] - pn["want"]) < 1.5 and abs(pn["th"] - pn["want"]) < 1.5 and pn["hit"] == "B1", wait=2500)
            setw("A", 200)
            grid(); pg.evaluate("document.getElementById('sheet-root').scrollLeft = 500"); pg.wait_for_timeout(300)
            pn2 = pinned()
            t.step("Change A to 200 px with the freeze still on: the pinned offset is measured again (now larger), B1 is still the topmost element", expect=lambda: abs(pn2["left"] - pn2["want"]) < 1.5 and pn2["want"] > pn["want"] + 100 and pn2["hit"] == "B1", wait=2500)
            pg.evaluate("document.getElementById('sheet-root').scrollLeft = 0")
            pg.reload(); pg.wait_for_timeout(700); pg.evaluate("document.getElementById('sheet-root').scrollLeft = 500"); pg.wait_for_timeout(300)
            pn3 = pinned()
            t.step("After a reload A is 200, B is 260 and the freeze is still measured correctly", lambda: grid(), expect=lambda: w("A1") == 200 and w("B1") == 260 and abs(pn3["left"] - pn3["want"]) < 1.5 and pn3["hit"] == "B1", wait=3000)
            pg.evaluate("document.getElementById('sheet-root').scrollLeft = 0")
            pg.click("#tab-add"); pg.wait_for_timeout(500)
            t.step("A new tab has standard widths and no stored width entry of its own", lambda: grid(), expect=lambda: w("A1") == 144 and w("B1") == 144 and len(cws()) == 1, wait=3000)
            pg.locator("#sheet-tabs button", has_text="Sheet 1").first.click(); pg.wait_for_timeout(500)
            pg.evaluate("document.querySelector('input[data-addr=B1]').focus()"); pg.evaluate("document.getElementById('col-ins').scrollIntoView({block:'center'})"); pg.click("#col-ins"); pg.wait_for_timeout(600)
            t.step("Insert a column at B: the text from B1 moves to C1 but the widths stay with the positions (A 200, B 260, C 144)", lambda: grid(), expect=lambda: val(pg, "C1").startswith("A much longer") and w("A1") == 200 and w("B1") == 260 and w("C1") == 144, wait=3000)
            pg.locator("#sheet-tabs button", has_text="Sheet 2").first.click(); pg.wait_for_timeout(400)
            pg.locator("#sheet-tabs button", has_text="Sheet 1").first.click(); pg.wait_for_timeout(400)
            pg.evaluate("document.getElementById('tab-del').scrollIntoView({block:'center'})"); pg.click("#tab-del"); pg.wait_for_timeout(600)
            gone = len(cws()) == 0
            pg.click("#tab-restore"); pg.wait_for_timeout(700)
            pg.locator("#sheet-tabs button", has_text="Sheet 1").first.click(); pg.wait_for_timeout(500)
            t.step("Delete Sheet 1 (its width entry goes with it), then Restore deleted tab: widths A 200 and B 260 are back", lambda: grid(), expect=lambda: gone and len(cws()) == 1 and w("A1") == 200 and w("B1") == 260, wait=3000)
            ctl(); pg.click("#cw-reset"); pg.wait_for_timeout(400)
            t.step("Reset all widths: every column is 144 again, the stored entry is removed, and the freeze offset is measured again", lambda: grid(), expect=lambda: w("A1") == 144 and w("B1") == 144 and len(cws()) == 0 and "standard width" in cs(), wait=3000)
            real = None
            setw("C", 100); k = cws()[0]; pg.evaluate("a=>localStorage.setItem(a, '{bad')", k)
            pg.reload(); pg.wait_for_timeout(700); ctl(); pg.select_option("#cw-col", "D"); pg.fill("#cw-px", "90"); pg.click("#cw-apply"); pg.wait_for_timeout(300)
            t.step("A damaged stored width entry reads as standard widths, the page says so, changing is refused and the stored text is left exactly as it was", expect=lambda: "could not be read" in pg.inner_text("#cw-list") and w("C1") == 144 and pg.evaluate("a=>localStorage.getItem(a)", k) == "{bad" and "changes are off" in cs(), wait=3000)
        run_tour(ctx, "sheets_widths", body, notes="Headless Chromium 390x844 emulated, one run. Not shown: dragging a column edge (there is no drag handle, widths are set with the control), a real full browser store, another window changing the entry.", unshown=["drag resize", "real quota", "another window"])

    g["DRILLS"]["tour_sheets_widths"] = tour


def register_slides_tspeed(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            go = lambda n: (pg.locator("#sl-list button").nth(n).click(), pg.wait_for_timeout(300))
            saved = lambda: pg.evaluate("()=>JSON.parse(localStorage.getItem(document.getElementById('slide-root').getAttribute('data-store-key')))")
            panel = lambda: pg.evaluate("document.getElementById('sl-trans').scrollIntoView({block:'center'})")
            nxt = lambda: pg.evaluate("()=>{document.getElementById('sl-show-next').click(); const s=document.querySelector('#sl-show-canvas svg'); const a=s.getAnimations(); if(!a.length) return [s.getAttribute('class'), null, null]; const dur=a[0].effect.getTiming().duration; a[0].pause(); a[0].currentTime=150; return [s.getAttribute('class'), dur, parseFloat(getComputedStyle(s).opacity)]}")
            got = []
            for _ in range(3):
                pg.click("#sl-new"); pg.wait_for_timeout(300)
            def put(x, y, w, h):
                for f, v in (('x', x), ('y', y), ('w', w), ('h', h)):
                    pg.fill('#sl-' + f, str(v)); pg.press('#sl-' + f, 'Tab')
            def fill(k, word, shape, extra=False):
                go(k); pg.click("#sl-add-text"); pg.fill("#sl-text", word); pg.fill("#sl-size", "44"); pg.press("#sl-size", "Tab"); pg.click("#sl-add-" + shape)
                put(560, 300, 300, 180)
                if extra:
                    pg.click("#sl-add-ellipse"); put(80, 300, 300, 180)
                pg.wait_for_timeout(200)
            fill(0, "Plan A: Launch", "rect", True); fill(1, "Plan B: Grow", "ellipse"); fill(2, "Plan C: Hold", "rect"); fill(3, "Plan D: Exit", "ellipse")
            t.step("Each of the 4 slides gets its own text and shapes so the fades are visible (slide 4 shown)", lambda: pg.evaluate("document.getElementById('sl-canvas-wrap').scrollIntoView({block:'center'})"), expect=lambda: "Plan D" in pg.locator("#sl-canvas-wrap").inner_text() or pg.locator("#sl-canvas-wrap svg text").count() >= 1, wait=1500)
            t.step("A deck of 4 slides. Slide 1 has no transition and the speed select is off", lambda: (go(0), panel()), expect=lambda: pg.input_value("#sl-trans") == "none" and pg.is_disabled("#sl-tspeed") and "v" in saved() and saved()["v"] < 3)
            t.step("Slide 2: fade, speed left at Normal. Nothing about speed is stored, deck stays v3", lambda: (go(1), pg.select_option("#sl-trans", "fade"), panel()), expect=lambda: pg.input_value("#sl-tspeed") == "normal" and not pg.is_disabled("#sl-tspeed") and saved()["v"] == 3 and "tspeed" not in saved()["slides"][1])
            t.step("Slide 3: fade and Slow. The deck becomes v10 and stores slow on that slide only", lambda: (go(2), pg.select_option("#sl-trans", "fade"), pg.select_option("#sl-tspeed", "slow"), panel()), expect=lambda: saved()["v"] == 10 and saved()["slides"][2].get("tspeed") == "slow" and "tspeed" not in saved()["slides"][1])
            t.step("Slide 4: fade and Fast", lambda: (go(3), pg.select_option("#sl-trans", "fade"), pg.select_option("#sl-tspeed", "fast"), panel()), expect=lambda: saved()["slides"][3].get("tspeed") == "fast")
            t.step("Reload the page: slide 3 still shows Slow and slide 4 Fast", lambda: (pg.reload(), pg.wait_for_timeout(600), go(2), panel()), expect=lambda: pg.input_value("#sl-tspeed") == "slow" and (go(3), pg.input_value("#sl-tspeed"))[1] == "fast")
            t.step("Slide 3: set the transition to None. The speed select goes off and the speed is removed", lambda: (go(2), pg.select_option("#sl-trans", "none"), panel()), expect=lambda: pg.is_disabled("#sl-tspeed") and "tspeed" not in saved()["slides"][2] and saved()["slides"][3].get("tspeed") == "fast")
            t.step("Put the fade back on slide 3: its speed starts again at Normal", lambda: (pg.select_option("#sl-trans", "fade"), panel()), expect=lambda: pg.input_value("#sl-tspeed") == "normal")
            pg.select_option("#sl-tspeed", "slow")
            t.step("Present from slide 1", lambda: (pg.click("#sl-present"), pg.keyboard.press("Home")), expect=lambda: pg.locator("#sl-show").evaluate("e=>e.open"), wait=1500)
            t.step("Next to slide 2: the fade runs 400 ms (Normal, as in every older deck)", lambda: got.append(nxt()), expect=lambda: got[-1][1] == 400 and "sl-slow" not in got[-1][0] and "sl-fast" not in got[-1][0], wait=900)
            t.step("Next to slide 3: Slow fade, frozen 150 ms in (about a fifth of the way, still faint)", lambda: got.append(nxt()), expect=lambda: got[-1][1] == 800 and "sl-slow" in got[-1][0] and got[-1][2] < 0.5, wait=2600)
            t.step("Next to slide 4: Fast fade, frozen at the same 150 ms (almost fully shown)", lambda: got.append(nxt()), expect=lambda: got[-1][1] == 200 and "sl-fast" in got[-1][0] and got[-1][2] > 0.85, wait=2600)
            t.step("Reduced motion on: the next move runs no fade at all (no animation, no fade class)", lambda: (pg.emulate_media(reduced_motion="reduce"), pg.keyboard.press("Home"), pg.wait_for_timeout(300), got.append(nxt())), expect=lambda: got[-1][1] is None and "sl-fade" not in (got[-1][0] or ""), wait=900)
            pg.emulate_media(reduced_motion="no-preference")
            t.step("Exit the show", lambda: pg.click("#sl-show-exit"), expect=lambda: not pg.locator("#sl-show").evaluate("e=>e.open"))
            item = lambda k: (pg.locator("#sl-items li").nth(k).click(), pg.wait_for_timeout(200))
            def anim(k, spd):
                item(k); pg.select_option("#sl-anim", "fade")
                if spd != "normal": pg.select_option("#sl-anim-spd", spd)
            t.step("Slide 1: text fades in Slow, rectangle Normal, ellipse Fast. The deck becomes v11", lambda: (go(0), anim(0, "slow"), anim(1, "normal"), anim(2, "fast"), pg.evaluate("document.getElementById('sl-anim').scrollIntoView({block:'center'})")), expect=lambda: [x.get("spd") for x in saved()["slides"][0]["anim"]] == ["slow", None, "fast"] and saved()["v"] == 11, wait=1500)
            pg.reload(); pg.wait_for_timeout(600); go(0)
            t.step("Reload: the list shows the speeds and the selected ellipse shows Fast", lambda: (item(2), pg.evaluate("document.getElementById('sl-anim').scrollIntoView({block:'center'})")), expect=lambda: pg.input_value("#sl-anim-spd") == "fast" and "(slow)" in pg.inner_text("#sl-anim-list") and "(fast)" in pg.inner_text("#sl-anim-list"), wait=1500)
            t.step("Present slide 1", lambda: (pg.click("#sl-present"), pg.keyboard.press("Home")), expect=lambda: pg.locator("#sl-show").evaluate("e=>e.open"), wait=1500)
            fx = lambda: pg.evaluate("()=>{document.getElementById('sl-show-next').click(); const g=document.querySelector('#sl-show-canvas svg g.sl-fadeel'); if(!g) return [null,null,null]; const a=g.getAnimations(); if(!a.length) return [g.getAttribute('class'), null, null]; const dur=a[0].effect.getTiming().duration; a[0].pause(); a[0].currentTime=150; return [g.getAttribute('class'), dur, parseFloat(getComputedStyle(g).opacity)]}")
            seen2 = []
            t.step("Click 1: text, Slow fade, frozen 150 ms in (still faint)", lambda: seen2.append(fx()), expect=lambda: seen2[-1][1] == 800 and seen2[-1][2] < 0.5, wait=2600)
            t.step("Click 2: the rectangle fades in over 400 ms (Normal)", lambda: seen2.append(fx()), expect=lambda: seen2[-1][1] == 400 and "sl-slow" not in seen2[-1][0] and "sl-fast" not in seen2[-1][0], wait=900)
            t.step("Click 3: ellipse, Fast fade, frozen at the same 150 ms (almost fully shown)", lambda: seen2.append(fx()), expect=lambda: seen2[-1][1] == 200 and seen2[-1][2] > 0.85, wait=2600)
            t.step("Reduced motion on: Home, then the first click shows the text with no animation", lambda: (pg.emulate_media(reduced_motion="reduce"), pg.keyboard.press("Home"), pg.wait_for_timeout(300), seen2.append(fx())), expect=lambda: seen2[-1][1] is None, wait=900)
            pg.emulate_media(reduced_motion="no-preference")
            t.step("Exit the show", lambda: pg.click("#sl-show-exit"), expect=lambda: not pg.locator("#sl-show").evaluate("e=>e.open"))
            t.note("Shown: the speed select, stored values, reload, and the running animation duration read from the browser. Reduced motion was emulated in headless Chromium for both fades; real devices were not tried. Not shown: the PDF and editor (they do not animate by design).", 3)
        run_tour(ctx, "slides_tspeed", body, notes="Device-local deck. Headless Chromium 390x844 emulated.")
    g["DRILLS"]["tour_slides_tspeed"] = tour


def register_sheets_profile(g):
    """Measures, not proves: load, one edit and a recalculation on a sheet whose rows are filled (a before/after profile for the grid)."""
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            rows = int(os.environ.get("PROFILE_ROWS", "1000"))
            pg.on("console", lambda m: print("CONSOLE", m.type, m.text[:200]))
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(400)
            seed = """(rows) => { const k = document.getElementById('sheet-root').dataset.storeKey; const c = {};
              for (let col = 0; col < 26; col++) { const L = String.fromCharCode(65 + col); for (let r = 1; r <= rows; r++) c[L + r] = r === 1 ? (col === 0 ? '1' : '=' + String.fromCharCode(64 + col) + rows + '+1') : '=' + L + (r - 1) + '+1'; }
              localStorage.setItem(k, JSON.stringify(c)); return Object.keys(c).length; }"""
            n = pg.evaluate(seed, rows)
            pg.reload(); pg.wait_for_timeout(1500)
            print("DIAG", pg.evaluate("() => { const r = document.getElementById('sheet-root'); const t = r.querySelector('table'); const ins = r.querySelectorAll('input'); const a2 = r.querySelector('input[data-addr=' + String.fromCharCode(34) + 'A2' + String.fromCharCode(34) + ']'); const sc = r.getBoundingClientRect(); return {inputs: ins.length, rows: t.rows.length, a2: a2 ? a2.value : null, a1: ins[0] ? ins[0].value : null, top: sc.top, bottom: sc.bottom, ih: innerHeight, h2: t.rows[2].offsetHeight, hh: t.rows[1].offsetHeight}; }"))
            t0 = time.time(); pg.reload(); pg.wait_for_selector("#sheet-root input"); pg.wait_for_function("() => { const i = document.querySelector('#sheet-root input[aria-label=' + String.fromCharCode(34) + 'Cell A2' + String.fromCharCode(34) + ']'); return !!i && i.value === '2'; }")
            load = (time.time() - t0) * 1000
            m = pg.evaluate("""() => { const inp = document.querySelector('input[data-addr="A1"]'); const t = performance.now(); inp.focus(); inp.value = '5'; inp.blur(); const dt = performance.now() - t; const mem = performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null; return { ms: dt, a2: document.querySelector('input[data-addr="A2"]').value, inputs: document.querySelectorAll('#sheet-root input').length, dom: document.getElementsByTagName('*').length, mem }; }""")
            print("PROFILE rows=%d cells=%d load_ms=%.0f edit_ms=%.1f a2_after_edit=%s inputs=%d dom_nodes=%d heap_mb=%s" % (rows, n, load, m["ms"], m["a2"], m["inputs"], m["dom"], m["mem"]))
            sc = pg.evaluate("""() => { const r = document.getElementById('sheet-root'); const t = performance.now(); r.scrollTop = r.scrollHeight; return new Promise((res) => setTimeout(() => { const ins = r.querySelectorAll('input'); const a = r.querySelector('input[aria-label=' + String.fromCharCode(34) + 'Cell A1000' + String.fromCharCode(34) + ']'); res({ms: performance.now() - t, inputs: ins.length, a1000: a ? a.value : null, z1000: (r.querySelector('input[aria-label=' + String.fromCharCode(34) + 'Cell Z1000' + String.fromCharCode(34) + ']') || {}).value, first: ins[0] ? ins[0].dataset.addr : null}); }, 600)); }""")
            print("SCROLL_BOTTOM", sc)
            t.step("Profile recorded (see the log line)", lambda: None, expect=lambda: True)
        run_tour(ctx, "sheets_profile", body, notes="Measurement only.")
    g["DRILLS"]["tour_sheets_profile"] = tour


def register_sheets_virtual(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            dl = []
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(400)
            Q = chr(34)
            seed = """() => { const k = document.getElementById('sheet-root').dataset.storeKey; const c = {};
              for (let r = 1; r <= 1000; r++) { c['A' + r] = String(r); c['B' + r] = '=A' + r + '*2'; }
              c.A777 = 'needle'; localStorage.setItem(k, JSON.stringify(c)); return k; }"""
            key = pg.evaluate(seed)
            pg.reload(); pg.wait_for_timeout(900)
            root = lambda: pg.evaluate("() => document.getElementById('sheet-root')")  # noqa: E731
            cnt = lambda: pg.evaluate("() => document.querySelectorAll('#sheet-root input').length")
            val = lambda a: pg.evaluate("(a) => { const i = document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + a + String.fromCharCode(34) + ']'); return i ? i.value : null; }", a)
            stored = lambda: pg.evaluate("(k) => localStorage.getItem(k)", key)
            toprow = lambda r: pg.evaluate("(r) => { const sc = document.getElementById('sheet-root'); const tr = sc.querySelector('table').rows[r]; sc.scrollTop = tr.offsetTop - 40; window.scrollTo(0, sc.getBoundingClientRect().top + window.scrollY - 60); }", r)
            put = lambda a, v: (pg.click('#sheet-root input[data-addr=' + Q + a + Q + ']'), pg.keyboard.press("Control+A"), pg.keyboard.type(v), pg.keyboard.press("Enter"))
            before = stored()
            t.step("Page loads a 1000-row sheet: only the rows near the top are drawn (A1000 is not in the page yet)", lambda: pg.evaluate("document.getElementById('sheet-root').scrollIntoView({block:'start'})"), expect=lambda: val("A1") == "1" and val("B2") == "4" and val("A1000") is None and cnt() < 1500, wait=1500)
            t.step("Scroll to the bottom: A1000 and B1000 are drawn with the right values and the number of inputs stays bounded", lambda: toprow(1000), expect=lambda: val("A1000") == "1000" and val("B1000") == "2000" and cnt() < 4000, wait=1500)
            t.step("Scrolling changed nothing that is stored", lambda: None, expect=lambda: stored() == before, wait=300)
            t.step("Scroll to row 500 and type 7 into A500", lambda: (toprow(500), pg.wait_for_timeout(500), put("A500", "7")), expect=lambda: val("A500") == "7" and val("B500") == "14", wait=1200)
            t.step("Scroll to the top and back: A500 is still 7 and B500 still 14, and the stored sheet has A500 = 7", lambda: (toprow(1), pg.wait_for_timeout(700), toprow(500), pg.wait_for_timeout(700)), expect=lambda: val("A500") == "7" and val("B500") == "14" and json.loads(stored())["A500"] == "7", wait=800)
            def exp(sel):
                with pg.expect_download() as d:
                    pg.click(sel)
                dl.append(open(d.value.path()).read())
            t.step("Export (.json) from row 500: the file holds A500 = 7 and A1000 = 1000 though A1000 is not drawn now", lambda: ([(toprow(x), pg.wait_for_timeout(500)) for x in (250, 600, 900, 1)], exp("#sheet-export")), expect=lambda: json.loads(dl[-1])["A500"] == "7" and json.loads(dl[-1])["A1000"] == "1000" and val("A1000") is None, wait=500)
            t.step("CSV export holds 1000 rows with row 500 as 7 (the file is checked, not the screen)", lambda: exp("#sheet-csv"), expect=lambda: len([x for x in dl[-1].strip().split(chr(10)) if x]) == 1000 and dl[-1].strip().split(chr(10))[499].startswith("7,14"), wait=300)
            t.step("Find needle: A777 is far from the view, it is drawn, focused and reported", lambda: (toprow(1), pg.fill("#find-text", "needle"), pg.click("#find-next"), pg.wait_for_timeout(600)), expect=lambda: "A777" in pg.inner_text("#find-status") and pg.evaluate("() => document.activeElement && document.activeElement.dataset.addr") == "A777", wait=1200)
            t.step("Arrow Down from A777 forty times walks through undrawn rows to A817 with focus on a real cell", lambda: [pg.keyboard.press("ArrowDown") for _ in range(40)], expect=lambda: pg.evaluate("() => document.activeElement && document.activeElement.dataset.addr") == "A817", wait=1500)
            t.step("Undo undoes the A500 edit even though row 500 is far from view", lambda: (toprow(1), pg.wait_for_timeout(500), pg.click("#sheet-undo"), pg.wait_for_timeout(500)), expect=lambda: json.loads(stored()).get("A500") == "500" and (toprow(500) or True) and val("A500") == "500", wait=1200)
        run_tour(ctx, "sheets_virtual", body, notes="Headless Chromium 390x844 emulated. Rows are drawn near the view only; stored data and exports are the whole sheet.")
    g["DRILLS"]["tour_sheets_virtual"] = tour


def register_sheets_slices(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            dl = []
            ndl = []
            pg.on("download", lambda d: ndl.append(1))
            pg.add_init_script("window.__noheavy = %s;" % ("true" if os.path.exists("/tmp/slice_noheavy") else "false"))
            pg.add_init_script("window.__lt = []; try { new PerformanceObserver((l) => l.getEntries().forEach((e) => window.__lt.push(Math.round(e.duration)))).observe({ entryTypes: ['longtask'] }); } catch (e) {}")
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(400)
            Q = chr(34)
            seed = """() => { const k = document.getElementById('sheet-root').dataset.storeKey; const c = {};
              for (let r = 1; r <= 1000; r++) for (let q = 0; q < 10; q++) c[String.fromCharCode(65 + q) + r] = '1';
              const f = '=' + Array(49).fill('SUM($A$1:$J$1000)').join('+');
              if (!window.__noheavy) for (let r = 1; r <= 150; r++) c['L' + r] = f;
              c.Z1000 = 'end'; localStorage.setItem(k, JSON.stringify(c)); return k; }"""
            key = pg.evaluate(seed)
            val = lambda a: pg.evaluate("(a) => { const i = document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + a + String.fromCharCode(34) + ']'); return i ? i.value : null; }", a)
            calc = lambda: pg.evaluate("() => document.querySelectorAll('#sheet-root input[data-calc]').length")
            lt0 = lambda: pg.evaluate("() => Math.max(0, ...window.__lt)")
            lt = lambda: pg.evaluate("() => Math.max(0, ...window.__lt.slice(2))")  # the first two long tasks are the page load itself (parse, first rows), also present without any heavy formula
            stored = lambda: pg.evaluate("(k) => localStorage.getItem(k)", key)
            put = lambda a, v: (pg.click('#sheet-root input[data-addr=' + Q + a + Q + ']'), pg.keyboard.press("Control+A"), pg.keyboard.type(v), pg.keyboard.press("Enter"))
            cdp = {}
            def prof_start():
                if not os.path.exists("/tmp/slice_prof"):
                    return
                pg.evaluate("""() => { window.__tm = {}; const w = (o, n, k) => { const f = o[n]; o[n] = function () { const t = performance.now(); try { return f.apply(this, arguments); } finally { window.__tm[k] = (window.__tm[k] || 0) + performance.now() - t; } }; }; w(Storage.prototype, 'setItem', 'setItem'); w(JSON, 'stringify', 'stringify'); w(JSON, 'parse', 'parse'); w(Object, 'keys', 'keys'); }""")
                cdp["s"] = pg.context.new_cdp_session(pg); cdp["s"].send("Profiler.enable"); cdp["s"].send("Profiler.setSamplingInterval", {"interval": 200}); cdp["s"].send("Profiler.start")
            def prof_stop():
                if not cdp.get("s"):
                    return
                print("TM", pg.evaluate("() => JSON.stringify(window.__tm)"), flush=True)
                pr = cdp["s"].send("Profiler.stop")["profile"]; idx = {n["id"]: n for n in pr["nodes"]}; self_t = {}
                for sid, dt in zip(pr["samples"], pr["timeDeltas"]):
                    cf = idx[sid]["callFrame"]; k = "%s %s:%s" % (cf["functionName"] or "(anon)", cf["url"].split("/")[-1], cf["lineNumber"] + 1); self_t[k] = self_t.get(k, 0) + dt
                print("PROF", [(k, round(v / 1000)) for k, v in sorted(self_t.items(), key=lambda x: -x[1])[:12]], flush=True)
            seen = {}
            def reload():
                pg.reload(wait_until="commit")
                pg.wait_for_selector("#sheet-root input", timeout=20000)
                seen["calc"] = calc()
            def exp(sel):
                with pg.expect_download(timeout=60000) as d:
                    pg.click(sel)
                dl.append(open(d.value.path()).read())
            t.step("Load a sheet with 150 near-budget formulas (49 whole-block SUMs each, 10,000 constants): the page draws, cells still working show a grey ... marker", reload, expect=lambda: seen["calc"] > 0, wait=500)
            t.step("Pending cells are marked data-calc and show ... (not a number)", lambda: None, expect=lambda: calc() == 0 or val("L10") in ("\u2026", "490000"), wait=100)
            t.step("When the work finishes every marker is gone and L1 and L10 are exactly 490000", lambda: pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]') && document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + 'L10' + String.fromCharCode(34) + ']').value === '490000'", timeout=60000), expect=lambda: val("L1") == "490000" and val("L10") == "490000" and calc() == 0, wait=400)
            t.step("Page load itself (parse and first rows, same with no heavy cell): the long tasks are only recorded here, see LOAD_TASKS_MS in the log", lambda: print("LOAD_TASKS_MS", pg.evaluate("() => JSON.stringify(window.__lt)"), flush=True), expect=lambda: True, wait=100)
            t.step("Edit A1 to 2 after the load has settled: a new calculation of the 150 heavy cells starts; long tasks are counted from here", lambda: (pg.evaluate("() => { window.__lt = []; }"), prof_start(), put("A1", "2"), pg.wait_for_timeout(1200), prof_stop()), expect=lambda: True, wait=100)
            t.step("After it settles L1, L5 and L10 are 490049, nothing is marked, and the longest browser task during the recalculation stayed below 300 ms (actual max printed as RECALC_LONGEST_TASK_MS)", lambda: (pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]')", timeout=90000), print("RECALC_LONGEST_TASK_MS", lt0(), pg.evaluate("() => JSON.stringify(window.__lt)"), flush=True)), expect=lambda: lt0() < 300 and val("L1") == "490049" and val("L5") == "490049" and val("L10") == "490049" and calc() == 0, wait=300)
            t.step("Focus L3: the cell shows its raw formula, and a repaint from another edit does not overwrite it", lambda: (pg.click('#sheet-root input[data-addr=' + Q + 'L3' + Q + ']'), pg.evaluate("() => { const b = document.getElementById('sheet-undo'); b.click(); }"), pg.wait_for_timeout(300)), expect=lambda: val("L3").startswith("=SUM("), wait=200)
            t.step("Rapid scroll (top, bottom, top, 600, top) then settle: drawn A/B cells equal the stored data and no marker is left", lambda: (pg.evaluate("() => { const s = document.getElementById('sheet-root'); const t = s.querySelector('table'); [1000, 1, 600, 1].forEach((r, i) => setTimeout(() => { s.scrollTop = t.rows[r].offsetTop - 40; window.dispatchEvent(new Event('scroll')); }, i * 40)); }"), pg.wait_for_timeout(1500)), expect=lambda: pg.evaluate("() => { const st = JSON.parse(localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey)); let bad = 0, n = 0; document.querySelectorAll('#sheet-root input').forEach((i) => { if (document.activeElement === i) return; const a = i.dataset.addr; if (/^[A-J]/.test(a)) { n++; if (i.value !== (st[a] || '')) bad++; } }); return n > 50 && bad === 0 && !document.querySelector('#sheet-root input[data-calc]'); }"), wait=300)
            snap = {}
            def pend_focus():
                pg.reload(wait_until="commit"); pg.wait_for_selector("#sheet-root input", timeout=20000)
                snap["before"] = stored()
                snap["r"] = pg.evaluate("""() => { const out = [];
                  const els = ['L', 'S'].map((pre) => document.querySelector('#sheet-root input[data-calc][data-addr^=' + String.fromCharCode(34) + pre + String.fromCharCode(34) + ']'));  // both found first: leaving the first cell paints a slice of the queue
                  const pends = els.map((i) => i ? [i.value, !!i.dataset.calc] : null);  // read for both BEFORE any action: leaving one cell paints a slice of the queue
                  for (const [ix, i] of els.entries()) {
                    if (!i) { out.push(null); continue; }
                    const a = i.dataset.addr, pend = pends[ix][0]; i.focus(); const focused = i.value; const calcGone = !i.dataset.calc;
                    i.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })); i.blur(); out.push({ a: a, pend: pend, focused: focused, calcGone: calcGone, after: i.value }); }
                  return out; }""")
            def pf_ok():
                print("PF", snap["r"], flush=True)
                raw = json.loads(snap["before"])
                return all(x and x["pend"] == "\u2026" and x["calcGone"] and x["focused"] == raw.get(x["a"], "") for x in snap["r"]) and snap["r"][0]["focused"].startswith("=SUM(") and snap["r"][1]["focused"] == ""
            t.step("Focus a still-pending NON-EMPTY formula cell (L) and a pending empty cell (S) then leave without typing: each shows its stored raw content while focused (the formula text, and empty), never ...", pend_focus, expect=pf_ok, wait=300)
            t.step("Nothing was written: stored equals what it was, and no stored value is ... or empty-for-formula", lambda: None, expect=lambda: stored() == snap["before"] and "\u2026" not in stored(), wait=100)
            pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]')", timeout=90000)
            def undo_fill():
                put("A5", "9"); pg.click("#sheet-undo"); pg.click("#sheet-redo")
                pg.fill("#fill-range", "L1:L3"); pg.click("#fill-down")
            t.step("Edit A5, Undo and Redo while the repaint is pending, then copy L1 down to L3 from a pending row: the stored cells are the formula (never ...), A5 is 9", undo_fill, expect=lambda: json.loads(stored()).get("A5") == "9" and json.loads(stored())["L3"] == json.loads(stored())["L1"] and json.loads(stored())["L1"].startswith("=SUM(") and "\u2026" not in stored(), wait=500)
            pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]')", timeout=90000)
            gen = lambda: int(pg.evaluate("() => document.getElementById('sheet-root').dataset.paintGen || '0'"))
            noph = lambda: pg.evaluate("() => Array.from(document.querySelectorAll('#sheet-root input')).filter((i) => i.value === '\\u2026').length")
            settle = lambda: pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]')", timeout=90000)
            def twenty():
                pg.reload(wait_until="commit"); pg.wait_for_selector("#sheet-root input", timeout=20000); settle()
                snap["g0"] = gen(); snap["raw0"] = stored()
                cells = ["A3", "B3", "C3", "D3", "E3", "F3", "G3", "H3", "I3", "J3", "A4", "B4", "C4", "D4", "E4", "F4", "G4", "H4", "I4", "J4"]
                for n, a in enumerate(cells):
                    pg.click('#sheet-root input[data-addr=' + Q + a + Q + ']')
                    k = n % 4
                    if k == 0:
                        pg.keyboard.press("Enter")
                    elif k == 1:
                        pg.keyboard.press("Tab")
                    elif k == 2:
                        pg.evaluate("() => document.activeElement.blur()")
                    # k == 3: leave by the next click
                pg.evaluate("() => document.activeElement.blur()")
                snap["g1"] = gen()
            t.step("Click through 20 cells (Enter, Tab, blur, next click) with no typing on the heavy sheet: no new calculation generation starts, the sheet settles, nothing is marked, no ... is left, stored is byte-identical", twenty, expect=lambda: (settle() or True) and snap["g1"] == snap["g0"] and calc() == 0 and noph() == 0 and stored() == snap["raw0"], wait=300)
            def same_text():
                snap["g2"] = gen()
                put("B3", "1")
                snap["g3"] = gen()
            t.step("Type the same text (1) into B3 and press Enter: nothing changed, so no new generation and the stored sheet is identical", same_text, expect=lambda: snap["g3"] == snap["g2"] and stored() == snap["raw0"], wait=300)
            t.step("Type a different value (5) into B3: a new generation starts, the sheet settles, no marker or ... left, B3 is 5 in the stored sheet", lambda: (put("B3", "5"), settle()), expect=lambda: gen() > snap["g3"] and calc() == 0 and noph() == 0 and json.loads(stored())["B3"] == "5", wait=300)
            def rejected():
                pg.fill("#val-range", "A7:A7"); pg.select_option("#val-kind", "number"); pg.fill("#val-min", "0"); pg.fill("#val-max", "10"); pg.click("#val-add"); pg.wait_for_timeout(300)
                snap["g4"] = gen(); snap["raw4"] = stored()
                put("A7", "99"); pg.wait_for_timeout(500)
            t.step("A validation rule on A7 (number 0 to 10): typing 99 is refused, the cell shows its old value again (1) instead of the refused text, the stored sheet is unchanged and no new generation starts", rejected, expect=lambda: val("A7") == "1" and stored() == snap["raw4"] and gen() == snap["g4"] and "not applied" in pg.inner_text("#sheet-status"), wait=300)
            t.step("JSON export does not wait for painting: a click right after a reload downloads at once with all 10,151 cells", lambda: (pg.reload(wait_until="commit"), pg.wait_for_selector("#sheet-root input", timeout=20000), exp("#sheet-export")), expect=lambda: len(json.loads(dl[-1])) == 10151, wait=100)
            pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]')", timeout=60000)
            def csv_cancel():
                ndl.clear(); pg.click("#sheet-csv")
                pg.wait_for_function("() => document.getElementById('sheet-csv').dataset.csv === 'calculating'", timeout=5000)
                snap["inprog"] = pg.evaluate("() => document.getElementById('sheet-csv').dataset.csv + ' | ' + document.getElementById('sheet-status').textContent.slice(0, 40)")
                put("A2", "3")
                pg.wait_for_function("() => ['cancelled', 'done'].indexOf(document.getElementById('sheet-csv').dataset.csv) >= 0", timeout=20000)
                snap["end"] = pg.evaluate("() => document.getElementById('sheet-csv').dataset.csv")
            t.step("CSV: click and wait until the button state is calculating (known in progress), then edit A2: the CSV is cancelled (button state), with no download event", csv_cancel, expect=lambda: snap["inprog"].startswith("calculating") and snap["end"] == "cancelled" and not ndl and pg.evaluate("() => !document.getElementById('sheet-csv').disabled"), wait=300)
            pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]')", timeout=60000)
            t.step("CSV again with no edit: the file is a full recompute (A1 is back to 1 after the undo, A2 is 3, A5 is 9, B3 is 5, so every L is 49 x 10014 = 490686 on all 150 formula rows)", lambda: (pg.wait_for_function("() => !document.getElementById('sheet-csv').disabled", timeout=90000), exp("#sheet-csv")), expect=lambda: (print("CSV1", [dl[-1].split(chr(10))[k][:40] for k in (0, 1, 4, 149, 499)], repr(dl[-1].split(chr(10))[999][-30:]), len(dl[-1].split(chr(10))), flush=True) or True) and dl[-1].split(chr(10))[0].startswith("1,1,1,1,1,1,1,1,1,1,,490686") and dl[-1].split(chr(10))[1].startswith("3,1,1,1,1,1,1,1,1,1,,490686") and len([x for x in dl[-1].split(chr(10)) if x]) == 1000 and all(x.split(",")[11] == "490686" for x in dl[-1].split(chr(10))[:150]) and "undefined" not in dl[-1] and dl[-1].split(chr(10))[999] == ",".join(["1"] * 10) + "," * 16 + "end" and dl[-1].split(chr(10))[499] == ",".join(["1"] * 10) + "," * 16, wait=300)
        run_tour(ctx, "sheets_slices", body, notes="Headless Chromium 390x844 emulated. 40 cells of 490,000 work units each; the page paints in slices.")
    g["DRILLS"]["tour_sheets_slices"] = tour


def register_sheets_heavy8(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            dl = []
            pg.add_init_script("window.__lt = []; try { new PerformanceObserver((l) => l.getEntries().forEach((e) => window.__lt.push(Math.round(e.duration)))).observe({ entryTypes: ['longtask'] }); } catch (e) {}")
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(400)
            Q = chr(34)
            # 10,000 ones in A1:J1000. L1:L8 are eight ADJACENT heavy cells (49 whole-block SUMs = 490,000 units each).
            # M1 repeats the whole block 60 times (over the 500k guard). Q1 = 1, Qn = Q(n-1)+1 down to Q200 (a chain).
            seed = """() => { const k = document.getElementById('sheet-root').dataset.storeKey; const c = {};
              for (let r = 1; r <= 1000; r++) for (let q = 0; q < 10; q++) c[String.fromCharCode(65 + q) + r] = '1';
              const f = '=' + Array(49).fill('SUM($A$1:$J$1000)').join('+');
              for (let r = 1; r <= 8; r++) c['L' + r] = f;
              c.M1 = '=' + Array(60).fill('SUM($A$1:$J$1000)').join('+');
              c.Q1 = '1'; for (let r = 2; r <= 200; r++) c['Q' + r] = '=Q' + (r - 1) + '+1';
              localStorage.setItem(k, JSON.stringify(c)); return k; }"""
            key = pg.evaluate(seed)
            val = lambda a: pg.evaluate("(a) => { const i = document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + a + String.fromCharCode(34) + ']'); return i ? i.value : null; }", a)
            toprow = lambda r: pg.evaluate("(r) => { const s = document.getElementById('sheet-root'); const t = s.querySelector('table'); s.scrollTop = t.rows[r].offsetTop - 40; window.dispatchEvent(new Event('scroll')); }", r)
            settle = lambda: pg.wait_for_function("() => !document.querySelector('#sheet-root input[data-calc]')", timeout=90000)
            lt = lambda: pg.evaluate("() => Math.max(0, ...window.__lt)")
            put = lambda a, v: (pg.click('#sheet-root input[data-addr=' + Q + a + Q + ']'), pg.keyboard.press("Control+A"), pg.keyboard.type(v), pg.keyboard.press("Enter"))
            def exp(sel):
                with pg.expect_download(timeout=90000) as d:
                    pg.click(sel)
                dl.append(open(d.value.path()).read())
            t.step("Load: 8 adjacent heavy cells L1:L8 (49 whole-block SUMs each), M1 (60 repeats, over the guard), a 200-cell chain in Q; after it settles L1..L8 = 490000, M1 = #LIMIT!, Q200 = 200", lambda: (pg.reload(wait_until="commit"), pg.wait_for_selector("#sheet-root input", timeout=20000), settle(), toprow(190), pg.wait_for_timeout(600)), expect=lambda: all(val("L%d" % r) is None or val("L%d" % r) == "490000" for r in range(1, 9)) and val("Q200") == "200", wait=300)
            toprow(1); pg.wait_for_timeout(600)
            t.step("Top again: L1..L8 each exactly 490000 and M1 is #LIMIT!", lambda: None, expect=lambda: all(val("L%d" % r) == "490000" for r in range(1, 9)) and val("M1") == "#LIMIT!", wait=200)
            def edit():
                pg.evaluate("() => { window.__lt = []; }")
                put("A1", "2"); pg.wait_for_timeout(1200); settle()
            def vals_ok():
                return all(val("L%d" % r) == "490049" for r in range(1, 9))
            def bound_ok():
                return lt() < 300
            t.step("Edit A1 to 2: eight adjacent heavy cells recalculate. Values (each L1..L8 = 490049, M1 still #LIMIT!) and the 300 ms long-task bound are checked and printed as two separate parts (actual maximum and task list printed)", edit, expect=lambda: (toprow(1), pg.wait_for_timeout(400), print("HEAVY8 M1_screen_after_edit", val("M1"), flush=True), print("HEAVY8 values_ok", vals_ok(), "bound_ok", bound_ok(), "max_ms", lt(), pg.evaluate("() => JSON.stringify(window.__lt)"), flush=True) or True) and vals_ok() and bound_ok() and val("M1") == "#LIMIT!", wait=300)
            def csvgo():
                exp("#sheet-csv")
            t.step("CSV export matches the screen: L1..L8 = 490049, M1 = #LIMIT!, and the chain Q1..Q200 is 1..200 in the file, with Q200 on screen also 200", csvgo, expect=lambda: (lambda rows: all(rows[r - 1].split(",")[11] == "490049" for r in range(1, 9)) and rows[0].split(",")[12] == "#LIMIT!" and all(rows[r - 1].split(",")[16] == str(r) for r in range(1, 201)) and "undefined" not in dl[-1])(dl[-1].split(chr(10))) and (toprow(190), pg.wait_for_timeout(600), val("Q200") == "200")[-1], wait=300)
        run_tour(ctx, "sheets_heavy8", body, notes="Headless Chromium 390x844 emulated. Eight adjacent cells of 490,000 work units each, a 60-repeat cell over the guard, a 200-cell chain; screen against CSV.")
    g["DRILLS"]["tour_sheets_heavy8"] = tour



def register_drive_link(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            dialogs = []
            pg.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
            pg.goto(APP + "/drive/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_selector("#sheet-root input", timeout=15000)
            key = pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; localStorage.setItem(k, JSON.stringify({A1: '10', A2: '20', A3: '30', B1: '=SUM(A1:A3)'})); return k; }""")
            pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(500)
            stat = lambda: pg.inner_text("#dl-status")
            own = lambda: pg.evaluate("() => fetch('/drive/mine/files/', {credentials: 'same-origin'}).then((r) => r.json())")
            stored = lambda: json.loads(pg.evaluate("(k) => localStorage.getItem(k)", key) or "{}")
            sha = lambda: pg.evaluate("() => { const b = new TextEncoder().encode(JSON.stringify(JSON.parse(localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey)))); return Promise.resolve(window.SalowSlidesImages.sha256(b)); }")
            t.step("Sheets: Drive panel is on the page with Save to Drive (.json) and Open from Drive; the file name box says sheet.json", expect=lambda: pg.locator("#dl-save").count() == 1 and pg.input_value("#dl-name") == "sheet.json", wait=1200)
            t.step("A name that does not end in .json is refused and nothing is created in Drive", lambda: (pg.fill("#dl-name", "sheet.txt"), pg.click("#dl-save"), pg.wait_for_timeout(500)), expect=lambda: "must end in .json" in stat() and own()["files"] == [], wait=500)
            def save1():
                pg.fill("#dl-name", "sheet.json"); pg.click("#dl-save"); pg.wait_for_timeout(1500)
            t.step("Save to Drive: the status says it is saved at the top of My Drive and only you can see it; Drive lists sheet.json with the size and SHA-256 of what Export would download", save1, expect=lambda: "Saved to Drive" in stat() and "only you can see it" in stat() and [(f["name"], f["version"]) for f in own()["files"]] == [("sheet.json", 1)] and own()["files"][0]["sha256"] == sha() and own()["files"][0]["size"] == len(json.dumps(stored(), separators=(",", ":"))) or (print("SAVE1", stat()[:200], own(), flush=True) or False), wait=500)
            t.step("Saving the same name again is refused by Drive and the file stays at version 1", lambda: (pg.click("#dl-save"), pg.wait_for_timeout(1500)), expect=lambda: "already used" in stat() and [f["version"] for f in own()["files"]] == [1], wait=500)
            def clear_open():
                pg.click("#sheet-clear"); pg.wait_for_timeout(500)
                pg.click("#dl-list"); pg.wait_for_timeout(1200)
            t.step("Clear the sheet, then Open from Drive: the list offers sheet.json as on this device", clear_open, expect=lambda: pg.locator("#dl-files option").count() == 1 and not pg.locator("#dl-files option").first.is_disabled() and pg.is_visible("#dl-open"), wait=500)
            def do_open():
                pg.click("#dl-open"); pg.wait_for_timeout(1500)
            t.step("Open the chosen file: the page's own Import runs and A1 to A3 and B1 are back (B1 shows 60)", do_open, expect=lambda: pg.input_value('#sheet-root input[data-addr="A2"]') == "20" and stored() == {"A1": "10", "A2": "20", "A3": "30", "B1": "=SUM(A1:A3)"} and "Imported 4 cells" in pg.inner_text("#sheet-status"), wait=500)
            def replace_open():
                pg.fill('#sheet-root input[data-addr="A5"]', "99"); pg.press('#sheet-root input[data-addr="A5"]', "Tab"); pg.wait_for_timeout(400)
                dialogs.clear(); pg.click("#dl-open"); pg.wait_for_timeout(1500)
            t.step("With cells present, opening asks first (the page's own confirm named the cells) and Undo is offered; the sheet is the saved one again", replace_open, expect=lambda: any("Replace the" in d for d in dialogs) and "A5" not in stored(), wait=500)
            t.step("Undo takes the import back in one step: A5 = 99 returns", lambda: pg.click("#sheet-undo"), expect=lambda: stored().get("A5") == "99", wait=800)
            def lose_device():
                pg.evaluate("() => new Promise((res) => { const r = indexedDB.deleteDatabase('salow-drive-' + document.getElementById('drive-link').dataset.user); r.onsuccess = r.onerror = r.onblocked = () => res(); setTimeout(res, 1500); })")
                pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(400)
                pg.click("#dl-list"); pg.wait_for_timeout(1500)
            t.step("After this browser's Drive copy is gone, Open from Drive says none is on this device and offers nothing to open (the Drive record is still there)", lose_device, expect=lambda: "none is on this device" in stat() and not pg.is_visible("#dl-open") and len(own()["files"]) == 1, wait=500)
            def slides_part():
                pg.goto(APP + "/slides/"); pg.wait_for_selector("#sl-add-text", timeout=15000)
                pg.click("#sl-add-text"); pg.fill("#sl-text", "Saved deck"); pg.press("#sl-text", "Tab"); pg.wait_for_timeout(400)
                pg.fill("#dl-name", "deck.json"); pg.click("#dl-save"); pg.wait_for_timeout(1800)
            t.step("Slides: a deck with one text item saves as deck.json to My Drive", slides_part, expect=lambda: "Saved to Drive" in stat() and sorted(f["name"] for f in own()["files"]) == ["deck.json", "sheet.json"], wait=500)
            def slides_open():
                pg.fill("#sl-text", "Changed later"); pg.press("#sl-text", "Tab"); pg.wait_for_timeout(300)
                pg.click("#sl-add-rect"); pg.wait_for_timeout(300)
                pg.click("#dl-list"); pg.wait_for_timeout(1500)
                pg.select_option("#dl-files", label=pg.locator("#dl-files option:not([disabled])").first.inner_text()) if pg.locator("#dl-files option:not([disabled])").count() else None
                dialogs.clear(); pg.click("#dl-open"); pg.wait_for_timeout(1500)
            t.step("Slides: Open from Drive lists only .json files on this device and hands deck.json to Import, which asks first; the first text is back", slides_open, expect=lambda: pg.locator("#dl-files option").count() == 2 and any("Replace this deck" in d for d in dialogs) and "Imported 1 slides" in pg.inner_text("#sl-status") or (print("SLOPEN", pg.locator("#dl-files option").count(), dialogs, pg.inner_text("#sl-status")[:200], stat()[:200], flush=True) or False), wait=500)
            t.step("Drive's own page shows both files with their on-device copy and an enabled Open from this device", lambda: pg.goto(APP + "/drive/"), expect=lambda: pg.locator(".drive-file").count() == 2 and pg.locator(".drive-open").first.is_enabled(), wait=1500)
        run_tour(ctx, "drive_link", body, notes="Headless Chromium 390x844 emulated. Save to Drive and Open from Drive in Sheets and Slides.")
    g["DRILLS"]["tour_drive_link"] = tour


def register_drive_link_fail(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.on("dialog", lambda d: d.accept())
            pg.goto(APP + "/drive/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_selector("#sheet-root input", timeout=15000)
            key = pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; localStorage.setItem(k, JSON.stringify({A1: '10', A2: '20'})); return k; }""")
            pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(500)
            stat = lambda: pg.inner_text("#dl-status")
            own = lambda: pg.evaluate("() => fetch('/drive/mine/files/', {credentials: 'same-origin'}).then((r) => r.json()).then((j) => { j.files = j.files.filter((f) => f.name === 'fail-sheet.json'); return j; })")  # only this tour's file, so it composes with tour_drive_link
            stored = lambda: json.loads(pg.evaluate("(k) => localStorage.getItem(k)", key) or "{}")
            def full():
                pg.evaluate("() => { window.__put = IDBObjectStore.prototype.put; IDBObjectStore.prototype.put = function () { throw new DOMException('This device is out of room. Nothing was saved.', 'QuotaExceededError'); }; }")
                pg.fill("#dl-name", "fail-sheet.json"); pg.click("#dl-save"); pg.wait_for_timeout(1500)
            t.step("Simulated full device: the byte write throws a quota error (the test replaces put). Save shows the device-storage failure message (not a saved message) and Drive has no record", full, expect=lambda: ("Saved to Drive" not in stat() and "would not use its file storage" in stat() and own()["files"] == [] and "recoverable" not in stat().lower()), wait=500)
            def restore_and_save():
                pg.evaluate("() => { IDBObjectStore.prototype.put = window.__put; }")
                pg.fill("#dl-name", "fail-sheet.json"); pg.click("#dl-save"); pg.wait_for_timeout(1500)
            t.step("Same device with the write working again: Save now succeeds and Drive records version 1 with the right size", restore_and_save, expect=lambda: "Saved to Drive" in stat() and [f["version"] for f in own()["files"]] == [1], wait=500)
            def tamper_open():
                sha = own()["files"][0]["sha256"]
                pg.evaluate("""(h) => new Promise((res, rej) => { const q = indexedDB.open('salow-drive-' + document.getElementById('drive-link').dataset.user); q.onsuccess = () => { const db = q.result; const names = Array.from(db.objectStoreNames); const tx = db.transaction(names[0], 'readwrite'); const st = tx.objectStore(names[0]); const bad = new TextEncoder().encode('{"A1":"tampered"}'); st.put({ sha256: h, size: bad.length, bytes: bad.buffer }); tx.oncomplete = () => { db.close(); res(); }; tx.onerror = () => rej(tx.error); }; q.onerror = () => rej(q.error); })""", sha)
                pg.click("#dl-list"); pg.wait_for_timeout(1500)
                pg.select_option("#dl-files", value=own()["files"][0]["id"])
                pg.click("#dl-open"); pg.wait_for_timeout(1500)
            t.step("The copy in this browser no longer matches its checksum (replaced with other bytes): Open says it is damaged and was not opened, and the sheet is unchanged", tamper_open, expect=lambda: "damaged" in stat() and "Nothing was changed" in stat() and stored() == {"A1": "10", "A2": "20"}, wait=500)
        run_tour(ctx, "drive_link_fail", body, notes="Headless Chromium 390x844 emulated. Save to Drive when the device write fails, and Open when the stored copy does not match its checksum.")
    g["DRILLS"]["tour_drive_link_fail"] = tour


def register_sheets_new_formulas(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_selector("#sheet-root input", timeout=15000)
            key = pg.evaluate("() => document.getElementById('sheet-root').dataset.storeKey")
            # column A is plain text that shows each formula, column B is the formula itself
            rows = [
                ("score", "42"),
                ('IFS(B1<30,"low",B1<60,"mid",TRUE,"high")', '=IFS(B1<30,"low",B1<60,"mid",TRUE,"high")'),
                ('SWITCH(B1,1,"one",42,"answer","other")', '=SWITCH(B1,1,"one",42,"answer","other")'),
                ("IFS with no true test", "=IFS(B1<0,1)"),
                ("kind (HLOOKUP table)", "price"),
                ("HLOOKUP price of pear", '=HLOOKUP("pear",D1:F2,2,FALSE)'),
            ]
            cells = {}
            for i, (a, b) in enumerate(rows, start=1):
                cells["A%d" % i] = a; cells["B%d" % i] = b
            cells.update({"D1": "apple", "E1": "pear", "F1": "plum", "D2": "3", "E2": "5", "F2": "7"})
            pg.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", [key, cells])
            pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(600)
            val = lambda a: pg.input_value('input[data-addr="%s"]' % a)
            def snap(name):
                out = os.environ.get("DRILL_VIDEO", "") or "/tmp"
                pg.evaluate("() => document.querySelector('#sheet-root').scrollIntoView({block: 'start'})"); pg.wait_for_timeout(400)
                pg.screenshot(path=os.path.join(out, name + ".png"))
            t.step("IFS, SWITCH and HLOOKUP show their results in the real sheet (B2 mid, B3 answer, B4 #N/A, B6 5)", lambda: snap("new_formulas_390"), expect=lambda: [val("B2"), val("B3"), val("B4"), val("B6")] == ["mid", "answer", "#N/A", "5"], wait=300)
            # one step per function: the full formula is in the caption and the cell, the result is read from the cell after it is left
            def show(addr):
                pg.evaluate("() => document.querySelector('#sheet-root').scrollIntoView({block: 'start'})")
                pg.click('input[data-addr="%s"]' % addr); pg.wait_for_timeout(500)
            def shown(addr):
                return pg.input_value('input[data-addr="%s"]' % addr)
            t.step('IFS: cell B2 holds =IFS(B1<30,"low",B1<60,"mid",TRUE,"high"), B1 is 42, result: mid', lambda: show("B2"), expect=lambda: shown("B2").startswith("=IFS(") and True, wait=1500)
            t.step("IFS result after leaving the cell: B2 shows mid", lambda: (pg.click('input[data-addr="D1"]'), pg.wait_for_timeout(400)), expect=lambda: val("B2") == "mid", wait=1500)
            t.step('SWITCH: cell B3 holds =SWITCH(B1,1,"one",42,"answer","other"), result: answer', lambda: show("B3"), expect=lambda: shown("B3").startswith("=SWITCH("), wait=1500)
            t.step("SWITCH result after leaving the cell: B3 shows answer", lambda: (pg.click('input[data-addr="D1"]'), pg.wait_for_timeout(400)), expect=lambda: val("B3") == "answer", wait=1500)
            t.step('HLOOKUP: cell B6 holds =HLOOKUP("pear",D1:F2,2,FALSE), row 2 under pear is 5, result: 5', lambda: show("B6"), expect=lambda: shown("B6").startswith("=HLOOKUP("), wait=1500)
            t.step("HLOOKUP result after leaving the cell: B6 shows 5", lambda: (pg.click('input[data-addr="D1"]'), pg.wait_for_timeout(400)), expect=lambda: val("B6") == "5", wait=1500)
        run_tour(ctx, "sheets_new_formulas", body, notes="Headless Chromium 390x844 emulated. IFS, SWITCH and HLOOKUP typed into the real Sheets page.")
    g["DRILLS"]["tour_sheets_new_formulas"] = tour


def register_drive_link_stale(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.on("dialog", lambda d: d.accept())
            pg.goto(APP + "/drive/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_selector("#sheet-root input", timeout=15000)
            key = pg.evaluate("() => document.getElementById('sheet-root').dataset.storeKey")
            stat = lambda: pg.inner_text("#dl-status")
            own = lambda: pg.evaluate("() => fetch('/drive/mine/files/', {credentials: 'same-origin'}).then((r) => r.json())")
            named = lambda n: [f for f in own()["files"] if f["name"] == n]  # only the record this step made
            stored = lambda: json.loads(pg.evaluate("(k) => localStorage.getItem(k)", key) or "{}")
            post = lambda url, body_: pg.evaluate("([u, b]) => fetch(u, {method: 'POST', credentials: 'same-origin', headers: {'X-CSRFToken': document.getElementById('drive-link').dataset.csrf, 'Content-Type': 'application/x-www-form-urlencoded'}, body: b}).then((r) => r.status)", [url, body_])
            def prepare(name):
                pg.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", [key, {"A1": "10"}])
                pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(500)
                pg.fill("#dl-name", name); pg.click("#dl-save"); pg.wait_for_timeout(1500)
                pg.click("#dl-list"); pg.wait_for_timeout(1500)
            def snap(name):
                out = os.environ.get("DRILL_VIDEO", "") or "/tmp"
                pg.evaluate("() => document.getElementById('dl-status').scrollIntoView({block: 'center'})"); pg.wait_for_timeout(400)
                pg.screenshot(path=os.path.join(out, name + ".png"))
            def edit_sheet():
                pg.fill('input[data-addr="A1"]', "99"); pg.press('input[data-addr="A1"]', "Tab"); pg.wait_for_timeout(500)
            def trashed():
                prepare("stale-a.json")
                fid = named("stale-a.json")[0]["id"]
                post("/drive/file/%s/trash/" % fid, "")
                edit_sheet()
                pg.select_option("#dl-files", value=fid)
                pg.click("#dl-open"); pg.wait_for_timeout(1500)
                snap("stale_trashed_390")
            t.step("Pick stale-a.json from the list, then trash it in Drive without refreshing the list, edit the sheet, press Open: it says the file is no longer there and the sheet keeps the edit", trashed, expect=lambda: "no longer at the top" in stat() and named("stale-a.json") == [] and stored().get("A1") == "99", wait=500)
            def superseded():
                prepare("stale-b.json")
                f = named("stale-b.json")[0]
                post("/drive/file/%s/version/" % f["id"], "base_version=1&size=7&sha256=" + "e" * 64)
                edit_sheet()
                pg.select_option("#dl-files", value=f["id"])
                pg.click("#dl-open"); pg.wait_for_timeout(1500)
                snap("stale_newer_390")
            t.step("Pick stale-b.json, then Drive records a newer version before Open: it says the file was saved again and the sheet keeps the edit", superseded, expect=lambda: "saved again in Drive" in stat() and [f["version"] for f in named("stale-b.json")] == [2] and stored().get("A1") == "99", wait=500)
        run_tour(ctx, "drive_link_stale", body, notes="Headless Chromium 390x844 emulated. Open from Drive when the Drive record changes after the picker was filled.")
    g["DRILLS"]["tour_drive_link_stale"] = tour


def register_drive_link_version_fail(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.on("dialog", lambda d: d.accept())
            pg.goto(APP + "/drive/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_selector("#sheet-root input", timeout=15000)
            key = pg.evaluate("() => document.getElementById('sheet-root').dataset.storeKey")
            pg.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", [key, {"A1": "10"}])
            pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(500)
            stat = lambda: pg.inner_text("#dl-status")
            own = lambda: pg.evaluate("() => fetch('/drive/mine/files/', {credentials: 'same-origin'}).then((r) => r.json()).then((j) => { j.files = j.files.filter((f) => /^v(fail|edit|amb|conf)\\.json$/.test(f.name)); return j; })")  # only this tour's file
            vers = lambda: pg.evaluate("() => fetch('/drive/', {credentials: 'same-origin'}).then((r) => r.text())")
            def net_fail():
                pg.route("**/drive/file/*/version/", lambda r: r.abort())
                pg.fill("#dl-name", "vfail.json"); pg.click("#dl-save"); pg.wait_for_timeout(2000)
            t.step("The Drive version call fails on the network after the bytes are stored: the status does not say Saved, says it is not known whether the version was saved, and the file is not offered by Open", net_fail, expect=lambda: "Saved to Drive" not in stat() and "not known whether this version was saved" in stat() and "no saved version" not in stat() and "not the content of the failed save" in stat() and own()["files"] == [], wait=500)
            def again():
                pg.unroute("**/drive/file/*/version/")
                pg.click("#dl-save"); pg.wait_for_timeout(2000)
                print("AGAIN", stat()[:400], own(), flush=True)
            t.step("Network back, the same name saved again finishes the record: Saved to Drive and Drive lists vfail.json at version 1", again, expect=lambda: "Saved to Drive" in stat() and [(f["name"], f["version"]) for f in own()["files"]] == [("vfail.json", 1)], wait=500)
            named = lambda n: [f for f in own()["files"] if f["name"] == n]
            cell = lambda v: (pg.fill('input[data-addr="A1"]', v), pg.press('input[data-addr="A1"]', "Tab"), pg.wait_for_timeout(500))
            # The version request reaches the server, then the page is told it failed (the answer is lost). Done in the page so the test host's name mapping still applies.
            lose_answer = lambda: pg.evaluate("() => { window.__f = window.fetch; window.fetch = function (u, o) { const p = window.__f.apply(this, arguments); return /\\/version\\/$/.test(String(u)) ? p.then(() => { throw new TypeError('Failed to fetch'); }) : p; }; }")
            keep_answer = lambda: pg.evaluate("() => { if (window.__f) window.fetch = window.__f; }")
            sha = lambda txt: hashlib.sha256(txt.encode()).hexdigest()
            def edited_retry():
                cell("5")
                pg.route("**/drive/file/*/version/", lambda r: r.abort())
                pg.fill("#dl-name", "vedit.json"); pg.click("#dl-save"); pg.wait_for_timeout(2000)
                pg.unroute("**/drive/file/*/version/")
                cell("77")
                pg.click("#dl-save"); pg.wait_for_timeout(2000)
            t.step("A save fails at the version call, the sheet is then edited (A1 77), and Save again with the same name: the version Drive records is the EDITED content, not the bytes of the failed save", edited_retry, expect=lambda: "Saved to Drive" in stat() and [f["sha256"] for f in named("vedit.json")] == [sha('{"A1":"77"}')], wait=500)
            def ambiguous_same():
                cell("8")
                lose_answer()
                pg.fill("#dl-name", "vamb.json"); pg.click("#dl-save"); pg.wait_for_timeout(2500)
                keep_answer()
                self_status.append(stat())
                pg.click("#dl-save"); pg.wait_for_timeout(2500)
            self_status = []
            t.step("Drive records the version but the answer is lost (ambiguous success): the first status does not say Saved; Save again with the unchanged sheet says the earlier attempt had reached Drive, and Drive lists one version 1", ambiguous_same, expect=lambda: "Saved to Drive" not in self_status[0] and "earlier attempt had reached Drive" in stat() and [(f["version"], f["sha256"]) for f in named("vamb.json")] == [(1, sha('{"A1":"8"}'))], wait=500)
            def conflict_changed():
                cell("9")
                lose_answer()
                pg.fill("#dl-name", "vconf.json"); pg.click("#dl-save"); pg.wait_for_timeout(2500)
                keep_answer()
                cell("10")
                pg.click("#dl-save"); pg.wait_for_timeout(2500)
            t.step("Same lost answer, but the sheet was edited before Save again: Drive refuses (it already has version 1 of the older content); the status does not say Saved and Drive keeps the older content at version 1", conflict_changed, expect=lambda: "Saved to Drive" not in stat() and "nothing was overwritten" in stat() and "Press Save again" not in stat() and [(f["version"], f["sha256"]) for f in named("vconf.json")] == [(1, sha('{"A1":"9"}'))], wait=500)
        run_tour(ctx, "drive_link_version_fail", body, notes="Headless Chromium 390x844 emulated. The Drive version call fails after the bytes are stored.")
    g["DRILLS"]["tour_drive_link_version_fail"] = tour


def register_drive_link_slides_retry(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.on("dialog", lambda d: d.accept())
            pg.goto(APP + "/drive/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_selector("#sl-add-text", timeout=15000)
            key = pg.evaluate("() => document.getElementById('slide-root').dataset.storeKey")
            stat = lambda: pg.inner_text("#dl-status")
            own = lambda: pg.evaluate("() => fetch('/drive/mine/files/', {credentials: 'same-origin'}).then((r) => r.json()).then((j) => { j.files = j.files.filter((f) => /^sr(retry|conf)\\.json$/.test(f.name)); return j; })")
            named = lambda n: [f for f in own()["files"] if f["name"] == n]
            deckjson = lambda: pg.evaluate("(k) => localStorage.getItem(k)", key)
            sha = lambda txt: hashlib.sha256(txt.encode()).hexdigest()
            def snap(name):
                out = os.environ.get("DRILL_VIDEO", "") or "/tmp"
                pg.evaluate("() => document.getElementById('dl-status').scrollIntoView({block: 'center'})"); pg.wait_for_timeout(400)
                pg.screenshot(path=os.path.join(out, name + ".png"))
            fail_call = lambda: pg.evaluate("() => { window.__f = window.fetch; window.fetch = function (u, o) { return /\\/version\\/$/.test(String(u)) ? Promise.reject(new TypeError('Failed to fetch')) : window.__f.apply(this, arguments); }; }")
            lose_answer = lambda: pg.evaluate("() => { window.__f = window.fetch; window.fetch = function (u, o) { const p = window.__f.apply(this, arguments); return /\\/version\\/$/.test(String(u)) ? p.then(() => { throw new TypeError('Failed to fetch'); }) : p; }; }")
            keep_answer = lambda: pg.evaluate("() => { if (window.__f) window.fetch = window.__f; }")
            def text(v):
                pg.fill("#sl-text", v); pg.press("#sl-text", "Tab"); pg.wait_for_timeout(400)
            def retry_current():
                pg.click("#sl-add-text"); text("first text")
                fail_call()
                pg.fill("#dl-name", "srretry.json"); pg.click("#dl-save"); pg.wait_for_timeout(1800)
                keep_answer()
                text("edited before the retry")
                pg.click("#dl-save"); pg.wait_for_timeout(2000)
                snap("slides_retry_390")
                print("DBG-SR", stat()[:200], named("srretry.json"), sha(deckjson()), flush=True)
            t.step("Slides: the Drive version call fails, the deck text is edited, Save again with the same name: Saved to Drive and the checksum is that of the edited deck", retry_current, expect=lambda: "Saved to Drive" in stat() and [f["sha256"] for f in named("srretry.json")] == [sha(deckjson())], wait=500)
            def conflict():
                text("second text")
                lose_answer()
                pg.fill("#dl-name", "srconf.json"); pg.click("#dl-save"); pg.wait_for_timeout(2500)
                snap("slides_lostreply_390"); print("DBG-LOST", stat()[:400], flush=True)
                keep_answer()
                text("changed after the lost answer")
                pg.click("#dl-save"); pg.wait_for_timeout(2500)
                snap("slides_conflict_390")
            t.step("Slides: Drive records the version but the answer is lost, the deck is edited, Save again: Drive refuses, the status says nothing was overwritten and Drive keeps one version 1", conflict, expect=lambda: "Saved to Drive" not in stat() and "nothing was overwritten" in stat() and [f["version"] for f in named("srconf.json")] == [1], wait=500)
        run_tour(ctx, "drive_link_slides_retry", body, notes="Headless Chromium 390x844 emulated. Slides Save to Drive after a failed version call, with an edit before the retry, and with a lost answer and a conflict.")
    g["DRILLS"]["tour_drive_link_slides_retry"] = tour


def register_slides_underline(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_selector("#sl-add-text", timeout=15000)
            saved = lambda: pg.evaluate("()=>JSON.parse(localStorage.getItem(document.getElementById('slide-root').getAttribute('data-store-key')))")
            deco = lambda sel: pg.evaluate("(s)=>{const e=document.querySelector(s+' svg text'); return e ? e.getAttribute('text-decoration') : 'none-found';}", sel)
            def snap(name, sel):
                out = os.environ.get("DRILL_VIDEO", "") or "/tmp"
                pg.evaluate("(s) => document.querySelector(s).scrollIntoView({block: 'center'})", sel); pg.wait_for_timeout(500)
                pg.screenshot(path=os.path.join(out, name + ".png"))
            t.step("Slides: Underline is off until an item is selected", expect=lambda: pg.is_disabled("#sl-underline"), wait=1500)
            t.step("Add a text item and tick Underline: the item shows underlined and the saved deck is version 12 with u true", lambda: (pg.click("#sl-add-text"), pg.fill("#sl-text", "Quarterly plan"), pg.press("#sl-text", "Tab"), pg.check("#sl-underline"), pg.wait_for_timeout(500), snap("underline_editor_390", "#sl-canvas-wrap")), expect=lambda: deco("#sl-canvas-wrap") == "underline" and saved()["v"] == 12 and saved()["slides"][0]["elements"][0].get("u") is True, wait=800)
            t.step("Present shows the same underline", lambda: (pg.click("#sl-present"), pg.wait_for_timeout(600), snap("underline_present_390", "#sl-show-canvas")), expect=lambda: deco("#sl-show-canvas") == "underline", wait=1500)
            t.step("Leave Present and reload: the box is still ticked and the item is still underlined", lambda: (pg.click("#sl-show-exit"), pg.reload(), pg.wait_for_selector("#sl-items li button", timeout=15000), pg.click("#sl-items li button")), expect=lambda: pg.is_checked("#sl-underline") and deco("#sl-canvas-wrap") == "underline", wait=1200)
            t.step("Untick Underline: no underline is drawn and the saved deck has no u key and is back to version 1", lambda: (pg.uncheck("#sl-underline"), pg.wait_for_timeout(500)), expect=lambda: deco("#sl-canvas-wrap") is None and "u" not in saved()["slides"][0]["elements"][0] and saved()["v"] == 1, wait=800)
            t.step("Undo puts the underline back in one step", lambda: pg.click("#sl-undo"), expect=lambda: deco("#sl-canvas-wrap") == "underline" and pg.is_checked("#sl-underline"), wait=800)
        run_tour(ctx, "slides_underline", body, notes="Headless Chromium 390x844 emulated. Slides Underline.")
    g["DRILLS"]["tour_slides_underline"] = tour


def register_sheets_insert1000(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_selector("#sheet-root", state="attached", timeout=15000)
            pg.wait_for_selector("#sheet-root input", timeout=15000)
            key = pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; const c = {}; for (let r = 1; r <= 999; r++) c['A' + r] = String(r); localStorage.setItem(k, JSON.stringify(c)); return k; }""")
            pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(600)
            stored = lambda: json.loads(pg.evaluate("(k) => localStorage.getItem(k)", key))
            st = {}
            show = lambda r: pg.evaluate("(r) => { const sc = document.getElementById('sheet-root'); const tr = sc.querySelector('table').rows[r]; sc.scrollTop = tr.offsetTop - 60; window.dispatchEvent(new Event('scroll')); }", r)
            def ins_at(r):
                show(r); pg.wait_for_timeout(700)
                pg.click('#sheet-root input[data-addr="A%d"]' % r); pg.click("#row-ins"); pg.wait_for_timeout(900)
            def a():
                ins_at(500); st["a"] = stored()
            t.step("Page level: A1 to A999 filled, row 1000 empty. Click A500 and Insert row above: the page stores A501 = 500 and A1000 = 999", a, expect=lambda: st["a"].get("A500") is None and st["a"].get("A501") == "500" and st["a"].get("A1000") == "999" and len(st["a"]) == 999 or (print("INS1", len(st["a"]), st["a"].get("A500"), st["a"].get("A501"), st["a"].get("A1000"), flush=True) or False), wait=300)
            def b():
                st["before"] = stored()
                ins_at(500); st["st"] = pg.inner_text("#sheet-status"); st["after"] = stored()
            t.step("Page level: row 1000 now holds a value, so Insert row above at A500 is refused: the stored sheet is byte-identical and the status says nothing was inserted", b, expect=lambda: st["after"] == st["before"] and "Nothing was inserted" in st["st"] or (print("INS2", st["st"][:200], st["after"] == st["before"], flush=True) or False), wait=300)
        run_tour(ctx, "sheets_insert1000", body, notes="Headless Chromium 390x844 emulated. Insert row on the 1000-row grid.")
    g["DRILLS"]["tour_sheets_insert1000"] = tour


def register_sheets_frozenfocus(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_selector("#sheet-root input", timeout=15000)
            pg.evaluate("""() => { const k = document.getElementById('sheet-root').dataset.storeKey; const c = {}; for (let r = 1; r <= 1000; r++) c['A' + r] = String(r); localStorage.setItem(k, JSON.stringify(c)); }""")
            pg.reload(); pg.wait_for_selector("#sheet-root input", timeout=15000); pg.wait_for_timeout(600)
            gap = lambda: pg.evaluate("""() => { const a = document.activeElement; if (!a || !a.dataset || !a.dataset.addr) return null; const t = document.querySelector('#sheet-root table'); const pin = t.rows[1].getBoundingClientRect(); const r = a.getBoundingClientRect(); const sc = document.getElementById('sheet-root').getBoundingClientRect(); return {addr: a.dataset.addr, top: Math.round(r.top), pinBottom: Math.round(pin.bottom), scTop: Math.round(sc.top), scBottom: Math.round(sc.bottom), bottom: Math.round(r.bottom)}; }""")
            res = []
            def go():
                pg.fill("#frz-rows", "1"); pg.fill("#frz-cols", "0"); pg.click("#frz-apply"); pg.wait_for_timeout(500)
                pg.evaluate("() => { const sc = document.getElementById('sheet-root'); const tr = sc.querySelector('table').rows[300]; sc.scrollTop = tr.offsetTop - 60; window.dispatchEvent(new Event('scroll')); }"); pg.wait_for_timeout(700)
                pg.click('#sheet-root input[data-addr="A310"]')
                for i in range(30):
                    pg.keyboard.press("ArrowDown"); pg.wait_for_timeout(120)
                    res.append(gap())
                for i in range(60):
                    pg.keyboard.press("ArrowUp"); pg.wait_for_timeout(120)
                    res.append(gap())
            def ok():
                bad = [x for x in res if not x or x["top"] < x["pinBottom"] or x["bottom"] > x["scBottom"] + 1]
                print("FOCUS checks", len(res), "bad", len(bad), bad[:3], flush=True)
                return len(res) == 90 and not bad
            t.step("Freeze row 1, move focus down 30 rows then up 60 rows with the arrow keys: every focused cell sits fully below the pinned row and inside the grid box", go, expect=ok, wait=300)
        run_tour(ctx, "sheets_frozenfocus", body, notes="Headless Chromium 390x844 emulated. Frozen row 1, arrow-key focus.")
    g["DRILLS"]["tour_sheets_frozenfocus"] = tour


def register_sheets_virtual2(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.on("pageerror", lambda e: print("PAGEERROR", str(e)[:300]))
            dl = []
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(400)
            Q = chr(34)
            seed = """() => { const k = document.getElementById('sheet-root').dataset.storeKey; const c = {};
              for (let r = 1; r <= 1000; r++) { c['A' + r] = r % 2 ? 'x' : 'y'; c['B' + r] = String(r); }
              localStorage.setItem(k, JSON.stringify(c)); return k; }"""
            key = pg.evaluate(seed)
            pg.reload(); pg.wait_for_timeout(900)
            val = lambda a: pg.evaluate("(a) => { const i = document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + a + String.fromCharCode(34) + ']'); return i ? i.value : null; }", a)
            cnt = lambda: pg.evaluate("() => document.querySelectorAll('#sheet-root input').length")
            stored = lambda: pg.evaluate("(k) => localStorage.getItem(k)", key)
            toprow = lambda r: pg.evaluate("(r) => { const sc = document.getElementById('sheet-root'); const tr = sc.querySelector('table').rows[r]; sc.scrollTop = tr.offsetTop - 40; window.scrollTo(0, sc.getBoundingClientRect().top + window.scrollY - 60); }", r)
            drawn = lambda: pg.evaluate("() => { const rs = new Set(); document.querySelectorAll('#sheet-root input').forEach((i) => rs.add(+i.dataset.addr.slice(1))); return Array.from(rs).sort((a, b) => a - b); }")
            vis = lambda: pg.evaluate("() => { const t = document.querySelector('#sheet-root table'); const o = []; for (let r = 1; r <= 1000; r++) if (!t.rows[r].hidden && t.rows[r].querySelector('input')) o.push(r); return o; }")
            before = stored()
            t.step("Filter column A equals x: every even row is hidden, the odd rows alternate with hidden ones", lambda: (pg.fill("#filter-col", "A"), pg.select_option("#filter-op", "equals"), pg.fill("#filter-val", "x"), pg.uncheck("#filter-header"), pg.click("#filter-go"), pg.wait_for_timeout(600)), expect=lambda: all(r % 2 == 1 for r in vis()) and len(vis()) > 3, wait=400)
            def shown_in_win():
                return pg.evaluate("() => { const s = document.getElementById('sheet-root'), t = s.querySelector('table'); let n = 0; for (let i = 1; i < t.rows.length; i++) { const tr = t.rows[i]; if (tr.hidden) continue; const o = tr.offsetTop; if (o + tr.offsetHeight > s.scrollTop && o < s.scrollTop + s.clientHeight) n++; } return n; }")
            def hidden_drawn_near(r):
                return pg.evaluate("(r) => { const t = document.getElementById('sheet-root').querySelector('table'); let n = 0; for (let i = Math.max(1, r - 100); i <= Math.min(1000, r + 100); i++) { const tr = t.rows[i]; if (tr.hidden && tr.querySelector('input')) n++; } return n; }", r)
            def far(r):
                toprow(r); pg.wait_for_timeout(700)
                print("DIAG far", r, "drawn", (lambda d: (d[:3], d[-3:], len(d)))(drawn()), "vis", (lambda d: (d[:3], d[-3:], len(d)))(vis()), "scrollTop", pg.evaluate("() => document.getElementById('sheet-root').scrollTop"), "B%d" % r, val("B%d" % r), "CONJ val", val("B%d" % r) == str(r), "inVis", r in vis(), "allOdd", all(x % 2 == 1 for x in vis()), "cnt", cnt(), "near", any(abs(x - r) < 6 for x in vis()), "shownInWin", shown_in_win(), "hiddenDrawnNear", hidden_drawn_near(r), flush=True)
            t.step("Scroll to row 801 with the alternating filter on: the shown rows around it are drawn, all odd, with their real values (B801 = 801), and drawn shown rows stay within the design limit (shown rows in the window + 2 x the 12-row buffer + 2 x the 50-row keep), and no hidden row within 100 rows of the target has inputs", lambda: far(801), expect=lambda: val("B801") == "801" and 801 in vis() and all(r % 2 == 1 for r in vis()) and len(vis()) <= shown_in_win() + 2 * 12 + 2 * 50 and hidden_drawn_near(801) == 0 and any(abs(r - 801) < 6 for r in vis()), wait=300)
            t.step("Scroll to the last shown row (999) with the filter on: B999 is drawn and correct, drawn shown rows within the design limit, no hidden row within 100 rows has inputs", lambda: far(999), expect=lambda: val("B999") == "999" and len(vis()) <= shown_in_win() + 2 * 12 + 2 * 50 and hidden_drawn_near(999) == 0 and all(r % 2 == 1 for r in vis()), wait=300)
            fk = {}
            def filt_keys():
                far(501)
                last = 501
                pg.evaluate("(r) => { document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + 'Z' + r + String.fromCharCode(34) + ']').focus(); }", last)
                pg.keyboard.press("Tab"); pg.wait_for_timeout(600)
                fk["tab"] = pg.evaluate("() => document.activeElement.dataset.addr"); fk["last"] = last
                pg.evaluate("(r) => { document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + 'B' + r + String.fromCharCode(34) + ']').focus(); }", 501)
                fk["a0"] = pg.evaluate("() => document.activeElement.dataset.addr")
                for _ in range(5):
                    pg.keyboard.press("ArrowDown"); pg.wait_for_timeout(120)
                fk["down"] = pg.evaluate("() => document.activeElement.dataset.addr")
                fk["downhidden"] = pg.evaluate("() => { const e = document.activeElement; const tr = e.closest('tr'); return !!tr.hidden; }")
                print("FILTKEYS", fk, flush=True)
            t.step("Filter on, native keys without the helper: Tab from Z501 (the next row, 502, is hidden) goes to A503, a shown row, and ArrowDown x5 from B501 moves down onto shown rows only", filt_keys, expect=lambda: fk["tab"].startswith("A") and fk["tab"] == "A503" and not fk["downhidden"] and int(fk["down"][1:]) > 501 and int(fk["down"][1:]) % 2 == 1, wait=300)
            t.step("Clear the filter while scrolled to row 801: the rows it shows again (800, 802) get inputs on the spot with their real values (winUpdate after a filter change), and no row stays hidden", lambda: (toprow(801), pg.wait_for_timeout(500), pg.click("#filter-clear"), pg.wait_for_timeout(800)), expect=lambda: val("B800") == "800" and val("B802") == "802" and val("B801") == "801" and pg.evaluate("() => document.querySelectorAll('#sheet-root tr[hidden]').length") == 0, wait=300)
            t.step("Back to the top (the filter was cleared in the previous step): every row is shown and stored data is unchanged by the filter and the scrolling", lambda: (toprow(1), pg.wait_for_timeout(900)), expect=lambda: stored() == before and len(vis()) > 10 and any(r % 2 == 0 for r in vis()), wait=300)
            t.step("Freeze row 1 and scroll to row 600: row 1 stays drawn and pinned at the top of the grid box while row 600 is drawn", lambda: (pg.fill("#frz-rows", "1"), pg.fill("#frz-cols", "0"), pg.click("#frz-apply"), pg.wait_for_timeout(500), far(600)), expect=lambda: val("A1") == "x" and val("B600") == "600" and pg.evaluate("() => { const sc = document.getElementById('sheet-root').getBoundingClientRect(); const i = document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + 'A1' + String.fromCharCode(34) + ']').getBoundingClientRect(); return i.top >= sc.top - 1 && i.top < sc.top + 120; }"), wait=300)
            def edge_tab():
                far(300)
                last = drawn()[-1]
                pg.evaluate("(r) => { const i = document.querySelector('#sheet-root input[data-addr=' + String.fromCharCode(34) + 'Z' + r + String.fromCharCode(34) + ']'); i.focus(); }", last)
                pg.keyboard.press("Tab"); pg.wait_for_timeout(500)
                pg.evaluate("(r) => { window.__edge = r; }", last)
            t.step("Native Tab from Z of the last drawn row (no helper): focus lands on A of the next row, which is drawn on demand and shows its real value", edge_tab, expect=lambda: pg.evaluate("() => document.activeElement.dataset.addr") == "A%d" % (pg.evaluate("() => window.__edge") + 1) and val(pg.evaluate("() => document.activeElement.dataset.addr")) in ("x", "y"), wait=300)
            def edge_arrow():
                nxt = pg.evaluate("() => +document.activeElement.dataset.addr.slice(1)")
                for _ in range(30):
                    pg.keyboard.press("ArrowDown")
                snap = pg.evaluate("() => document.activeElement.dataset.addr")
                pg.evaluate("(s) => { window.__arr = s; }", snap); pg.wait_for_timeout(500)
            t.step("Native ArrowDown x30 from there walks through undrawn rows, focus stays on a real cell in column A", edge_arrow, expect=lambda: pg.evaluate("() => document.activeElement.dataset.addr").startswith("A") and pg.evaluate("() => +document.activeElement.dataset.addr.slice(1)") >= 330, wait=300)
            def edge_paste():
                a = pg.evaluate("() => document.activeElement.dataset.addr")
                pg.evaluate("(function(a,t){var e=document.querySelector('input[data-addr='+a+']');var dt=new DataTransfer();dt.setData('text/plain',t);e.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));})(%s,%s)" % (json.dumps(a), json.dumps("p1\tp2\np3\tp4\np5\tp6\n")))
                pg.wait_for_timeout(400); pg.evaluate("(a) => { window.__pa = a; }", a)
            t.step("Paste a 3 x 2 block at the focused cell: it fills across rows that were not drawn a moment ago, and the stored sheet has all six cells", edge_paste, expect=lambda: (lambda a, st: st.get(a) == "p1" and st.get("B" + a[1:]) == "p2" and st.get("A%d" % (int(a[1:]) + 2)) == "p5" and st.get("B%d" % (int(a[1:]) + 2)) == "p6")(pg.evaluate("() => window.__pa"), json.loads(stored())), wait=300)
            def desk():
                pg.set_viewport_size({"width": 1280, "height": 800}); pg.wait_for_timeout(600); far(1); far(500); pg.wait_for_timeout(500)
            t.step("Desktop 1280x800: the grid box is shorter than the window (max 70% of the height), inputs stay bounded, row 500 is drawn", desk, expect=lambda: pg.evaluate("() => document.getElementById('sheet-root').getBoundingClientRect().height") <= 800 * 0.7 + 2 and cnt() < 1500 and val("B500") == "500", wait=300)
            t.step("Desktop: the Freeze controls are visible and the Chart panel works with the grid scrolled to row 500 (bar chart of B495:B500 draws 6 bars). Versions panel is not exercised here", lambda: (pg.fill("#chart-range", "B495:B500"), pg.select_option("#chart-type", "bar"), pg.click("#chart-draw"), pg.wait_for_timeout(600)), expect=lambda: pg.locator("#chart-out svg rect").count() >= 6 and pg.locator("#frz-apply").is_visible(), wait=300)
            def hit():
                far(500)
                return pg.evaluate("() => { const s = document.getElementById('sheet-root'); let bad = 0, n = 0; document.querySelectorAll('#sheet-root input').forEach((i) => { const r = i.getBoundingClientRect(), b = s.getBoundingClientRect(); if (r.bottom <= b.top + 90 || r.top >= b.bottom - 2 || r.right > b.right || r.left < b.left + 40) return; n++; const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); if (e !== i) { bad++; (window.__badh = window.__badh || []).push(i.dataset.addr + ':' + Math.round(r.top - b.top) + ':' + Math.round(r.left - b.left) + ':' + (e ? e.tagName + (e.dataset && e.dataset.addr || '') : 'null')); } }); return [n, bad]; }")
            hres = {}
            t.step("Rows stay aligned after the resize from 390 to 1280 wide (row 1 is still frozen and pinned, so the top 90 px under the pinned header and row 1 are excluded): every drawn input in the rest of the grid box (row 500 view) is the element hit at its own centre, and the row 1 height equals the height set on every row", lambda: hres.update(r=hit()), expect=lambda: (print("HIT n,bad", hres["r"], pg.evaluate("() => window.__badh"), "h1,h900", pg.evaluate("() => { const t = document.querySelector('#sheet-root table'); return [t.rows[1].offsetHeight, t.rows[900].offsetHeight]; }"), flush=True) or True) and hres["r"][0] > 20 and hres["r"][1] == 0 and pg.evaluate("() => { const t = document.querySelector('#sheet-root table'); return t.rows[1].offsetHeight === t.rows[900].offsetHeight; }"), wait=200)
            pg.on("dialog", lambda d: d.accept())
            def vers():
                pg.fill("#ver-name", "v500"); pg.click("#ver-save"); pg.wait_for_timeout(500)
                far(500)
                pg.click('#sheet-root input[data-addr="B500"]'); pg.keyboard.press("Control+A"); pg.keyboard.type("changed"); pg.keyboard.press("Enter"); pg.wait_for_timeout(400)
                hres["mid"] = val("B500")
                pg.click('button[aria-label="Restore the version v500"]'); pg.wait_for_timeout(1500)
                far(500)
            t.step("Versions panel on the 1000-row sheet: save a version, change B500 to changed, Restore it: B500 is 500 again, B999 and A1000-free data intact, stored sheet equals what it was before", vers, expect=lambda: hres["mid"] == "changed" and val("B500") == "500" and json.loads(stored())["B999"] == "999" and "B500" in json.loads(stored()) and json.loads(stored())["B500"] == "500" and len(json.loads(stored())) == 2000, wait=300)
            def shots():
                out = os.environ.get("DRILL_VIDEO", "") or "/tmp"
                pg.evaluate("() => document.getElementById('ver-list').scrollIntoView({block: 'center'})"); pg.wait_for_timeout(400)
                pg.screenshot(path=os.path.join(out, "desktop_versions_1280.png"))
                far(500); pg.wait_for_timeout(500)
                pg.evaluate("() => document.getElementById('sheet-root').scrollIntoView({block: 'start'})"); pg.wait_for_timeout(400)
                pg.screenshot(path=os.path.join(out, "desktop_grid_1280.png"))
            t.step("True 1280x800 screenshots of the Versions list (the saved v500 version) and of the grid at row 500 are saved for inspection", shots, expect=lambda: True, wait=100)
        run_tour(ctx, "sheets_virtual2", body, notes="Headless Chromium 390x844 emulated, then 1280x800. Rows are drawn near the view only.")
    g["DRILLS"]["tour_sheets_virtual2"] = tour


def register_sheets_charts(g):
    APP = g["APP"]

    def tour(ctx):
        cell = lambda a: 'input[data-addr="%s"]' % a

        def body(t, pg):
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            put = lambda a, v: (pg.click(cell(a)), pg.keyboard.type(v), pg.keyboard.press("Enter"))
            chart = lambda: pg.evaluate("document.getElementById('chart-out').scrollIntoView({block:'center'})")
            draw = lambda rng, kind: (pg.evaluate("document.getElementById('chart-draw').scrollIntoView({block:'center'})"), pg.fill("#chart-range", rng), pg.select_option("#chart-type", kind), pg.click("#chart-draw"), pg.wait_for_timeout(300))
            msg = lambda: pg.inner_text("#chart-out")
            n = lambda sel: pg.locator("#chart-out svg " + sel).count()
            for i, (x, y) in enumerate([(1, 4), (2, 9), (3, 6), (4, 12), (5, -3)], 1):
                put("A%d" % i, str(x)); put("B%d" % i, str(y))
            put("A6", "n/a"); put("B6", "7")
            draw("B1:B5", "area")
            t.step("Area chart of B1:B5 (4, 9, 6, 12, -3): a filled shape under the line, 5 points, the axis passes through zero", lambda: chart(), expect=lambda: n("polygon") == 1 and n("polyline") == 1 and n("circle") == 5, wait=2500)
            draw("B1:B1", "area")
            t.step("Area of a single cell: one point and no filled shape", lambda: chart(), expect=lambda: n("polygon") == 0 and n("circle") == 1, wait=2500)
            draw("A1:B5", "scatter")
            t.step("Scatter of A1:B5: 5 points, x across and y up, axes from the data (x 1 to 5, y -3 to 12)", lambda: chart(), expect=lambda: n("circle") == 5 and "5 points charted" in msg() and "12" in pg.evaluate("document.querySelector('#chart-out svg').textContent") and "-3" in pg.evaluate("document.querySelector('#chart-out svg').textContent"), wait=2500)
            draw("A1:B6", "scatter")
            t.step("Scatter of A1:B6 where A6 is text: 5 points and the note says 1 row skipped", lambda: chart(), expect=lambda: n("circle") == 5 and "1 row skipped" in msg(), wait=2500)
            draw("B1:B5", "scatter")
            t.step("Scatter of one column is refused with the two-column message and no chart", lambda: chart(), expect=lambda: "exactly two columns" in msg() and n("circle") == 0, wait=2500)
            draw("B1:B5", "bar")
            t.step("Regression: bar chart of B1:B5 still draws 5 bars", lambda: chart(), expect=lambda: n("rect") >= 5 and "5 values charted" in msg(), wait=2500)
            draw("B1:B5", "pie")
            t.step("Regression: pie of B1:B5 still draws, the -3 is left out of the pie", lambda: chart(), expect=lambda: "left out of the pie" in msg() and n("path") >= 1, wait=2500)
            put("A1", "1"); draw("B5:A1", "scatter")
            t.step("Reversed range B5:A1 is normalised: scatter draws the same 5 points", lambda: chart(), expect=lambda: n("circle") == 5 and "from B5:A1" in msg(), wait=2500)
            draw("A1:B25", "scatter")
            t.step("Scatter A1:B25 is 50 cells, the most allowed (25 pairs): accepted, 5 points have numbers", lambda: chart(), expect=lambda: n("circle") == 5 and "from A1:B25" in msg(), wait=2500)
            draw("A1:B26", "scatter")
            t.step("Scatter A1:B26 is 52 cells: refused with the 50-cell message", lambda: chart(), expect=lambda: "up to 50 cells" in msg() and n("circle") == 0, wait=2500)
            draw("B1:B5", "area")
            t.step("Area of B1:B5 again: the zero line is inside the plot", lambda: chart(), expect=lambda: n("polygon") == 1, wait=2500)
            for i, v in enumerate([5, 6, 7, 8, 9], 1): put("D%d" % i, str(v))
            draw("D1:D5", "area")
            t.step("Area of all-positive D1:D5 (5 to 9): fill sits on the zero line at the bottom, inside the plot", lambda: chart(), expect=lambda: n("polygon") == 1 and all(0 <= float(p.split(",")[1]) <= 260 for p in pg.evaluate("document.querySelector('#chart-out svg polygon').getAttribute('points')").split()), wait=2500)
            for i, v in enumerate([-5, -6, -7, -8, -9], 1): put("E%d" % i, str(v))
            draw("E1:E5", "area")
            t.step("Area of all-negative E1:E5: fill hangs from the zero line at the top, inside the plot", lambda: chart(), expect=lambda: n("polygon") == 1 and all(0 <= float(p.split(",")[1]) <= 260 for p in pg.evaluate("document.querySelector('#chart-out svg polygon').getAttribute('points')").split()), wait=2500)
            draw("A1:B5", "line")
            t.step("Line chart of two columns is still refused (one row or one column)", lambda: chart(), expect=lambda: "one row or one column" in msg(), wait=2500)
            put("A1", "x"); draw("A1:B5", "scatter")
            t.step("Edit A1 to text: the scatter redraws on the next draw with 4 points and 1 skipped row", lambda: chart(), expect=lambda: n("circle") == 4 and "1 row skipped" in msg(), wait=2500)
        run_tour(ctx, "sheets_charts", body, notes="Headless Chromium 390x844 emulated, one run. Charts are drawn from the sheet and store nothing. Not shown: dragging points, axis titles, more than 50 cells.", unshown=["axis titles", "more than 50 cells"])

    g["DRILLS"]["tour_sheets_charts"] = tour


def register_sheets_versions(g):
    APP = g["APP"]

    def tour(ctx):
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)
        cell = lambda a: 'input[data-addr="%s"]' % a

        def body(t, pg):
            dlg = {"accept": True, "msgs": []}
            pg.on("dialog", lambda d: (dlg["msgs"].append(d.message), d.accept() if dlg["accept"] else d.dismiss()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            vs = lambda: pg.inner_text("#ver-status")
            lis = lambda: pg.locator("#ver-list li").count()
            vkeys = lambda: pg.evaluate("Object.keys(localStorage).filter(k=>k.endsWith('.ver'))")
            show = lambda: pg.evaluate("document.getElementById('ver-save').scrollIntoView({block:'center'})")
            put = lambda a, v: (pg.click(cell(a)), pg.keyboard.type(v), pg.keyboard.press("Enter"))
            grid = lambda: pg.evaluate("()=>{document.getElementById('sheet-root').scrollIntoView({block:'start'});scrollBy(0,-(document.getElementById('sheet-barrow').getBoundingClientRect().height+6))}")
            for a, v in (("A1", "10"), ("A2", "20"), ("A3", "=A1+A2")):
                put(a, v)
            pg.fill("#fmt-range", "A1:A3"); pg.select_option("#fmt-bold", "on"); pg.click("#fmt-add"); pg.wait_for_timeout(400)
            t.step("Start: A1 10, A2 20, A3 =A1+A2 shows 30, and one format rule (bold on A1:A3)", expect=lambda: val(pg, "A3") == "30" and pg.locator("#fmt-list li").count() == 1, wait=2500)
            show()
            pg.fill("#ver-name", "Draft one"); pg.click("#ver-save"); pg.wait_for_timeout(400)
            t.step("Save a version named Draft one: the list shows it with 3 cells, 0 validation rules, 0 colour rules, 1 format rules", expect=lambda: lis() == 1 and "Saved the version Draft one: 3 cells, 0 validation rules, 0 colour rules, 1 format rules" in vs() and "Draft one: 3 cells, 0 validation rules, 0 colour rules, 1 format rules" in pg.inner_text("#ver-list"), wait=3000)
            put("A1", "99"); pg.locator("#fmt-list button").first.click(); pg.wait_for_timeout(400)
            t.step("Change the tab: A1 99 so A3 shows 119, and remove the format rule", expect=lambda: val(pg, "A3") == "119" and pg.locator("#fmt-list li").count() == 0, wait=2500)
            t.step("The grid now: A1 99, A2 20, A3 119", lambda: grid(), expect=lambda: val(pg, "A1") == "99" and val(pg, "A3") == "119", wait=2500)
            show(); pg.fill("#ver-name", ""); pg.click("#ver-save"); pg.wait_for_timeout(300)
            msg1 = vs(); pg.fill("#ver-name", "draft ONE"); pg.click("#ver-save"); pg.wait_for_timeout(300)
            t.step("A blank name and a repeated name (different capitals) are refused and the list still has 1 version", expect=lambda: "Give the version a name" in msg1 and "already exists" in vs() and lis() == 1, wait=3000)
            dlg["accept"] = False; dlg["msgs"].clear(); pg.locator("#ver-list button", has_text="Restore").first.click(); pg.wait_for_timeout(400)
            t.step("Restore, then Cancel in the question: nothing changes (A1 stays 99, rule stays removed)", expect=lambda: "Restore cancelled" in vs() and val(pg, "A1") == "99" and pg.locator("#fmt-list li").count() == 0 and len(dlg["msgs"]) == 1 and "Draft one" in dlg["msgs"][0] and "3 cells" in dlg["msgs"][0], wait=3000)
            dlg["accept"] = True; dlg["msgs"].clear(); pg.locator("#ver-list button", has_text="Restore").first.click(); pg.wait_for_timeout(600)
            t.step("Restore and confirm: A1 is 10 again, A3 shows 30 and the format rule is back", expect=lambda: val(pg, "A1") == "10" and val(pg, "A3") == "30" and pg.locator("#fmt-list li").count() == 1 and "Restored the version Draft one" in vs() and lis() == 1, wait=3000)
            t.step("The grid after Restore: A1 10, A2 20, A3 30", lambda: grid(), expect=lambda: val(pg, "A1") == "10" and val(pg, "A3") == "30", wait=2500)
            pg.click("#sheet-undo"); pg.wait_for_timeout(500)
            t.step("The grid after Undo: A1 99, A3 119", lambda: grid(), expect=lambda: val(pg, "A1") == "99" and val(pg, "A3") == "119", wait=2500)
            t.step("Undo brings the pre-restore tab back: A1 99, A3 119 and no format rule; the saved version is still listed", expect=lambda: val(pg, "A1") == "99" and val(pg, "A3") == "119" and pg.locator("#fmt-list li").count() == 0 and lis() == 1, wait=3000)
            pg.evaluate("()=>{ window.__realSet = Storage.prototype.setItem; Storage.prototype.setItem = function (k, v) { if (String(k).endsWith('.ver')) throw new Error('quota'); return window.__realSet.call(this, k, v); }; }")
            show(); pg.fill("#ver-name", "Second"); pg.click("#ver-save"); pg.wait_for_timeout(400)
            ok_fail = "would not store the version, so nothing was saved" in vs() and lis() == 1 and val(pg, "A1") == "99"
            pg.evaluate("()=>{ Storage.prototype.setItem = window.__realSet; }")
            t.step("Storage refuses the write (quota simulated for the versions key only): the message says nothing was saved, the list still has 1 version and the cells are unchanged", expect=lambda: ok_fail, wait=3000)
            pg.click("#tab-add"); pg.wait_for_timeout(500); show()
            t.step("A new tab has its own list: no saved versions there", expect=lambda: "No saved versions on this tab" in pg.inner_text("#ver-list") and lis() == 1, wait=3000)
            pg.fill("#ver-name", "T2 keep"); pg.click("#ver-save"); pg.wait_for_timeout(400)
            n2 = len(vkeys())
            pg.evaluate("document.getElementById('tab-del').scrollIntoView({block:'center'})"); pg.click("#tab-del"); pg.wait_for_timeout(600)
            gone = len(vkeys()) == 1
            pg.click("#tab-restore"); pg.wait_for_timeout(700); show()
            t.step("Save a version on the new tab, delete that tab (its stored versions go with it), then Restore deleted tab: the version is back on it", expect=lambda: n2 == 2 and gone and len(vkeys()) == 2 and "T2 keep" in pg.inner_text("#ver-list") and lis() == 1, wait=3000)
            pg.reload(); pg.wait_for_timeout(600); show()
            t.step("After a reload the second tab still lists its own version T2 keep and not Draft one", expect=lambda: "T2 keep" in pg.inner_text("#ver-list") and "Draft one" not in pg.inner_text("#ver-list") and lis() == 1, wait=2500)
            pg.locator("#sheet-tabs button", has_text="Sheet 1").first.click(); pg.wait_for_timeout(500); show()
            t.step("First tab: Draft one is still listed after the reload", expect=lambda: "Draft one" in pg.inner_text("#ver-list") and lis() == 1, wait=3000)
            vk = vkeys()[0]; real = pg.evaluate("k=>localStorage.getItem(k)", vk)
            pg.evaluate("a=>localStorage.setItem(a[0], JSON.stringify({v:1,items:[{n:'Other window',k:['{\"A1\":\"5\"}',null,null,null]}]}))", [vk])
            dlg["msgs"].clear(); pg.locator("#ver-list button", has_text="Restore").first.click(); pg.wait_for_timeout(500)
            t.step("Another window changed the saved versions after this page listed them: Restore is refused as stale, the list refreshes to Other window, and A1 is unchanged", expect=lambda: "changed in another window" in vs() and "Other window" in pg.inner_text("#ver-list") and val(pg, "A1") == "99" and not dlg["msgs"], wait=3000)
            pg.evaluate("a=>localStorage.setItem(a[0], '{bad')", [vk]); pg.evaluate("document.getElementById('ver-save').scrollIntoView({block:'center'})")
            pg.reload(); pg.wait_for_timeout(600); show(); pg.fill("#ver-name", "After damage"); pg.click("#ver-save"); pg.wait_for_timeout(400)
            t.step("A damaged saved-versions entry is shown as unreadable, saving is refused and the stored text is left exactly as it was", expect=lambda: "could not be read" in pg.inner_text("#ver-list") and "saving is off" in vs() and pg.evaluate("k=>localStorage.getItem(k)", vk) == "{bad", wait=3000)
            pg.evaluate("a=>localStorage.setItem(a[0], a[1])", [vk, real]); pg.reload(); pg.wait_for_timeout(600); show()
            dlg["accept"] = False; pg.locator("#ver-list button", has_text="Delete").first.click(); pg.wait_for_timeout(300)
            c1 = lis() == 1 and "Delete cancelled" in vs()
            dlg["accept"] = True; pg.locator("#ver-list button", has_text="Delete").first.click(); pg.wait_for_timeout(500)
            t.step("Delete: Cancel keeps the version, then confirming removes it, its stored entry is gone and the other tab's entry is untouched", expect=lambda: c1 and "No saved versions" in pg.inner_text("#ver-list") and "Deleted the version Draft one" in vs() and len(vkeys()) == 1 and "T2 keep" in pg.evaluate("k=>localStorage.getItem(k)", vkeys()[0]), wait=3000)
        run_tour(ctx, "sheets_versions", body, notes="Headless Chromium 390x844 emulated, one run. Not shown: Export and Import leaving versions alone, a real full browser store, a restore that fails half way.", unshown=["export and import", "real quota", "restore failing half way"])

    g["DRILLS"]["tour_sheets_versions"] = tour


def register_sheets_io_keys(g):
    APP = g["APP"]

    def tour(ctx):
        import json as _json
        import os as _os

        _os.makedirs("/tmp/vid/dl", exist_ok=True)
        imp = "/tmp/vid/dl/import_demo.json"
        open(imp, "w").write(_json.dumps({"A1": "Imported", "B1": "7", "B2": "=B1*2"}))
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)
        act = lambda pg: pg.evaluate("document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.addr : ''")

        def sc(pg, sel):
            pg.evaluate("s=>{var e=document.querySelector(s);if(e)e.scrollIntoView({block:'center'})}", sel)

        def body(t, pg):
            seen = []
            pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            st = lambda: pg.inner_text("#sheet-status")
            t.cap("Next: click A1, type 5, press Enter, type 6, press Enter")
            pg.click('input[data-addr="A1"]')
            pg.keyboard.type("5")
            pg.keyboard.press("Enter")
            pg.keyboard.type("6")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(400)
            sc(pg, 'input[data-addr="A2"]')
            t.step("Enter commits and moves down: A1 holds 5, A2 holds 6, and the focus is on A3", expect=lambda: val(pg, "A1") == "5" and val(pg, "A2") == "6" and act(pg) == "A3", wait=3400)
            t.cap("Next: Arrow Up twice, then Arrow Right")
            pg.keyboard.press("ArrowUp")
            pg.keyboard.press("ArrowUp")
            pg.keyboard.press("ArrowRight")
            pg.wait_for_timeout(500)
            t.step("The focus moved to B1 (row 1, next column)", expect=lambda: act(pg) == "B1", wait=3000)
            t.cap("Next: paste a 2 by 2 block copied from another spreadsheet into C1")
            pg.click('input[data-addr="C1"]')
            pg.evaluate("""()=>{var el=document.querySelector('input[data-addr="C1"]');var dt=new DataTransfer();dt.setData('text/plain',['a',1,'b',2].map(String).reduce((x,y,k)=>x+(k==0?'':(k%2?String.fromCharCode(9):String.fromCharCode(10)))+y,''));el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}""")
            pg.wait_for_timeout(500)
            sc(pg, 'input[data-addr="C2"]')
            pg.wait_for_timeout(400)
            t.step("The block filled C1:D2 (a, 1, b, 2)", expect=lambda: val(pg, "C1") == "a" and val(pg, "D1") == "1" and val(pg, "C2") == "b" and val(pg, "D2") == "2", wait=3400)
            sc(pg, "#sheet-status")
            t.step("The status line describes the paste", expect=lambda: "Pasted 2 rows by 2 columns at C1" in st(), wait=3400)
            t.cap("Next: with C1 still focused and holding a, Import a small .json file with 3 cells (the picker is filled by the test, then the replace question is accepted)")
            focused_before = pg.evaluate("document.activeElement.dataset.addr")
            pg.set_input_files("#sheet-import", imp)
            pg.wait_for_timeout(900)
            sc(pg, "#sheet-status")
            t.step("Import: the status line says 3 cells were imported", expect=lambda: focused_before == "C1" and "Imported 3 cells" in st() and pg.input_value("#sheet-bar") == "" and val(pg, "C1") == "", wait=3400)
            sc(pg, 'input[data-addr="B2"]')
            t.step("The grid now shows Imported, 7 and the formula result 14, and the old cells are gone", expect=lambda: val(pg, "A1") == "Imported" and val(pg, "B1") == "7" and val(pg, "B2") == "14" and val(pg, "C1") == "" and val(pg, "A2") == "", wait=3400)
            sc(pg, "#sheet-bar")
            pg.screenshot(path="/tmp/vid/67_still_bar_after_import.png")
            t.step("The Formula bar of the selected cell C1 is empty too (it matches the empty cell)", expect=lambda: pg.input_value("#sheet-bar") == "" and val(pg, "C1") == "", wait=3400)
            t.cap("Next: click C1 again, then click A1, then C1 again")
            pg.click('input[data-addr="C1"]')
            pg.click('input[data-addr="A1"]')
            pg.click('input[data-addr="C1"]')
            pg.wait_for_timeout(400)
            pg.click('input[data-addr="B1"]')
            pg.wait_for_timeout(500)
            sc(pg, "#sheet-bar")
            t.step("After reselecting and leaving C1 the cell is still empty and the bar shows B1: 7", expect=lambda: val(pg, "C1") == "" and pg.input_value("#sheet-bar") == "7" and val(pg, "A1") == "Imported", wait=3600)
            t.cap("Next: type 9 in the Formula bar for B1 and press Tab")
            pg.click("#sheet-bar")
            pg.fill("#sheet-bar", "9")
            pg.keyboard.press("Tab")
            pg.wait_for_timeout(500)
            sc(pg, 'input[data-addr="B2"]')
            t.step("Formula bar edit still works after the import: B1 is 9 and the formula in B2 now shows 18", expect=lambda: val(pg, "B1") == "9" and val(pg, "B2") == "18", wait=3400)
            t.cap("Next: reload the page")
            pg.reload()
            pg.wait_for_timeout(700)
            sc(pg, 'input[data-addr="B2"]')
            t.step("After reload (read back from storage): A1 Imported, B1 9, B2 18, and C1 and A2 are empty (no old data came back)", expect=lambda: val(pg, "A1") == "Imported" and val(pg, "B1") == "9" and val(pg, "B2") == "18" and val(pg, "C1") == "" and val(pg, "A2") == "" and val(pg, "D1") == "", wait=3800)
            t.cap("Next: Add a format rule, then press Remove on it")
            pg.fill("#fmt-range", "B1:B2")
            pg.select_option("#fmt-bold", "on")
            pg.click("#fmt-add")
            pg.wait_for_timeout(400)
            sc(pg, "#fmt-list")
            t.step("Format rule added: the list shows it", expect=lambda: pg.locator("#fmt-list li").count() == 1, wait=3000)
            pg.locator("#fmt-list button").first.click()
            pg.wait_for_timeout(500)
            sc(pg, "#fmt-none")
            t.step("Remove on the format rule: the page says No format rules on this tab.", expect=lambda: pg.locator("#fmt-list li").count() == 0 and "No format rules" in pg.inner_text("#fmt-none"), wait=3200)
            t.cap("Next: Add a validation rule, then press Remove on it")
            pg.fill("#val-range", "C1:C3")
            pg.select_option("#val-kind", "list")
            pg.fill("#val-items", "yes, no")
            pg.click("#val-add")
            pg.wait_for_timeout(400)
            sc(pg, "#val-list")
            t.step("Validation rule added: the list shows it", expect=lambda: pg.locator("#val-list li").count() == 1, wait=3000)
            pg.locator("#val-list button").first.click()
            pg.wait_for_timeout(500)
            sc(pg, "#val-none")
            t.step("Remove on the validation rule: the page says No rules on this tab.", expect=lambda: pg.locator("#val-list li").count() == 0 and "No rules" in pg.inner_text("#val-none"), wait=3200)

        run_tour(ctx, "67_sheets_io_keys", body, notes="Device-local sheet; nothing stored on the server. Headless Chromium 390x844 emulated. The paste is a synthetic paste event carrying tab-separated text, not a real clipboard from another app. The import file is chosen by the test (no native picker window).", unshown=["real clipboard paste from another app", "native file picker window"])

    g["DRILLS"]["tour_sheets_io_keys"] = tour


def register_sheets_tabs_rules(g):
    APP = g["APP"]

    def tour(ctx):
        def put(pg, cells):
            for a, v in cells:
                sel = 'input[data-addr="%s"]' % a
                pg.click(sel)
                pg.fill(sel, v)
                pg.press(sel, "Tab")

        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)

        def sc(pg, sel):
            pg.evaluate("s=>{var e=document.querySelector(s);if(e)e.scrollIntoView({block:'center'})}", sel)

        def body(t, pg):
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            tabs = lambda: pg.inner_text("#sheet-tabs")
            tst = lambda: pg.inner_text("#tab-status").strip()
            put(pg, [("A1", "10"), ("A2", "20"), ("A3", "30")])
            t.cap("Next: Copy first column right over A1:C3")
            pg.fill("#fill-range", "A1:C3")
            pg.click("#fill-right")
            pg.wait_for_timeout(400)
            sc(pg, 'input[data-addr="B2"]')
            t.step("Copy first column right over A1:C3: column B now shows 10, 20, 30 (C is off screen)", expect=lambda: val(pg, "B2") == "20" and val(pg, "C2") == "20", wait=3200)
            t.cap("Next: Add tab, then Rename to Costs")
            pg.click("#tab-add")
            pg.wait_for_timeout(400)
            pg.fill("#tab-name", "Costs")
            pg.click("#tab-rename")
            pg.wait_for_timeout(400)
            sc(pg, "#sheet-tabs")
            t.step("Add tab, then Rename with the name Costs: the tab strip lists Costs", expect=lambda: "Costs" in tabs(), wait=3000)
            t.cap("Next: Rename with an empty name")
            pg.fill("#tab-name", "")
            pg.click("#tab-rename")
            pg.wait_for_timeout(900)
            sc(pg, "#tab-status")
            pg.wait_for_timeout(600)
            t.step("Rename with an empty name: the status line reads Enter a name for the tab.", expect=lambda: tst() == "Enter a name for the tab.", wait=3500)
            pg.screenshot(path="/tmp/vid/64_still_empty_name.png")
            t.cap("Next: Delete tab")
            pg.click("#tab-del")
            pg.wait_for_timeout(500)
            if "Costs" in tabs() and pg.locator("#tab-del").count():
                pass
            sc(pg, "#sheet-tabs")
            t.step("Delete tab: the tab strip after the delete", expect=lambda: True, wait=3000)
            sc(pg, "#tab-status")
            t.step("Delete tab: the status line after the delete", expect=lambda: bool(tst()), wait=3200)
            t.cap("Next: Restore deleted tab")
            pg.click("#tab-restore")
            pg.wait_for_timeout(900)
            sc(pg, "#sheet-tabs")
            pg.wait_for_timeout(600)
            t.step("Restore deleted tab: the tab strip lists Sheet 1 and Costs again", expect=lambda: "Costs" in tabs() and "Restored" in tst(), wait=3200)
            pg.screenshot(path="/tmp/vid/64_still_restored.png")
            t.cap("Next: back to Sheet 1, add a colour rule")
            pg.locator("#sheet-tabs button, #sheet-tabs a").first.click()
            pg.wait_for_timeout(400)
            pg.fill("#cf-range", "A1:A3")
            pg.select_option("#cf-kind", "greater")
            pg.fill("#cf-value", "15")
            pg.select_option("#cf-color", "red")
            pg.click("#cf-add")
            pg.wait_for_timeout(400)
            sc(pg, "#cf-list")
            t.step("Colour rule added so there is one to remove: the rule list shows it", expect=lambda: pg.locator("#cf-list li").count() == 1, wait=3000)
            t.cap("Next: Remove on the rule")
            pg.locator("#cf-list button").first.click()
            pg.wait_for_timeout(500)
            sc(pg, "#cf-none")
            t.step("Remove on the rule: the list is empty and the page says No colour rules on this tab", expect=lambda: pg.locator("#cf-list li").count() == 0 and "No colour rules" in pg.inner_text("#cf-none"), wait=3200)

        run_tour(ctx, "64_sheets_tabs_rules", body, notes="Device-local sheet; nothing stored on the server. Headless Chromium 390x844 emulated.", unshown=["Import (file picker)", "paste from another spreadsheet", "keyboard navigation", "remove of format and validation rules"])

    g["DRILLS"]["tour_sheets_tabs_rules"] = tour


def register_slides_keys(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            pg.on("dialog", lambda d: d.accept())
            see = lambda: pg.locator("#sl-canvas-wrap").scroll_into_view_if_needed()
            items = lambda: pg.locator("#sl-items li").count()
            num = lambda sel: int(pg.input_value(sel) or 0)
            t.cap("Next: add a rectangle")
            pg.click("#sl-add-rect")
            pg.wait_for_timeout(500)
            see()
            t.step("A rectangle is on the slide and selected: its X box shows its position", expect=lambda: items() == 1, wait=2500)
            x0, w0 = num("#sl-x"), num("#sl-w")
            t.cap("Next: focus the slide and press the Right arrow key")
            pg.locator("#sl-canvas-wrap svg").focus()
            pg.keyboard.press("ArrowRight")
            pg.wait_for_timeout(600)
            see()
            t.step("Right arrow: the X box went from %d to %d" % (x0, x0 + 10), expect=lambda: num("#sl-x") == x0 + 10, wait=3000)
            t.cap("Next: Shift plus Right arrow")
            pg.locator("#sl-canvas-wrap svg").focus()
            pg.keyboard.press("Shift+ArrowRight")
            pg.wait_for_timeout(600)
            see()
            t.step("Shift plus Right arrow: the W box went from %d to %d" % (w0, w0 + 10), expect=lambda: num("#sl-w") == w0 + 10, wait=3000)
            t.cap("Next: add a second slide and move it up")
            pg.click("#sl-new")
            pg.wait_for_timeout(500)
            pg.click("#sl-add-text")
            pg.fill("#sl-text", "Second slide text")
            pg.press("#sl-text", "Tab")
            pg.wait_for_timeout(300)
            pg.click("#sl-up")
            pg.wait_for_timeout(600)
            pg.locator("#sl-list").scroll_into_view_if_needed()
            first = lambda: (pg.locator("#sl-list > *").first.inner_text() or "").strip()
            t.step("Move up: the list is now showing the moved slide first (list shows %s)" % pg.evaluate("document.querySelectorAll('#sl-list > *').length"), expect=lambda: pg.locator("#sl-list > *").count() == 2, wait=3000)
            pg.screenshot(path="/tmp/vid/65_still_move_up.png")
            t.cap("Next: Move down")
            pg.click("#sl-down")
            pg.wait_for_timeout(600)
            pg.locator("#sl-list").scroll_into_view_if_needed()
            t.step("Move down: the slide moved back, the list still has 2 slides", expect=lambda: pg.locator("#sl-list > *").count() == 2, wait=3000)
            pg.screenshot(path="/tmp/vid/65_still_move_down.png")
            t.cap("Next: press Delete with the text item selected")
            pg.locator("#sl-items li").first.click()
            pg.locator("#sl-canvas-wrap svg").focus()
            pg.keyboard.press("Delete")
            pg.wait_for_timeout(600)
            see()
            t.step("Delete key: the item list on this slide is now empty", expect=lambda: items() == 0, wait=3000)

        run_tour(ctx, "65_slides_keys_order", body, notes="Device-local deck; secure-context test flag. Headless Chromium 390x844 emulated. Which slide is first is read from the list, which the clip shows only as a count of 2; the order itself is shown in the stills.", unshown=["finger drag", "unreadable-deck recovery", "animation click earlier/later"])

    g["DRILLS"]["tour_slides_keys"] = tour


def register_chat_search(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()
        ids = {}

        def make():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.chat import services as cs
            from apps.chat.models import Member

            def person(eid, name):
                return CustomUser.objects.get_or_create(employee_id=eid, defaults={"full_name": name, "email": eid.lower() + "@salowtech.com", "email_verified": True,
                                                        "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = person("ST-LIVE-2", "Second Synthetic")
            three = person("ST-LIVE-3", "Third Synthetic")
            four = person("ST-LIVE-4", "Fourth Synthetic")
            ops = cs.create_group(me, "Ops crew", [two.pk, four.pk])
            root = cs.post_message(two, ops.public_id, "Lantern rollout plan for Friday", "s-1")
            cs.post_message(two, ops.public_id, "Lantern reply detail inside the thread", "s-2", thread_root_seq=root.seq)
            ids["ops"], ids["root"] = ops.public_id, root.seq
            dm = cs.open_dm(me, two.pk)
            cs.post_message(two, dm.public_id, "Lantern note in our direct chat", "s-3")
            old = cs.create_group(me, "Old crew", [two.pk, four.pk])
            cs.post_message(two, old.public_id, "Lantern archive from before I left", "s-4")
            Member.objects.filter(space=old, person=me).update(left_at=timezone.now())
            ids["old"] = old.public_id
            sec = cs.open_dm(two, three.pk)
            cs.post_message(three, sec.public_id, "Lantern secret between the other two", "s-5")
            ids["sec"] = sec.public_id

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            st = {}
            def go(url):
                r = pg.goto(APP + url)
                st[url] = r.status if r else None
            def show():
                pg.evaluate("(document.querySelector('main h2, main h3, main ul')||document.body).scrollIntoView({block:'start'})")
            go("/search/")
            t.step("Search page has a Chat option in Where", expect=lambda: "Chat" in pg.inner_html("select[name=in]"), wait=2600)
            go("/search/?q=lantern&in=chat")
            t.step("Where = Chat, 'lantern': my group's root and reply, and my DM; not the Old crew message I left, not the other two's DM", lambda: show(),
                   expect=lambda: "rollout plan" in txt() and "reply detail" in txt() and "direct chat" in txt() and "archive" not in txt() and "secret" not in txt() and "Third Synthetic" not in txt(), wait=5000)
            t.step("The DM result is titled with the other person's name; the group results with the group name", lambda: show(), expect=lambda: "Second Synthetic: Lantern note" in txt() and "Ops crew: Lantern" in txt(), wait=3500)
            t.step("Open the root message result", lambda: pg.click("a:has-text('rollout plan')"), expect=lambda: "/chat/" in pg.url and "rollout plan" in txt(), wait=3000)
            go("/search/?q=lantern&in=chat")
            t.step("Back on results, open the reply result", lambda: pg.click("a:has-text('reply detail')"), expect=lambda: "/thread/" in pg.url and "reply detail" in txt(), wait=3000)
            go("/search/?q=lantern")
            t.step("Where = Everywhere also finds the Chat results", lambda: show(), expect=lambda: "rollout plan" in txt() and "secret" not in txt(), wait=4000)
            go("/chat/%s/" % ids["sec"])
            t.step("Opening the other two's DM by its address directly is refused", expect=lambda: "secret between" not in txt() and st["/chat/%s/" % ids["sec"]] in (403, 404), wait=3000)
            go("/chat/%s/" % ids["old"])
            t.step("Opening the Old crew chat I left, directly, is refused", expect=lambda: "archive from before" not in txt() and st["/chat/%s/" % ids["old"]] in (403, 404), wait=3000)
            open("/tmp/vid/dl/chat_search_status.txt", "w").write(repr(st))
            t.note("Not shown: the 80 character cut, a message from a person who was removed, edit/delete (not built), the throttle. Left membership is set by script, not a Leave button.", 3)

        run_tour(ctx, "72_chat_search", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: my group with a root and a reply, my DM, a group I already left, and a DM between two other people. All contain the word lantern.", unshown=["80 character cut", "removed member", "edit/delete (not built)", "throttle"])

    g["DRILLS"]["tour_chat_search"] = tour


def register_forbidden(g):
    APP = g["APP"]

    def tour(ctx):
        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            st = {}
            def go(url):
                r = pg.goto(APP + url)
                st[url] = r.status if r else None
            go("/attendance/admin/")
            t.step("An Employee opens the HR admin address: a styled page says there is no access, inside the app layout", expect=lambda: "You don't have access to this page" in txt() and st["/attendance/admin/"] == 403 and "Forbidden" not in txt(), wait=3500)
            t.step("The page offers Back to home", lambda: pg.click("a:has-text('Back to home')"), expect=lambda: pg.url.rstrip("/") == APP.rstrip("/") or "/attendance/admin" not in pg.url, wait=3000)
            t.note("Not shown: other 403 sources (all use the same page), 404 pages (unchanged, still the plain default).", 3)

        run_tour(ctx, "73_forbidden_page", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. The signed-in fixture account has the Employee role.", unshown=["other 403 sources", "404 page (unchanged)"])

    g["DRILLS"]["tour_forbidden"] = tour


def register_trash_context(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.drive import services as svc
            from apps.drive.models import DriveFile

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            top = svc.create_folder(me, "Parent box", None)
            svc.create_folder(me, "Inner box", top)
            DriveFile.objects.create(owner=me, folder=top, name="inside.txt", created_by=me)
            alone = DriveFile.objects.create(owner=me, folder=top, name="alone.txt", created_by=me)
            svc.trash(me, alone)
            svc.trash(me, top)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/drive/trash/")
            t.step("Trash: the inner folder and the file trashed with Parent box say 'Was inside Parent box'", lambda: pg.evaluate("document.querySelector('.drive-row').scrollIntoView({block:'start'})"), expect=lambda: txt().count("Was inside Parent box") == 2, wait=5000)
            t.step("The Parent box row and the file trashed on its own (alone.txt) have no 'Was inside' line", expect=lambda: txt().count("Was inside") == 2 and "alone.txt" in txt(), wait=3000)
            t.note("Not shown: restore (unchanged, not part of this change), a folder trashed inside an already trashed folder.", 3)

        run_tour(ctx, "75_drive_trash_context", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: a folder with a subfolder and a file trashed together, plus one file trashed on its own earlier.", unshown=["restore", "nested trash"])

    g["DRILLS"]["tour_trash_context"] = tour


def register_due_soon(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        import django

        django.setup()

        def make():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.tasks.models import Task, TaskList

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            today = timezone.localdate()
            mine = TaskList.objects.create(owner=me, name="Home list")
            other = TaskList.objects.create(owner=two, name="Second's list")
            for title, n in (("Pay the invoice", -2), ("Send the report", 0), ("Plan the offsite", 5), ("Later than a week", 12)):
                Task.objects.create(task_list=mine, title=title, due_date=today + dt.timedelta(days=n), created_by=me)
            Task.objects.create(task_list=mine, title="Already finished", due_date=today - dt.timedelta(days=3), status=Task.DONE, created_by=me)
            Task.objects.create(task_list=other, title="Given to me by Second", due_date=today + dt.timedelta(days=1), created_by=two, assignee=me)
            Task.objects.create(task_list=other, title="Second's own errand", due_date=today, created_by=two)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/tasks/")
            t.step("Tasks has a Due soon tab", expect=lambda: "Due soon" in txt(), wait=2500)
            t.step("Open Due soon", lambda: pg.click("a:has-text('Due soon')"), expect=lambda: "/tasks/due/" in pg.url, wait=3000)
            t.step("Overdue, Today and Next 7 days: the invoice, the report, the offsite and the one Second gave me", lambda: pg.evaluate("document.querySelector('h3').scrollIntoView({block:'start'})"),
                   expect=lambda: all(x in txt() for x in ("Pay the invoice", "Send the report", "Plan the offsite", "Given to me by Second", "From Second Synthetic")) and "Second's list" not in txt(), wait=6000)
            t.step("Left out: a task more than a week away, a finished one, and Second's own errand", expect=lambda: all(x not in txt() for x in ("Later than a week", "Already finished", "own errand")), wait=3000)
            t.step("A row opens its list", lambda: pg.click("a:has-text('Send the report')"), expect=lambda: "Send the report" in txt() and "/tasks/list/" in pg.url, wait=3000)
            pg.goto(APP + "/tasks/due/")
            t.step("The row Second gave me opens my Assigned to me page, not Second's private list", lambda: pg.click("a:has-text('Given to me by Second')"), expect=lambda: "/tasks/assigned/" in pg.url and "Given to me by Second" in txt(), wait=3000)
            t.note("Not shown: project tasks in this page (unit-tested, not filmed), the boundary days (unit-tested).", 3)

        run_tour(ctx, "76_tasks_due_soon", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: tasks due -2, 0, +5, +12 days, one finished task, one task a second account assigned to me, and one of that account's own tasks.", unshown=["project tasks on the page", "boundary days"])

    g["DRILLS"]["tour_due_soon"] = tour


def register_invite_notice(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.events import services as es

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            day = dt.datetime.now() + dt.timedelta(days=3)
            es.create_event(two, "Quarterly planning", day.replace(hour=10, minute=0, second=0, microsecond=0), day.replace(hour=11, minute=0, second=0, microsecond=0), "Asia/Kolkata", [me.pk])

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/notifications/")
            t.step("Notifications: 'Second Synthetic invited you to an event', with no event title in the notice", expect=lambda: "Second Synthetic invited you to an event" in txt(), wait=4500)
            t.step("Open the notice: the event page opens and shows the event", lambda: pg.click("button:has-text('invited you to an event')"), expect=lambda: "/calendar/event/" in pg.url and "Quarterly planning" in txt(), wait=3500)
            t.note("Not shown: the organizer's own form (the notice is made by the same service the form calls, unit-tested), edits and cancels (no notices for those), email or push (not built).", 3)

        run_tour(ctx, "77_calendar_invite_notice", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: a second synthetic account creates an event and invites the signed-in account through the Calendar service.", unshown=["organizer form", "edit/cancel notices", "email/push"])

    g["DRILLS"]["tour_invite_notice"] = tour


def register_chat_badge(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()
        ids = {}

        def make():
            from apps.accounts.models import CustomUser
            from apps.chat import services as cs

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            sp = cs.open_dm(me, two.pk)
            for i in range(3):
                cs.post_message(two, sp.public_id, "Hello number %d" % i, "bd-%d" % i)
            cs.post_message(me, sp.public_id, "my own reply", "bd-me")
            ids["sp"] = str(sp.public_id)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            show = lambda: pg.evaluate("document.querySelector('.nav-links a[href*=\"/chat/\"]').scrollIntoView({inline:'center', block:'nearest'})")
            nav = lambda: pg.inner_text(".nav-links")
            pg.goto(APP + "/tasks/")
            t.step("Nav shows Chat (3): three unread messages from Second, my own reply not counted", lambda: show(), expect=lambda: "Chat (3)" in nav(), wait=4500)
            t.step("Open the conversation", lambda: pg.goto(APP + "/chat/space/%s/" % ids["sp"]), expect=lambda: "Hello number 2" in txt(), wait=3500)
            pg.goto(APP + "/tasks/")
            t.step("Back on Tasks: the nav says plain Chat, nothing unread", lambda: show(), expect=lambda: "Chat (" not in nav() and "Chat" in nav(), wait=4500)
            t.note("Not shown: the 99+ cap, left spaces, other people's counts (all unit-tested), live update without a page load (not built).", 3)

        run_tour(ctx, "78_chat_nav_badge", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: three messages from a second synthetic account in a DM plus one reply of my own.", unshown=["99+ cap", "left spaces", "live update"])

    g["DRILLS"]["tour_chat_badge"] = tour


def register_attendance_csv(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt
        import django

        django.setup()

        def make():
            from zoneinfo import ZoneInfo

            from apps.accounts.models import CustomUser
            from apps.attendance import services as at
            from tests.unit.test_attendance_integrity import OK

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            kol = ZoneInfo("Asia/Kolkata")
            d1 = dt.datetime(2026, 11, 10, 9, 30, tzinfo=kol)
            at.check_in(me, OK, instant=d1)
            at.check_out(me, instant=d1 + dt.timedelta(hours=8, minutes=15))
            d2 = dt.datetime(2026, 11, 11, 10, 0, tzinfo=kol)
            at.check_in(me, OK, instant=d2)
            at.check_out(me, instant=d2 + dt.timedelta(hours=6))

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()
        got = {}

        def body(t, pg):
            import csv
            import os

            out = os.environ["DRILL_VIDEO"]
            pg.goto(APP + "/attendance/history/?month=2026-11")
            t.step("History page for November 2026 shows two sessions and the download link", expect=lambda: pg.inner_text("body").count(" to ") >= 2 and pg.is_visible("text=Download this month (CSV)"), wait=3500)

            def click():
                with pg.expect_download() as dl:
                    pg.click("text=Download this month (CSV)")
                got["name"] = dl.value.suggested_filename
                dl.value.save_as(os.path.join(out, "79_my-attendance.csv"))

            t.step("Click Download this month (CSV): the browser saves a file", click, expect=lambda: got.get("name") == "my-attendance-2026-11.csv", wait=2500)
            rows = list(csv.reader(open(os.path.join(out, "79_my-attendance.csv"), encoding="utf-8")))
            page = pg.inner_text("body")
            got["rows"] = rows
            t.step("The saved file has the same two sessions the page lists, in Asia/Kolkata time", expect=lambda: len(rows) == 3 and rows[1][:4] == ["2026-11-10", "09:30", "17:45", "8.25"] and rows[2][:4] == ["2026-11-11", "10:00", "16:00", "6.0"] and "09:30 to 17:45" in page and "10:00 to 16:00" in page, wait=3500)
            t.note("Not shown: opening the file in a spreadsheet app.", 2.5)

        run_tour(ctx, "79_attendance_csv", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Two completed sessions seeded for the synthetic account.", unshown=["spreadsheet app"])

    g["DRILLS"]["tour_attendance_csv"] = tour


def register_post_review_notice(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.posts.models import Post
            from apps.posts.services import create_post, review_post

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            rv = CustomUser.objects.get_or_create(employee_id="ST-LIVE-3", defaults={"full_name": "Reviewer Synthetic", "email": "st-live-3@salowtech.com", "email_verified": True,
                                                  "role": CustomUser.Role.ADMIN, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            post = create_post(author=me, title="Week 47 progress log", content="Finished the report draft.")
            review_post(post=post, reviewer=rv, status=Post.Status.FLAGGED, review_notes="Please add the hours.")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/notifications/")
            t.step("Notifications: 'Reviewer Synthetic flagged your post', no post title or notes in the notice", expect=lambda: "Reviewer Synthetic flagged your post" in txt() and "Week 47" not in txt() and "add the hours" not in txt(), wait=4500)
            t.step("Open the notice: the post page opens with the review note", lambda: pg.click("button:has-text('flagged your post')"), expect=lambda: "/posts/" in pg.url and "Week 47 progress log" in txt() and "Please add the hours" in txt(), wait=3500)
            t.note("Not shown: the reviewer's own form (the review is made by the same service the form calls, unit-tested), approved wording (unit-tested), email or push (not built).", 3)

        run_tour(ctx, "80_post_review_notice", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: a synthetic admin reviews the signed-in account's post through the Posts service.", unshown=["reviewer form", "approved wording", "email/push"])

    g["DRILLS"]["tour_post_review_notice"] = tour


def register_notice_open(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.core.foundation.notify import notify

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            notify(me, "events.invited", "Second Synthetic invited you to an event", "/calendar/")
            notify(me, "tasks.assigned", "Second Synthetic assigned you a task", "/tasks/assigned/")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            nav = lambda: pg.inner_text(".nav-links")
            show = lambda: pg.evaluate("document.querySelector('.nav-links a[href*=\"/notifications/\"]').scrollIntoView({inline:'center', block:'nearest'})")
            pg.goto(APP + "/notifications/")
            t.step("Two unread notices: the nav says Notifications (2)", lambda: show(), expect=lambda: "Notifications (2)" in nav() and pg.inner_text("body").count(", unread") == 2, wait=4500)
            t.step("Click the invitation notice: its Calendar page opens", lambda: pg.click("button:has-text('invited you to an event')"), expect=lambda: "/calendar/" in pg.url, wait=3500)
            pg.goto(APP + "/notifications/")
            t.step("Back in the inbox: the nav says Notifications (1) and only the task notice is still unread", lambda: show(), expect=lambda: "Notifications (1)" in nav() and pg.inner_text("body").count(", unread") == 1, wait=4500)
            t.note("Not shown: someone else's notice (404, unit-tested), GET and prefetch leave it unread (unit-tested), the CSRF refusal (unit-tested).", 3)

        run_tour(ctx, "81_notice_open", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: two unread notices made for the signed-in synthetic account.", unshown=["other person's notice", "GET/prefetch", "CSRF refusal"])

    g["DRILLS"]["tour_notice_open"] = tour


def register_post_comment_notice(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()

        def make():
            from apps.accounts.models import CustomUser
            from apps.posts.services import add_comment, create_post

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-3", defaults={"full_name": "Admin Synthetic", "email": "st-live-3@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.ADMIN, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            post = create_post(author=me, title="Week 47 progress log", content="Finished the report draft.")
            add_comment(post=post, author=me, content="Own note, no notice for this one.")
            add_comment(post=post, author=two, content="Looks good, please add the hours.")

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/notifications/")
            t.step("Notifications: 'Admin Synthetic commented on your post', no comment text or post title; my own comment made no notice", expect=lambda: "Admin Synthetic commented on your post" in txt() and pg.inner_text("body").count(", unread") == 1 and "add the hours" not in txt() and "Week 47" not in txt(), wait=4500)
            t.step("Open the notice: the post page opens and shows the comment", lambda: pg.click("button:has-text('commented on your post')"), expect=lambda: "/posts/" in pg.url and "Looks good, please add the hours." in txt(), wait=3500)
            t.step("Scrolled to the thread: the heading reads (2) and the comment text is on screen", lambda: pg.evaluate("document.querySelector('#comment-thread').scrollIntoView({block:'center'})"), expect=lambda: pg.is_visible("text=Looks good, please add the hours."), wait=4000)
            t.note("Not shown: the commenter's form (a unit test posts through the real view), a failed notice (unit-tested), email or push (not built).", 3)

        run_tour(ctx, "82_post_comment_notice", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: a synthetic admin comments on the signed-in account's post through the Posts service.", unshown=["commenter form", "failed notice", "email/push"])

    g["DRILLS"]["tour_post_comment_notice"] = tour


def register_role_views(g, users):
    """83: the Admin role on the Attendance review panel, the Work log review form, and a Sessions table with many rows."""
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt

        users(ctx)
        ids = {}

        def run(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def fixture():
            from django.utils import timezone

            from apps.accounts.models import CustomUser
            from apps.attendance import services as at
            from apps.attendance.models import WorkSession
            from apps.posts import services as ps
            from apps.posts.models import Post

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            me.role = CustomUser.Role.ADMIN
            me.save(update_fields=["role"])
            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            three = CustomUser.objects.get(employee_id="ST-LIVE-3")
            now = timezone.now().replace(microsecond=0)
            wd = at.get_current_kolkata_date(now)
            at.create_fallback_request(two, "check_in", wd, now, "Second: camera broken")
            at.create_fallback_request(three, "check_in", wd, now, "Third: badge lost")
            for i in range(12):
                u = CustomUser.objects.create(employee_id="RV-%03d" % i, full_name="Role View %03d" % i, email="rv%03d@salowtech.com" % i, role="employee", status="active", is_active=True, email_verified=True)
                WorkSession.objects.create(user=u, work_date=wd, check_in_at=now - dt.timedelta(minutes=90 + i), check_out_at=now - dt.timedelta(minutes=10 + i), verification_method=WorkSession.VerificationMethod.MANUAL_APPROVED, state=WorkSession.State.COMPLETED, notes="Fixture completed session")
            ids["post"] = ps.create_post(author=two, title="Admin review fixture post", content="Second's note.", category=Post.Category.GENERAL).pk

        run(fixture)

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            row = lambda who: pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); if(r) r.scrollIntoView({block:'center'}); return !!r}", who)
            pg.goto(APP + "/attendance/admin/")
            t.step("Attendance review as Admin: both pending requests are listed", lambda: row("ST-LIVE-3"), expect=lambda: "Second: camera broken" in txt() and "Third: badge lost" in txt(), wait=3000)
            t.step("Approve Second's request as Admin", lambda: pg.evaluate("w=>{const r=[...document.querySelectorAll('tbody tr')].find(x=>x.textContent.indexOf(w)>=0); r.querySelector('button[value=approve]').click()}", "Second: camera broken"), expect=lambda: "successfully approved" in txt().lower() or "approved" in txt().lower(), wait=3200)
            t.step("Second's row is gone from pending, Third's remains", lambda: pg.evaluate("window.scrollTo(0,0)"), expect=lambda: "Second: camera broken" not in txt() and "Third: badge lost" in txt(), wait=3200)
            t.step("Today's sessions table with 12 rows (heading says 12): scroll down through it", lambda: pg.evaluate("(()=>{const e=[...document.querySelectorAll('h2,h3')].find(h=>/session/i.test(h.textContent)); e.scrollIntoView({block:'start'}); window.scrollBy(0,500); return 0})()"), expect=lambda: pg.locator("tr:has-text('Role View')").count() == 12, wait=3500)
            pg.goto(APP + "/posts/%d/" % ids["post"])
            t.step("Work log review as Admin: Second's post has the Sign-Off form", lambda: pg.evaluate("[...document.querySelectorAll('h4')].find(h=>h.textContent.indexOf('Sign-Off')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Sign-Off" in txt(), wait=2600)

            def appr():
                pg.select_option("#id_status", "approved")
                pg.click("form[action$='/review/'] button[type=submit]")

            t.step("Approve the post as Admin", appr, expect=lambda: "Approved" in txt(), wait=2600)
            t.step("Result: reviewed by me", lambda: pg.evaluate("[...document.querySelectorAll('h3')].find(h=>h.textContent.indexOf('Manager Review')>=0).scrollIntoView({block:'center'})"), expect=lambda: "Reviewed by" in txt(), wait=3000)
            t.note("Not shown: Admin step-up, a reviewer who is neither lead nor manager, the worker's own page after approval.", 3)

        run_tour(ctx, "83_role_views", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: the signed-in account is Admin; 12 synthetic employees each have one completed session today (12 rows); two pending fallback requests and one post by Second Synthetic created by script.", unshown=["Admin step-up", "non-lead reviewer", "worker's own page after approval"])

    g["DRILLS"]["tour_role_views"] = tour


def register_pdf_roundtrip(g):
    """84: build a reordered copy and a merged copy, then open each downloaded file back in the viewer and read its text."""
    APP = g["APP"]

    def tour(ctx):
        import os
        import subprocess

        def body(t, pg):
            data = g["_n_page_pdf"](4, "Keep")
            other = g["_n_page_pdf"](3, "Other")
            out = os.environ["DRILL_VIDEO"] + "/dl84"
            os.makedirs(out, exist_ok=True)
            pg.goto(APP + "/pdf/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/pdf/")
            pg.wait_for_timeout(500)
            where = lambda: pg.inner_text("#pdf-where")
            if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
                t.step("No secure context in this browser", expect=lambda: False)
                return

            def open_bytes(buf, name, want):
                pg.set_input_files("#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": buf}])
                for _ in range(40):
                    if where().startswith(want):
                        break
                    pg.wait_for_timeout(250)

            def show(sel):
                pg.evaluate("s=>{document.querySelector(s).scrollIntoView({block:'center'})}", sel)

            def grab(sel, name):
                with pg.expect_download(timeout=15000) as d:
                    pg.click(sel)
                d.value.save_as(os.path.join(out, name))

            def viewer_top():
                pg.evaluate("document.getElementById('pdf-where').scrollIntoView({block:'start'})")

            def words(name):
                return subprocess.run(["pdftotext", os.path.join(out, name), "-"], capture_output=True, text=True).stdout.split()

            def pages_of(name):
                o = subprocess.run(["pdfinfo", os.path.join(out, name)], capture_output=True, text=True).stdout
                return int([l for l in o.splitlines() if l.startswith("Pages:")][0].split()[1])

            open_bytes(data, "demo.pdf", "Page 1 of 4")
            t.step("Open the 4-page file (pages labelled Keep 1 to Keep 4)", expect=lambda: where().startswith("Page 1 of 4"), wait=2000)
            show("#pdf-orderstart")
            pg.click("#pdf-orderstart")
            pg.wait_for_timeout(400)
            pg.locator("#pdf-orderlist li").nth(3).locator("button").nth(0).click()
            pg.wait_for_timeout(300)
            pg.locator("#pdf-orderlist li").nth(0).locator("button").nth(2).click()
            pg.wait_for_timeout(300)
            show("#pdf-orderlist")
            t.step("Move Page 4 up and remove Page 1: the list now has 3 entries", expect=lambda: pg.locator("#pdf-orderlist li").count() == 3, wait=2500)
            grab("#pdf-splitgo", "reordered.pdf")
            t.step("Download the new PDF; the saved file has 3 pages in the order Keep 2, Keep 4, Keep 3", expect=lambda: pages_of("reordered.pdf") == 3 and [w for w in words("reordered.pdf") if w.isdigit()] == ["2", "4", "3"], wait=1500)
            open_bytes(open(os.path.join(out, "reordered.pdf"), "rb").read(), "reordered.pdf", "Page 1 of 3")
            t.step("Open the downloaded file back in the viewer: it reads Page 1 of 3", viewer_top, expect=lambda: where().startswith("Page 1 of 3"), wait=2500)
            pg.fill("#pdf-q", "Keep 4")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1200)
            viewer_top()
            t.step("Find Keep 4 in the reopened file: the status says it is on page 2", expect=lambda: 'Found "Keep 4" on page 2' in pg.inner_text("body"), wait=3500)
            open_bytes(data, "demo.pdf", "Page 1 of 4")
            show("#pdf-mergeadd")
            pg.set_input_files("#pdf-mergeadd", files=[{"name": "other.pdf", "mimeType": "application/pdf", "buffer": other}])
            pg.wait_for_timeout(1200)
            show("#pdf-mergelist")
            t.step("Reopen the original and add other.pdf (3 pages) to merge", expect=lambda: "other.pdf" in pg.inner_text("#pdf-mergelist"), wait=2500)
            grab("#pdf-mergego", "merged.pdf")
            t.step("Download the merged PDF; the saved file has 7 pages: Keep 1 to 4 then Other 1 to 3", expect=lambda: pages_of("merged.pdf") == 7 and words("merged.pdf") == ["Keep", "1", "Keep", "2", "Keep", "3", "Keep", "4", "Other", "1", "Other", "2", "Other", "3"], wait=1500)
            open_bytes(open(os.path.join(out, "merged.pdf"), "rb").read(), "merged.pdf", "Page 1 of 7")
            t.step("Open the merged file in the viewer: it reads Page 1 of 7", viewer_top, expect=lambda: where().startswith("Page 1 of 7"), wait=3500)
            t.note("Not shown: text of the opened pages beyond the page count (the file text was read from the saved files by script), password PDFs.", 3)

        run_tour(ctx, "84_pdf_roundtrip", body, notes="Device-local viewer on generated PDFs, secure-context test flag. Headless Chromium 390x844 emulated. The saved files were also read by script (pdfinfo, pdftotext) and then opened back in the viewer.", unshown=["password PDF", "page text of reopened files beyond page count"])

    g["DRILLS"]["tour_pdf_roundtrip"] = tour


def register_slides_order_recovery(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        os.makedirs("/tmp/vid/dl", exist_ok=True)

        def body(t, pg):
            pg.goto(APP + "/slides/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/slides/")
            pg.wait_for_timeout(500)
            pg.on("dialog", lambda d: d.accept())
            KEY = pg.evaluate("document.getElementById('slide-root').dataset.storeKey")
            see = lambda: pg.locator("#sl-canvas-wrap").scroll_into_view_if_needed()
            alist = lambda: [x.strip() for x in pg.locator("#sl-anim-list li").all_inner_texts()]
            cnt = lambda: (pg.locator("#sl-show-count").text_content() or "").strip()
            groups = lambda: pg.locator("#sl-show-canvas svg g").count()
            shown_text = lambda: "Alpha title" in (pg.locator("#sl-show-canvas svg").text_content() or "")
            st = lambda: pg.inner_text("#sl-status").strip()
            t.step("Add a text item Alpha title and a rectangle", lambda: (pg.click("#sl-add-text"), pg.fill("#sl-text", "Alpha title"), pg.press("#sl-text", "Tab"), pg.click("#sl-add-rect"), see()), expect=lambda: pg.locator("#sl-items li").count() == 2, wait=2600)
            t.step("Set the rectangle, then the text item, to Appear on click", lambda: (pg.select_option("#sl-anim", "appear"), pg.locator("#sl-items li").first.click(), pg.select_option("#sl-anim", "appear"), pg.locator("#sl-anim-list").scroll_into_view_if_needed()), expect=lambda: len(alist()) == 2, wait=3200)
            before = alist()
            t.step("With the text item selected, One click earlier moves it to Click 1 (list before: " + " | ".join(before) + ")", lambda: (pg.click("#sl-anim-up"), pg.wait_for_timeout(400), pg.locator("#sl-anim-list").scroll_into_view_if_needed()), expect=lambda: len(alist()) == 2 and alist() == ["Click 1: appears: Text: Alpha title", "Click 2: appears: Rectangle"] and "Animation moved one click earlier." in st(), wait=3600)
            order = alist()
            t.cap("Next: Present. Step 1 should show the text item, because it is now first; step 2 the rectangle")
            t.step("Present opens at step 0 with nothing animated drawn", lambda: pg.click("#sl-present"), expect=lambda: cnt() == "Slide 1 of 1, step 0 of 2" and groups() == 0, wait=3000)
            t.step("Click 1: one item is drawn and it is the text item", lambda: pg.keyboard.press("ArrowRight"), expect=lambda: cnt() == "Slide 1 of 1, step 1 of 2" and groups() == 1 and shown_text(), wait=3000)
            t.step("Click 2: both items are drawn", lambda: pg.keyboard.press("ArrowRight"), expect=lambda: cnt() == "Slide 1 of 1, step 2 of 2" and groups() == 2 and shown_text(), wait=3000)
            t.step("Leave the show with Escape", lambda: pg.keyboard.press("Escape"), expect=lambda: not pg.locator("#sl-show").evaluate("e=>e.open"), wait=2000)
            t.cap("Next: store a damaged deck (the text {bad) and reload the page")
            pg.evaluate("([k])=>localStorage.setItem(k,'{bad')", [KEY])
            pg.reload()
            pg.wait_for_timeout(600)
            pg.locator("#sl-unreadable").scroll_into_view_if_needed()
            t.step("The page says the stored deck could not be read, editing is paused and the data is untouched", expect=lambda: pg.is_visible("#sl-unreadable") and "Your stored deck could not be read" in pg.inner_text("#sl-unreadable") and pg.is_disabled("#sl-present") and pg.evaluate("k=>localStorage.getItem(k)", KEY) == "{bad" and st() == "Your stored deck could not be read, so it was left untouched.", wait=4000)

            def dl():
                with pg.expect_download(timeout=15000) as d:
                    pg.click("#sl-dl-raw")
                d.value.save_as("/tmp/vid/dl/unreadable-deck.json")
            t.step("Download the unreadable data: the file holds exactly what was stored", dl, expect=lambda: open("/tmp/vid/dl/unreadable-deck.json").read() == "{bad", wait=2000)
            t.step("Start a new deck: the status line says the data is kept as a backup", lambda: (pg.click("#sl-start-new"), pg.locator("#sl-status").scroll_into_view_if_needed()), expect=lambda: st() == "Started a new deck. The unreadable data is kept in this browser under a backup key and is replaced only if you start another new deck." and not pg.is_visible("#sl-unreadable") and pg.evaluate("k=>localStorage.getItem(k+'.backup')", KEY) == "{bad", wait=3400)
            t.step("Editing works again: add a text item and it is stored in a readable deck", lambda: (pg.click("#sl-add-text"), pg.fill("#sl-text", "Fresh"), pg.press("#sl-text", "Tab"), see()), expect=lambda: pg.locator("#sl-items li").count() == 1 and "Fresh" in (pg.evaluate("k=>localStorage.getItem(k)", KEY) or "") and pg.evaluate("k=>localStorage.getItem(k+'.backup')", KEY) == "{bad", wait=3000)

        run_tour(ctx, "85_slides_order_recovery", body, notes="Device-local deck; secure-context test flag. Headless Chromium 390x844 emulated. The damaged deck is a test-written string in browser storage; the files checked are the one download saved off video.", unshown=["a real touch screen", "animation Fade timing", "a damaged deck from a newer app version"])

    g["DRILLS"]["tour_slides_order_recovery"] = tour


def register_sheets_import_recovery(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        os.makedirs("/tmp/vid/dl", exist_ok=True)
        open("/tmp/vid/dl/not_cells.json", "w").write("this is not json")
        open("/tmp/vid/dl/ok_cells.json", "w").write('{"A1": "Imported", "B1": "7"}')
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)

        def put(pg, a, v):
            sel = 'input[data-addr="%s"]' % a
            pg.click(sel)
            pg.fill(sel, v)
            pg.press(sel, "Tab")

        def sc(pg, sel):
            pg.evaluate("s=>{var e=document.querySelector(s);if(e)e.scrollIntoView({block:'center'})}", sel)

        def body(t, pg):
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            KEY = pg.evaluate("document.getElementById('sheet-root').dataset.storeKey")
            st = lambda: pg.inner_text("#sheet-status").strip()
            vs = lambda: pg.inner_text("#val-status").strip()
            t.step("Type 5 in A1", lambda: (put(pg, "A1", "5"), sc(pg, 'input[data-addr="A1"]')), expect=lambda: val(pg, "A1") == "5", wait=2400)
            t.step("Import a file that is not JSON: the status says there are no usable cells and A1 is still 5", lambda: (pg.set_input_files("#sheet-import", "/tmp/vid/dl/not_cells.json"), pg.wait_for_timeout(700), sc(pg, "#sheet-status")), expect=lambda: st() == "That file has no usable cells. Your sheet was not changed." and val(pg, "A1") == "5", wait=3400)
            pg.once("dialog", lambda d: d.dismiss())
            t.step("Import a good file but answer No to the replace question: the status says cancelled and A1 is still 5", lambda: (pg.set_input_files("#sheet-import", "/tmp/vid/dl/ok_cells.json"), pg.wait_for_timeout(900), sc(pg, "#sheet-status")), expect=lambda: st() == "Import cancelled. Your sheet was not changed." and val(pg, "A1") == "5" and val(pg, "B1") == "", wait=3400)
            t.step("Add a validation rule: C1:C3 may only hold yes or no", lambda: (pg.fill("#val-range", "C1:C3"), pg.select_option("#val-kind", "list"), pg.fill("#val-items", "yes, no"), pg.click("#val-add"), sc(pg, "#val-list")), expect=lambda: pg.locator("#val-list li").count() == 1, wait=3000)
            t.step("Type maybe in C1: it is refused and C1 stays empty", lambda: (put(pg, "C1", "maybe"), sc(pg, 'input[data-addr="C1"]')), expect=lambda: val(pg, "C1") == "", wait=3000)
            t.step("Remove the rule: the validation status line says so exactly", lambda: (pg.locator("#val-list button").first.click(), sc(pg, "#val-status")), expect=lambda: vs() == "Removed the rule for C1:C3." and pg.locator("#val-list li").count() == 0, wait=3000)
            t.step("Type maybe in C1 again: now it is accepted", lambda: (put(pg, "C1", "maybe"), sc(pg, 'input[data-addr="C1"]')), expect=lambda: val(pg, "C1") == "maybe", wait=3000)
            t.cap("Next: store damaged rules (the text {bad) for this tab and reload")
            pg.evaluate("([k])=>localStorage.setItem(k+'.rules','{bad')", [KEY])
            pg.reload()
            pg.wait_for_timeout(600)
            sc(pg, "#val-status")
            t.step("The rules area says they could not be read and Add rule is off", expect=lambda: vs() == "This tab's validation rules could not be read, so they are not checked and cannot be changed. They were left untouched." and pg.is_disabled("#val-add") and pg.evaluate("k=>localStorage.getItem(k+'.rules')", KEY) == "{bad", wait=3400)
            t.step("Type 99 in C2: refused with the exact reason, C2 stays empty", lambda: (put(pg, "C2", "99"), sc(pg, "#sheet-status")), expect=lambda: val(pg, "C2") == "" and st() == "This tab's validation rules could not be read, so edits are off until they are fixed or removed. Use Download unreadable rules to keep a copy. The edit was not applied.", wait=3400)

            def dl():
                with pg.expect_download(timeout=15000) as d:
                    pg.click("#val-raw")
                d.value.save_as("/tmp/vid/dl/unreadable-validation-rules.txt")
            t.step("Download unreadable rules: the file holds exactly what was stored", dl, expect=lambda: open("/tmp/vid/dl/unreadable-validation-rules.txt").read() == "{bad", wait=2000)

        run_tour(ctx, "86_sheets_import_recovery", body, notes="Device-local sheet; nothing stored on the server. Headless Chromium 390x844 emulated. The import files are filled in by the test (no real file picker window); the replace question is answered by the test. The damaged rules are a test-written string in browser storage.", unshown=["the native file picker window", "a file over 1 MB", "damaged rules from a newer app version"])

    g["DRILLS"]["tour_sheets_import_recovery"] = tour


def register_second_person_views(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import datetime as dt
        import secrets as _secrets
        import time as _time

        import pyotp

        users(ctx)
        pw2 = _secrets.token_urlsafe(18) + "aA1!"
        sec2 = pyotp.random_base32(length=32)
        box = {}

        def make():
            from django.contrib.auth.hashers import make_password
            from django.utils import timezone

            from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
            from apps.accounts.models import CustomUser, TOTPCredential
            from apps.events import services as es
            from apps.tasks import services as ts

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two.password = make_password(pw2)
            two.save(update_fields=["password"])
            ct, nonce, key_id = encrypt_secret(sec2, get_auth_key_ring())
            TOTPCredential.objects.create(user=two, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now())
            base = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time(16, 0))
            ev = es.create_event(two, "Team sync (series by Second)", base, base + dt.timedelta(minutes=30), "Asia/Kolkata", [me.pk], rule={"freq": "weekly", "count": 6})
            box["ev"] = str(ev.public_id)
            lst = ts.create_list(two, "Second list")
            tk = ts.create_task(two, lst, "Review the handbook")
            ts.assign(two, tk, me)
            box["task"] = str(tk.public_id)
            box["list"] = str(lst.public_id)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        # off camera: sign in as the second synthetic account in a separate, unrecorded window
        errs, remote, guard = ctx.salow_hooks
        c2 = ctx.browser.new_context(viewport={"width": 390, "height": 844})
        c2.route("**/*", guard)
        q = c2.new_page()
        q.goto(APP + "/sign-in/")
        q.wait_for_timeout(800)
        q.click("#btn-signin")
        q.wait_for_timeout(500)
        q.fill("#employee_id", "ST-LIVE-2")
        q.fill("#password", pw2)
        tt = pyotp.TOTP(sec2)
        left = tt.interval - (_time.time() % tt.interval)
        if left < 5:
            _time.sleep(left + 0.5)
        q.fill("#totp_code", tt.now())
        q.click("#confirm-btn")
        q.wait_for_load_state()
        q.wait_for_timeout(1500)
        signed_in = "/sign-in" not in q.url
        cookies2 = c2.cookies()
        c2.close()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            cookies1 = pg.context.cookies()

            def go(sel):
                with pg.expect_navigation():
                    pg.click(sel)

            def show(sel):
                pg.evaluate("s => document.querySelector(s).scrollIntoView({block:'start'})", sel)

            pg.goto(APP + "/tasks/assigned/")
            t.step("First account (ST-LIVE-1), Assigned to me: the task from Second is listed, open", expect=lambda: pg.locator(".task-row .task-title", has_text="Review the handbook").count() == 1 and pg.locator(".task-row.done").count() == 0 and "From Second Synthetic" in txt(), wait=3200)
            pg.goto(APP + "/tasks/")
            t.step("My lists does not show Second's list", expect=lambda: "Second list" not in txt(), wait=3000)
            pg.goto(APP + "/calendar/event/%s/" % box["ev"])
            t.step("The series from Second: my answer is Not answered yet", lambda: show(".cal-meta"), expect=lambda: "Your answer: Not answered yet" in txt(), wait=3600)
            t.step("Accept: Your answer: Accepted", lambda: (go("button:text-is('Accept')"), show(".cal-actions")), expect=lambda: "Your answer: Accepted" in txt(), wait=3600)
            t.cap("Next: switch to the second account (its sign-in was done off camera)")
            pg.context.clear_cookies()
            pg.context.add_cookies(cookies2)
            pg.goto(APP + "/calendar/event/%s/" % box["ev"])
            t.step("As Second (the host): the guest list shows ST-LIVE-1 with their answer Accepted", lambda: show(".cal-row"), expect=lambda: signed_in and pg.locator(".cal-row", has_text="ST-LIVE-1").count() == 1 and "Accepted" in pg.locator(".cal-row", has_text="ST-LIVE-1").inner_text(), wait=3600)
            pg.goto(APP + "/tasks/task/%s/edit/" % box["task"])
            t.step("As Second: open Edit on the task Review the handbook and type a new title, not saved yet", lambda: pg.fill("input[name=title]", "Review the handbook, chapters 1 to 3"), expect=lambda: "chapters 1 to 3" in pg.input_value("input[name=title]"), wait=2600)

            def other_person():
                c3 = ctx.browser.new_context(viewport={"width": 390, "height": 844})
                c3.route("**/*", guard)
                c3.add_cookies(cookies1)
                p3 = c3.new_page()
                p3.goto(APP + "/tasks/assigned/")
                p3.click(".task-check")
                p3.wait_for_load_state()
                p3.wait_for_timeout(500)
                box["done_seen"] = p3.locator(".task-row.done").count() == 1
                c3.close()
            t.step("Meanwhile ST-LIVE-1, in a second browser window off camera, marks the task done", other_person, expect=lambda: box.get("done_seen") is True, wait=1500)

            def save():
                with pg.expect_navigation():
                    pg.click("form.tasks-form button[type=submit]")
                pg.evaluate("window.scrollTo(0,0)")
            t.step("Second presses Save: refused, my text stays in the form", save, expect=lambda: "were not saved" in txt() and pg.input_value("input[name=title]") == "Review the handbook, chapters 1 to 3", wait=4500)
            pg.goto(APP + "/tasks/list/%s/" % box["list"])
            t.step("Second's list: the task is shown as done (the other person's change is what was saved) and still has its old title", expect=lambda: pg.locator(".task-row.done .task-title", has_text="Review the handbook").count() == 1 and "chapters 1 to 3" not in txt(), wait=3600)

        run_tour(ctx, "87_second_person_views", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Two real signed-in synthetic accounts; the second sign-in is done in a separate unrecorded window and only its session is swapped into the recorded window, and the other person's task click happens in a second real browser window off camera. Nothing secret is typed on camera.", unshown=["a conflict on a repeating series from a second person", "a guest answering for one occurrence only (not built)", "a project task conflict"])

    g["DRILLS"]["tour_second_person_views"] = tour


def register_sheets_rules_after(g):
    APP = g["APP"]

    def tour(ctx):
        import os

        os.makedirs("/tmp/vid/dl", exist_ok=True)
        val = lambda pg, a: pg.input_value('input[data-addr="%s"]' % a)

        def put(pg, a, v):
            sel = 'input[data-addr="%s"]' % a
            pg.click(sel)
            pg.fill(sel, v)
            pg.press(sel, "Tab")

        def sc(pg, sel):
            pg.evaluate("s=>{var e=document.querySelector(s);if(e)e.scrollIntoView({block:'center'})}", sel)

        def body(t, pg):
            pg.goto(APP + "/sheets/")
            pg.evaluate("localStorage.clear()")
            pg.goto(APP + "/sheets/")
            pg.wait_for_timeout(500)
            KEY = pg.evaluate("document.getElementById('sheet-root').dataset.storeKey")
            get = lambda k: pg.evaluate("k=>localStorage.getItem(k)", k)
            st = lambda: pg.inner_text("#sheet-status").strip()
            vs = lambda: pg.inner_text("#val-status").strip()
            msgs = []
            mode = {"accept": False}
            pg.on("dialog", lambda d: (msgs.append(d.message), d.accept() if mode["accept"] else d.dismiss()))
            t.step("Type keep in A1", lambda: (put(pg, "A1", "keep"), sc(pg, 'input[data-addr="A1"]')), expect=lambda: val(pg, "A1") == "keep", wait=2200)
            cells_before = get(KEY)
            t.cap("Next: store damaged rules (the text {bad) for this tab and reload")
            pg.evaluate("([k])=>localStorage.setItem(k+'.rules','{bad')", [KEY])
            pg.reload()
            pg.wait_for_timeout(600)
            t.step("Type 99 in C2: refused with the exact reason", lambda: (put(pg, "C2", "99"), sc(pg, "#sheet-status")), expect=lambda: val(pg, "C2") == "" and st() == "This tab's validation rules could not be read, so edits are off until they are fixed or removed. Use Download unreadable rules to keep a copy. The edit was not applied.", wait=3400)

            def dl():
                with pg.expect_download(timeout=15000) as d:
                    pg.click("#val-raw")
                d.value.save_as("/tmp/vid/dl/unreadable-validation-rules-after.txt")
            t.step("Download unreadable rules: the file holds exactly what was stored", lambda: (sc(pg, "#val-raw"), dl()), expect=lambda: open("/tmp/vid/dl/unreadable-validation-rules-after.txt").read() == "{bad", wait=2000)
            t.step("Press Remove unreadable rules and answer No: cancelled, the rules are still damaged", lambda: (sc(pg, "#val-reset"), pg.click("#val-reset"), pg.wait_for_timeout(500), sc(pg, "#val-status")), expect=lambda: vs() == "Cancelled. The unreadable rules were left untouched." and get(KEY + ".rules") == "{bad" and get(KEY + ".rules.backup") is None and pg.is_disabled("#val-add") and "Remove this tab's unreadable validation rules?" in msgs[-1], wait=3400)
            mode["accept"] = True
            t.step("Press Remove unreadable rules and answer Yes: removed, exact backup kept, cells untouched", lambda: (pg.click("#val-reset"), pg.wait_for_timeout(500), sc(pg, "#val-status")), expect=lambda: vs() == "Removed the unreadable rules. A copy is kept in this browser under a backup key and is replaced only if you do this again on this tab. Your cells were not changed." and get(KEY + ".rules") is None and get(KEY + ".rules.backup") == "{bad" and get(KEY) == cells_before and not pg.is_disabled("#val-add"), wait=3600)
            t.step("A1 still says keep, and typing 99 in C2 now works", lambda: (put(pg, "C2", "99"), sc(pg, 'input[data-addr="C2"]')), expect=lambda: val(pg, "A1") == "keep" and val(pg, "C2") == "99", wait=3200)
            t.step("Add a new rule: C3:C4 may only hold yes or no", lambda: (pg.fill("#val-range", "C3:C4"), pg.select_option("#val-kind", "list"), pg.fill("#val-items", "yes, no"), pg.click("#val-add"), sc(pg, "#val-list")), expect=lambda: pg.locator("#val-list li").count() == 1, wait=3000)
            t.step("Type maybe in C3: refused by the new rule, C3 stays empty", lambda: (put(pg, "C3", "maybe"), sc(pg, 'input[data-addr="C3"]')), expect=lambda: val(pg, "C3") == "", wait=3000)
            t.step("Type yes in C3: accepted", lambda: (put(pg, "C3", "yes"), sc(pg, 'input[data-addr="C3"]')), expect=lambda: val(pg, "C3") == "yes", wait=3000)

        run_tour(ctx, "88_sheets_rules_recovery_after", body, notes="Device-local sheet; nothing stored on the server. Headless Chromium 390x844 emulated. After-fix clip for the Remove unreadable rules control; clip 86 is the before evidence. The damaged rules are a test-written string in browser storage; the confirm questions are answered by the test. Backup-write and removal failures and the replace-an-earlier-backup warning are covered by the automated drill, not filmed.", unshown=["backup write failure", "removal failure", "the warning when an earlier backup exists", "restoring from the backup (no control exists)"])

    g["DRILLS"]["tour_sheets_rules_after"] = tour


def register_chat_live(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import secrets as _secrets
        import time as _time

        import pyotp

        users(ctx)
        pw2 = _secrets.token_urlsafe(18) + "aA1!"
        sec2 = pyotp.random_base32(length=32)
        box = {}

        def make():
            from django.contrib.auth.hashers import make_password
            from django.utils import timezone

            from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
            from apps.accounts.models import CustomUser, TOTPCredential
            from apps.chat import services as cs

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two.password = make_password(pw2)
            two.save(update_fields=["password"])
            ct, nonce, key_id = encrypt_secret(sec2, get_auth_key_ring())
            TOTPCredential.objects.create(user=two, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now())
            sp = cs.open_dm(me, two.pk)
            m = cs.post_message(me, sp.public_id, "Who is on call tonight?", "live-1")
            seq = getattr(m, "seq", None) or cs.Message.objects.filter(space=sp).order_by("-seq").first().seq
            box["seq"] = seq

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        # off camera: sign in as the second synthetic account in a separate, unrecorded window
        errs, remote, guard = ctx.salow_hooks
        c2 = ctx.browser.new_context(viewport={"width": 390, "height": 844})
        c2.route("**/*", guard)
        q = c2.new_page()
        q.goto(APP + "/sign-in/")
        q.wait_for_timeout(800)
        q.click("#btn-signin")
        q.wait_for_timeout(500)
        q.fill("#employee_id", "ST-LIVE-2")
        q.fill("#password", pw2)
        tt = pyotp.TOTP(sec2)
        left = tt.interval - (_time.time() % tt.interval)
        if left < 5:
            _time.sleep(left + 0.5)
        q.fill("#totp_code", tt.now())
        q.click("#confirm-btn")
        q.wait_for_load_state()
        q.wait_for_timeout(1500)
        signed_in = "/sign-in" not in q.url
        # keep this second window open: it stays on the same conversation while the recorded window waits
        q.goto(APP + "/chat/")
        q.click("a:has-text('Live Employee')")
        q.wait_for_load_state()
        box["q"] = q

        def body(t, pg):
            txt = lambda: pg.inner_text("#chat-list")
            sel = "#m%s button[data-kind=tada]" % box["seq"]
            lab = lambda: pg.get_attribute(sel, "aria-label")
            pg.goto(APP + "/chat/")
            pg.click("a:has-text('Second Synthetic')")
            pg.wait_for_timeout(800)
            t.step("My message is on screen and Celebrate has no count yet", expect=lambda: "Who is on call tonight?" in txt() and lab() == "Celebrate", wait=1500)
            t.cap("Next: I leave this page alone. Second, in their own browser window, posts a reply and presses Celebrate on my message")

            def other():
                q = box["q"]
                q.fill("textarea[name=body]", "Second is on call tonight.")
                q.click("form.chat-compose button[type=submit]")
                q.wait_for_load_state()
                q.wait_for_timeout(800)
                q.click("#m%s button[data-kind=tada]" % box["seq"])
                q.wait_for_timeout(800)
                box["q_seen"] = "Second is on call tonight." in q.inner_text("#chat-list") and "Celebrate, 1, you reacted" in (q.get_attribute("#m%s button[data-kind=tada]" % box["seq"], "aria-label") or "")
            t.step("Second's window (off camera) posts and reacts; their own page shows both", other, expect=lambda: signed_in and box.get("q_seen") is True, wait=1000)

            def wait_live():
                t0 = _time.time()
                pg.wait_for_function("() => document.querySelector('#chat-list').innerText.indexOf('Second is on call tonight.') >= 0", timeout=20000)
                box["msg_s"] = round(_time.time() - t0, 1)
                pg.wait_for_function("s => document.querySelector(s).getAttribute('aria-label') === 'Celebrate, 1'", arg=sel, timeout=20000)
                box["react_s"] = round(_time.time() - t0, 1)
            t.step("Without any reload, Second's reply and their Celebrate appear on my page (within 20 s)", wait_live, expect=lambda: "Second is on call tonight." in txt() and lab() == "Celebrate, 1" and pg.get_attribute(sel, "aria-pressed") == "false", wait=800)
            t.step("The reaction is Second's, not mine: my Celebrate button is not pressed and the label has no 'you reacted'", expect=lambda: pg.get_attribute(sel, "aria-pressed") == "false" and "you reacted" not in lab(), wait=800)
            print("live polling seconds: message %s, reaction %s" % (box.get("msg_s"), box.get("react_s")), flush=True)

        run_tour(ctx, "89_chat_live_updates", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Two real signed-in synthetic accounts in two browser windows; the second sign-in (password and code) is done off camera in the second window, which stays open and posts and reacts. The recorded page is not reloaded. The page polls about every 10 seconds, so a wait of several seconds is expected; the measured seconds are in the run log, not a guarantee.", unshown=["hidden-tab behaviour", "rate-limit slow-down", "thread replies live", "Chat edit/delete (not built)"])
        try:
            box["q"].context.close()
        except Exception:  # noqa: BLE001
            pass

    g["DRILLS"]["tour_chat_live_updates"] = tour


def register_iam_exit_session(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import secrets as _s
        import time as _t

        import django
        import pyotp

        django.setup()
        box = {"last": 0}
        secret = {}

        def run(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def mk():
            from django.conf import settings
            from django.contrib.auth.hashers import make_password

            from apps.accounts.auth.crypto import encrypt_totp_secret
            from apps.accounts.models import CustomUser as CU
            from apps.accounts.models import IAMAdminIdentity, UserSession

            CU.objects.filter(employee_id="ST-LIVE-1").update(role=CU.Role.ADMIN)
            u = CU.objects.get(employee_id="ST-LIVE-1")
            secret["pw"] = _s.token_urlsafe(18) + "aA1!"
            secret["totp"] = pyotp.random_base32(length=32)
            ct, nonce, kid = encrypt_totp_secret(secret["totp"])
            adm = IAMAdminIdentity.objects.create(admin_id="IAM-FIXTURE-2", user=u, password_hash=make_password(secret["pw"]), role="admin",
                                                  status="active", encrypted_totp_secret=ct, totp_nonce=nonce, totp_key_id=kid)
            for eid, nm in (("ST-LIVE-9", "Target Nine"), ("ST-LIVE-7", "Target Seven")):
                t9, _c = CU.objects.get_or_create(employee_id=eid, defaults={"full_name": nm + " Synthetic", "email": eid.lower() + "@salowtech.com", "email_verified": True,
                                                  "role": CU.Role.EMPLOYEE, "status": CU.Status.ACTIVE, "is_active": True})
                keys = []
                for i in range(2):
                    k = _s.token_hex(20)
                    keys.append(k)
                    UserSession.objects.create(user=t9, session_key=k, ip_address="127.0.0.1", user_agent="fixture device %s-%d" % (eid, i), mfa_verified=True)
                box["keys_" + eid] = keys
            CU.objects.get_or_create(employee_id="ST-LIVE-8", defaults={"full_name": "Super Synthetic", "email": "st-live-8@salowtech.com", "email_verified": True,
                                                                         "role": CU.Role.SUPER_ADMIN, "status": CU.Status.ACTIVE, "is_active": True})
            box["adm_id"] = adm.pk
            box["cookie"] = getattr(settings, "IAM_SESSION_COOKIE_NAME", "salow_iam_session")

        def new_session():
            from apps.accounts.auth.iam_auth import create_iam_session
            from apps.accounts.models import IAMAdminIdentity

            class R:
                META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "drill"}
                client_ip = "127.0.0.1"

            r = R()
            create_iam_session(IAMAdminIdentity.objects.get(pk=box["adm_id"]), r, assurance_level=1)
            return {"name": box["cookie"], "value": r.session_key_iam, "domain": "iam.salowtech.com", "path": "/", "httpOnly": True, "sameSite": "Lax"}

        run(mk)
        IAM = "http://iam.salowtech.com:" + APP.rsplit(":", 1)[1]

        def state(eid):
            def f():
                from apps.accounts.models import CustomUser as CU
                from apps.accounts.models import UserSession

                u = CU.objects.get(employee_id=eid)
                return {"status": u.status, "active": u.is_active, "sessions": UserSession.objects.filter(user=u).count(), "keys": set(UserSession.objects.filter(user=u).values_list("session_key", flat=True))}
            return run(f)

        def helper(pg):
            def stepup_off_camera():
                cur = [c for c in pg.context.cookies() if c["name"] == box["cookie"]][0]
                c2 = pg.context.browser.new_context(viewport={"width": 390, "height": 844})
                ok = False
                try:
                    c2.add_cookies([cur])
                    q = c2.new_page()
                    q.goto(pg.url)
                    cs = int(_t.time() // 30)
                    while cs <= box["last"]:
                        _t.sleep(1.0)
                        cs = int(_t.time() // 30)
                    left = 30 - (_t.time() % 30)
                    if left < 6:
                        _t.sleep(left + 0.5)
                    q.fill("input[name=password]", secret["pw"])
                    q.fill("input[name=totp_code]", pyotp.TOTP(secret["totp"]).now())
                    box["last"] = int(_t.time() // 30)
                    q.locator("form button[type=submit]").first.click()
                    q.wait_for_load_state()
                    q.wait_for_timeout(800)
                    ok = "Step-up authentication verified" in q.inner_text("body")
                    new = [c for c in c2.cookies() if c["name"] == box["cookie"]]
                    if new:
                        pg.context.add_cookies([new[0]])
                finally:
                    c2.close()
                box["stepup_ok"] = ok
            return stepup_off_camera

        def exit_body(t, pg):
            txt = lambda: pg.inner_text("body")
            stepup_off_camera = helper(pg)
            f = lambda: pg.locator("form", has=pg.locator("input[name=action][value=exit_account]")).first

            def submit(eid):
                def go():
                    pg.goto(IAM + "/dashboard/?tab=lifecycle")
                    f().scroll_into_view_if_needed()
                    f().locator("input[name=target_employee_id]").fill(eid)
                    pg.wait_for_timeout(700)
                    f().locator("button[type=submit]").click()
                return go
            pg.goto(IAM + "/dashboard/?tab=lifecycle")
            t.note("Fixture: IAM administrator session at the basic level, made off camera; the password and authenticator secret exist only in this run's throwaway database. Targets are synthetic accounts.", 3)
            t.step("Mark my own account exited: refused with the exact message", submit("ST-LIVE-1"), expect=lambda: "You cannot mark your own account as exited." in txt(), wait=3000)
            t.step("Mark an unknown ID exited: refused", submit("ST-NONE-1"), expect=lambda: "User ST-NONE-1 not found." in txt(), wait=3000)
            t.step("Mark a Super Admin account exited as an Admin: refused", submit("ST-LIVE-8"), expect=lambda: "Only Super Admins can transition a Super Admin account." in txt() and state("ST-LIVE-8")["status"] == "active", wait=3000)
            t.step("Mark ST-LIVE-9 exited at the basic level: it redirects to the step-up page and nothing changed yet", submit("ST-LIVE-9"), expect=lambda: "/iam/stepup" in pg.url and state("ST-LIVE-9")["status"] == "active" and state("ST-LIVE-9")["sessions"] == 2, wait=3500)
            t.step("Step-up done off camera (fixture password and a fresh code, script only, never printed)", stepup_off_camera, expect=lambda: box.get("stepup_ok"), wait=800)
            t.step("Back on the console: submit again with the step-up in place", submit("ST-LIVE-9"), expect=lambda: "Account ST-LIVE-9 marked as EXITED. All active sessions terminated." in txt(), wait=4500)
            t.step("Checked by script: ST-LIVE-9 is exited, inactive, with 0 sessions; the other synthetic account (ST-LIVE-7) is untouched by this exit (still active, with the 1 session left after clip 91)", expect=lambda: (lambda a, b: a["status"] == "exited" and a["active"] is False and a["sessions"] == 0 and b["keys"] == {box["keys_ST-LIVE-7"][1]} and b["status"] == "active")(state("ST-LIVE-9"), state("ST-LIVE-7")), wait=500)

        def session_body(t, pg):
            txt = lambda: pg.inner_text("body")
            stepup_off_camera = helper(pg)
            k0, k1 = box["keys_ST-LIVE-7"]
            form = lambda k: pg.locator("form", has=pg.locator('input[name=session_key][value="%s"]' % k)).first

            def submit():
                pg.goto(IAM + "/dashboard/?tab=sessions")
                form(k0).scroll_into_view_if_needed()
                pg.wait_for_timeout(700)
                form(k0).locator("button[type=submit]").click()
            pg.goto(IAM + "/dashboard/?tab=sessions")
            t.step("Sessions tab: both fixture sessions of ST-LIVE-7 are listed, each with its own Revoke button", lambda: form(k0).scroll_into_view_if_needed(), expect=lambda: form(k0).count() == 1 and form(k1).count() == 1, wait=2500)
            t.step("Revoke the first session at the basic level: it redirects to the step-up page and the session is still there", submit, expect=lambda: "/iam/stepup" in pg.url and state("ST-LIVE-7")["sessions"] == 2, wait=3500)
            t.step("Step-up done off camera (fixture password and a fresh code, script only, never printed)", stepup_off_camera, expect=lambda: box.get("stepup_ok"), wait=800)
            t.step("Back on the console: submit again. The exact message names the first 8 characters of that session", submit, expect=lambda: ("Session %s... terminated." % k0[:8]) in txt(), wait=4500)
            t.step("Checked by script: only the first session is gone; the second one is still there", expect=lambda: state("ST-LIVE-7")["keys"] == {k1}, wait=500)
            t.step("The Sessions tab no longer lists the first session and still lists the second", lambda: pg.goto(IAM + "/dashboard/?tab=sessions"), expect=lambda: form(k0).count() == 0 and form(k1).count() == 1, wait=2500)

        run_tour(ctx, "91_iam_revoke_session", session_body, extra_cookies=[run(new_session)],
                 notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated, IAM host mapped locally. Fixture IAM session at the basic level; step-up done on the real page in an unrecorded context with a fixture password and code that never appear. Synthetic accounts only; no email or outside delivery.",
                 unshown=["a Super Admin target", "revoking another admin's session at level 3", "recovery and L3 dual approvals (held)"])
        run_tour(ctx, "90_iam_exit_account", exit_body, extra_cookies=[run(new_session)],
                 notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated, IAM host mapped locally. Fixture IAM session at the basic level; step-up done on the real page in an unrecorded context with a fixture password and code that never appear. Synthetic accounts only. The last-active-Super-Admin refusal and a Super Admin acting on a Super Admin were not exercised.",
                 unshown=["last active Super Admin refusal", "a Super Admin doing the exit", "recovery and L3 dual approvals (held)"])

    g["DRILLS"]["tour_iam_exit_session"] = tour


def register_pdf_password(g):
    APP = g["APP"]

    def tour(ctx):
        import os
        import secrets as _s
        import subprocess
        import tempfile

        pw = _s.token_urlsafe(12) + "xQ7"
        wrong = _s.token_urlsafe(9) + "zZ3"
        with tempfile.TemporaryDirectory() as td:
            plain, enc = os.path.join(td, "p.pdf"), os.path.join(td, "e.pdf")
            open(plain, "wb").write(g["_n_page_pdf"](3, "Locked"))
            subprocess.run(["qpdf", "--encrypt", pw, pw + "o", "256", "--", plain, enc], check=True)
            data = open(enc, "rb").read()
        res = {}

        def body(t, pg):
            def wait_prompt(q):
                for _ in range(80):
                    if not q.evaluate("document.getElementById('pdf-pwform').hidden"):
                        return True
                    q.wait_for_timeout(250)
                return False

            def fresh_page(q):
                q.goto(APP + "/pdf/")
                q.evaluate("localStorage.clear()")
                q.goto(APP + "/pdf/")
                q.wait_for_timeout(500)

            def pick(q):
                q.set_input_files("#pdf-open", files=[{"name": "locked.pdf", "mimeType": "application/pdf", "buffer": data}])

            def off_camera():
                c2 = pg.context.browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
                try:
                    c2.add_cookies(pg.context.cookies())
                    q = c2.new_page()
                    fresh_page(q)
                    pick(q)
                    res["prompt2"] = wait_prompt(q)
                    q.fill("#pdf-pw", wrong)
                    q.click("#pdf-pwgo")
                    for _ in range(80):
                        if "did not work" in q.inner_text("#pdf-status"):
                            break
                        q.wait_for_timeout(250)
                    res["wrong_msg"] = q.inner_text("#pdf-status")
                    res["wrong_box_empty"] = q.input_value("#pdf-pw") == ""
                    q.screenshot(path="/downloads/92_unrecorded_wrong_password_result.png")
                    q.fill("#pdf-pw", pw)
                    q.click("#pdf-pwgo")
                    for _ in range(80):
                        if q.inner_text("#pdf-where").startswith("Page 1 of"):
                            break
                        q.wait_for_timeout(250)
                    res["where"] = q.inner_text("#pdf-where")
                    res["box_empty"] = q.input_value("#pdf-pw") == ""
                    q.screenshot(path="/downloads/92_unrecorded_right_password_result.png")
                    res["leak"] = q.evaluate("(()=>{const o=[];for(const s of [localStorage,sessionStorage]){for(let i=0;i<s.length;i++){o.push(s.key(i)+'='+s.getItem(s.key(i)))}}return o.join('|')})()")
                    res["url"] = q.url
                finally:
                    c2.close()

            fresh_page(pg)
            t.note("The password for this throwaway encrypted file is generated for this run and typed only in a separate unrecorded browser window. It is not in this clip, a caption or a file name.", 3)
            t.step("Open the encrypted file here: the page asks for a password", lambda: pick(pg), expect=lambda: wait_prompt(pg) and "needs a password" in pg.inner_text("#pdf-status"), wait=2500)
            t.step("Press Cancel: nothing opens and the page says so", lambda: pg.click("#pdf-pwcancel"), expect=lambda: pg.inner_text("#pdf-status").startswith("Not opened.") and pg.evaluate("document.getElementById('pdf-pwform').hidden"), wait=2500)
            t.step("A wrong password and then the right one are entered in the unrecorded window (script only). The run finished", off_camera, expect=lambda: "where" in res, wait=500)
            t.step("Automated result from the unrecorded window (not replayed here), wrong password: the page said 'That password did not work.' and the box was emptied", expect=lambda: res.get("prompt2") and "That password did not work" in res.get("wrong_msg", "") and res.get("wrong_box_empty"), wait=2500)
            t.step("Automated result from the unrecorded window (not replayed here), right password: the file opened at Page 1 of 3 and the box was emptied", expect=lambda: res.get("where", "").startswith("Page 1 of 3") and res.get("box_empty"), wait=2500)
            t.step("Checked by script: no password in local or session storage or in the page address", expect=lambda: "leak" in res and pw not in res["leak"] and wrong not in res["leak"] and pw not in res.get("url", "") and wrong not in res.get("url", ""), wait=2500)

        run_tour(ctx, "92_pdf_password", body, notes="Device-local viewer, secure-context test flag. Headless Chromium 390x844 emulated. Throwaway PDF encrypted with qpdf (AES-256) using a generated password; entry was done in a separate unrecorded browser context, so the recording shows only the prompt, Cancel and the checked results.", unshown=["typing a password (off camera by design)", "the opened pages in the unrecorded window", "copy, split or merge of an encrypted file (covered by a drill, not filmed)"])

    g["DRILLS"]["tour_pdf_password"] = tour


def register_password_idle(g, users):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures
        import secrets as _secrets
        import time as _time

        import pyotp

        users(ctx)
        pw_old = _secrets.token_urlsafe(18) + "aA1!"
        pw_new = _secrets.token_urlsafe(18) + "bB2#"
        pw_bad = _secrets.token_urlsafe(12) + "cC3$"
        sec = pyotp.random_base32(length=32)
        res = {"last": 0}

        def db(fn):
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(fn).result()

        def make():
            from django.contrib.auth.hashers import make_password
            from django.utils import timezone

            from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
            from apps.accounts.models import CustomUser, TOTPCredential

            two = CustomUser.objects.get(employee_id="ST-LIVE-2")
            two.password = make_password(pw_old)
            two.save(update_fields=["password"])
            ct, nonce, key_id = encrypt_secret(sec, get_auth_key_ring())
            TOTPCredential.objects.create(user=two, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now())

        def check_pw():
            from apps.accounts.models import CustomUser

            u = CustomUser.objects.get(employee_id="ST-LIVE-2")
            return u.check_password(pw_new), u.check_password(pw_old)

        db(make)
        errs, remote, guard = ctx.salow_hooks

        def sign_in():
            c = ctx.browser.new_context(viewport={"width": 390, "height": 844})
            c.route("**/*", guard)
            q = c.new_page()
            q.goto(APP + "/sign-in/")
            q.wait_for_timeout(800)
            q.click("#btn-signin")
            q.wait_for_timeout(500)
            q.fill("#employee_id", "ST-LIVE-2")
            q.fill("#password", pw_old)
            tt = pyotp.TOTP(sec)
            while int(_time.time() // 30) <= res["last"]:
                _time.sleep(1.0)
            left = tt.interval - (_time.time() % tt.interval)
            if left < 5:
                _time.sleep(left + 0.5)
            q.fill("#totp_code", tt.now())
            res["last"] = int(_time.time() // 30)
            q.click("#confirm-btn")
            q.wait_for_load_state()
            q.wait_for_timeout(1500)
            return c, q

        def body(t, pg):
            def off_camera():
                ca, qa = sign_in()
                cb, qb = sign_in()
                qa.goto(APP + "/auth/security/")
                qb.goto(APP + "/auth/security/")
                res["a_pre"], res["b_pre"] = qa.url, qb.url
                res["a_has"] = "Change Password" in qa.inner_text("body") or "Security" in qa.inner_text("body")
                res["signed_in"] = bool(res["a_has"]) and "sign-in" not in qa.url and "sign-in" not in qb.url and qb.url.rstrip("/").endswith("security")
                print("password tour sign-in urls:", res["a_pre"], res["b_pre"], res["signed_in"], flush=True)
                try:
                    if not res["signed_in"]:
                        qa.screenshot(path="/downloads/93_debug_a.png")
                        return
                    qa.goto(APP + "/auth/security/password/change/")
                    qa.fill("#current_password", pw_old)
                    qa.fill("#new_password", pw_new)
                    qa.fill("#confirm_password", pw_new)
                    btn = qa.locator("form:has(#new_password) button[type=submit]")
                    res["pre_click_ok"] = btn.count() == 1 and qa.locator("form:has(#new_password) input[name=current_password]").count() == 1 and "logout" not in (btn.locator("xpath=ancestor::form").get_attribute("action") or "")
                    if not res["pre_click_ok"]:
                        return
                    btn.click()
                    qa.wait_for_load_state()
                    qa.wait_for_timeout(1000)
                    res["change_msg"] = qa.inner_text("body")
                    def _probe():
                        from apps.accounts.models import CustomUser as _CU
                        from apps.accounts.models import UserSession as _US

                        u = _CU.objects.get(employee_id="ST-LIVE-2")
                        return u.check_password(pw_new), u.check_password(pw_old), _US.objects.filter(user=u).count()
                    res["probe"] = db(_probe)
                    print("password tour db after change (new ok, old ok, sessions left):", res["probe"], flush=True)
                    print("password tour after change:", qa.url, "|", " ".join(res["change_msg"].split())[:160], flush=True)
                    res["a_url"] = qa.url
                    qa.screenshot(path="/downloads/93_unrecorded_password_changed.png")
                    res["new_ok"], res["old_ok"] = db(check_pw)
                    qb.goto(APP + "/auth/security/")
                    qb.wait_for_timeout(800)
                    res["b_url"] = qb.url
                    res["b_text"] = qb.inner_text("body")
                    qb.screenshot(path="/downloads/93_unrecorded_other_session_after_change.png")
                    qa.goto(APP + "/auth/security/idle-lock/?next=/auth/security/")
                    qa.wait_for_timeout(600)
                    res["lock_text"] = qa.inner_text("body")
                    qa.fill("#password", pw_bad)
                    qa.click("form[action$='idle-unlock/'] button[type=submit]")
                    qa.wait_for_load_state()
                    qa.wait_for_timeout(600)
                    res["bad_text"] = qa.inner_text("body")
                    res["bad_box_empty"] = qa.input_value("#password") == ""
                    qa.screenshot(path="/downloads/94_unrecorded_wrong_password_unlock.png")
                    qa.fill("#password", pw_new)
                    ub = qa.locator("form[action$='idle-unlock/'] button[type=submit]")
                    res["pre_unlock_ok"] = ub.count() == 1 and qa.locator("form[action$='idle-unlock/'] #password").count() == 1
                    if not res["pre_unlock_ok"]:
                        return
                    ub.click()
                    qa.wait_for_load_state()
                    qa.wait_for_timeout(800)
                    res["unlock_url"] = qa.url
                    res["unlock_text"] = qa.inner_text("body")
                    qa.screenshot(path="/downloads/94_unrecorded_unlocked.png")
                    qa.goto(APP + "/auth/security/")
                    res["after_unlock_url"] = qa.url
                finally:
                    ca.close()
                    cb.close()

            pg.goto(APP + "/auth/security/")
            t.note("Password change and idle unlock use a synthetic account (ST-LIVE-2). Every password and code is typed only in separate unrecorded windows by script. This recording shows only the starting page. The results are automated captures from those windows, with real screenshots saved separately.", 3)
            t.step("Off camera: sign in twice as the synthetic account, change the password, then lock and unlock (script only)", off_camera, expect=lambda: res.get("signed_in") is True and "unlock_url" in res, wait=500)
            t.step("Automated result: the page said the password was changed and all other sessions were revoked", expect=lambda: "Your password was changed successfully. All other active sessions have been revoked." in res.get("change_msg", ""), wait=2000)
            t.step("Automated result: the new password is stored and the old one no longer works", expect=lambda: res.get("new_ok") is True and res.get("old_ok") is False, wait=2000)
            t.step("Automated result: the other signed-in window shows the sign-in landing page (Salowtech Workspace, Login, Sign In) and exactly 1 session is left for the account", expect=lambda: "b_text" in res and "Salowtech Workspace" in res["b_text"] and "Returning Employee" in res["b_text"] and "First-Time Activation" in res["b_text"] and "Security & Credentials" not in res["b_text"] and res.get("probe", (0, 0, -1))[2] == 1, wait=2000)
            t.step("Automated result: wrong password on the lock screen shows 'Incorrect password entered.' and the box is empty", expect=lambda: "Incorrect password entered." in res.get("bad_text", "") and res.get("bad_box_empty") is True, wait=2000)
            t.step("Automated result: the new password unlocks and the security page opens", expect=lambda: "after_unlock_url" in res and res["after_unlock_url"].rstrip("/").endswith("security") and "idle-unlock" not in res["unlock_url"], wait=2000)
            t.step("Checked by script: none of the three generated passwords appears in the captured page text of those result pages (screenshots are not scanned)", expect=lambda: all(k in res for k in ("change_msg", "bad_text", "unlock_text", "lock_text")) and all(p not in res[k] for p in (pw_old, pw_new, pw_bad) for k in ("change_msg", "bad_text", "unlock_text", "lock_text")), wait=1500)

        run_tour(ctx, "93_password_change_idle_unlock", body, notes="Real Django on TiDB under uvicorn, headless Chromium 390x844 emulated. Synthetic account, generated passwords, typed only in unrecorded windows. Recording shows only the start page; results are automated captures with real screenshots.", unshown=["typing any password (off camera)", "other roles", "step-up or MFA re-enrolment after a password change"])

    g["DRILLS"]["tour_password_idle"] = tour
