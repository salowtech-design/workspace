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


def run_tour(ctx, name, body, w=390, h=844, scale=2, notes="", unshown=()):
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
    errs, remote, guard = ctx.salow_hooks
    c.route("**/*", guard)
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
