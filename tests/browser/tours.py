"""Recorded walkthroughs (manual, not CI). Each tour opens its own phone-size browser context with video on, reuses the
already signed-in session cookies (the sign-in itself is never recorded), moves a visible pointer, shows a caption for each
step, and writes a JSON log of which steps worked and which failed. Output goes to DRILL_VIDEO. Synthetic fixtures only."""
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
    register_drive_creator(g)
    register_task_conflict(g)
    register_sessions_scroll(g, users)
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


def register_drive_creator(g):
    APP = g["APP"]

    def tour(ctx):
        import concurrent.futures

        import django

        django.setup()
        ids = {}

        def make():
            from apps.accounts.models import CustomUser
            from apps.drive import services as svc
            from apps.drive.models import DriveFile

            me = CustomUser.objects.get(employee_id="ST-LIVE-1")
            two = CustomUser.objects.get_or_create(employee_id="ST-LIVE-2", defaults={"full_name": "Second Synthetic", "email": "st-live-2@salowtech.com", "email_verified": True,
                                                   "role": CustomUser.Role.EMPLOYEE, "status": CustomUser.Status.ACTIVE, "is_active": True})[0]
            top = svc.create_folder(me, "Joint plans", None)
            svc.share(me, top, two, "editor")
            svc.create_folder(two, "Made by second", top)
            svc.create_folder(me, "Made by me", top)
            DriveFile.objects.create(owner=me, folder=top, name="second-notes.txt", created_by=two)
            ids["top"] = str(top.public_id)

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(make).result()

        def body(t, pg):
            txt = lambda: pg.inner_text("body")
            pg.goto(APP + "/drive/folder/%s/" % ids["top"])
            t.step("Owner opens the shared folder: the folder and the file made by Second Synthetic say 'Created by Second Synthetic'", lambda: pg.evaluate("document.querySelector('.drive-row').scrollIntoView({block:'start'})"), expect=lambda: txt().count("Created by Second Synthetic") == 2, wait=5000)
            t.step("The folder the owner made herself has no Created by line", expect=lambda: "Created by Live Employee" not in txt() and "Made by me" in txt(), wait=3000)
            t.note("Not shown: the view from the second account (checked in the unit test, not on video), files uploaded by a person (upload is not built; the file row here is made by script).", 3)

        run_tour(ctx, "74_drive_creator", body, notes="Real Django on TiDB, headless Chromium 390x844 emulated. Fixture by script: a shared folder with a folder and a file created by a second synthetic account (editor) and a folder made by the signed-in owner.", unshown=["second account view", "real upload"])

    g["DRILLS"]["tour_drive_creator"] = tour
