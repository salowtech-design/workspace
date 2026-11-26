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
        self.cap(text)
        self.pg.wait_for_timeout(600)
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
            self.cap("DID NOT WORK: " + text)
            self.pg.wait_for_timeout(1500)
        self.log.append({"step": text, "ok": ok, "detail": detail})
        return ok

    def note(self, text, secs=2.2):
        """A caption only (for what is not built or not shown)."""
        self.cap(text)
        self.pg.wait_for_timeout(int(secs * 1000))
        self.log.append({"step": text, "ok": None, "detail": "note"})


def run_tour(ctx, name, body, w=390, h=844, scale=2, notes=""):
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
    res = {"name": name, "seconds": round(secs), "bytes": size, "page_errors": perr[:3], "notes": notes, "steps": log}
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
