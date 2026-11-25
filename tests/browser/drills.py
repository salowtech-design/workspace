"""Browser drills for the device-local modules (Slides, Sheets). Each check prints PASS or FAIL with a short reason.
Exit code is 1 if any check fails. Run through run.sh, which supplies a live server and throwaway secrets.
Not in CI. Needs playwright and Chromium. Secrets are read from a 0600 file and never printed."""

import json
import os
import sys
import time

import pyotp
from playwright.sync_api import sync_playwright

APP = os.environ["DRILL_APP"]
SECRETS = json.load(open(os.environ["DRILL_SECRETS"]))
SHOTS = os.environ.get("DRILL_SHOTS", "")
results = []
_SECRET_VALUES = [v for v in SECRETS.values() if v]


def scrub(text):
    """Remove the run's secrets (and the current TOTP codes) from any text before it is printed.
    Playwright errors can carry the values that were typed into a field, so nothing raw is printed."""
    text = str(text)
    vals = list(_SECRET_VALUES)
    try:
        t = pyotp.TOTP(SECRETS["emp"])
        now = time.time()
        vals += [t.at(now + d) for d in (-60, -30, 0, 30, 60)]
    except Exception:  # noqa: S110 - scrubbing must never raise
        pass
    for v in sorted(vals, key=len, reverse=True):
        text = text.replace(v, "[removed]")
    return text


def describe(ex):
    """Exception type plus the first line of its scrubbed message. No repr, no call log."""
    first = (str(ex).strip().splitlines() or [""])[0]
    return type(ex).__name__ + ": " + scrub(first)[:200]


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(
        ("PASS " if cond else "FAIL ") + name + ("" if cond else (": " + scrub(detail)[:300])),
        flush=True,
    )


def fresh_totp():
    """A code from a window with at least 5 s left, so it cannot expire between typing and submit."""
    t = pyotp.TOTP(SECRETS["emp"])
    left = t.interval - (time.time() % t.interval)
    if left < 5:
        time.sleep(left + 0.5)
    return t.now()


def login(pg):
    pg.goto(APP + "/sign-in/")
    pg.wait_for_timeout(800)
    pg.click("#btn-signin")
    pg.wait_for_timeout(500)
    pg.fill("#employee_id", "ST-LIVE-1")
    pg.fill("#password", SECRETS["emp_pw"])
    pg.fill("#totp_code", fresh_totp())
    pg.click("#confirm-btn")
    pg.wait_for_load_state()
    pg.wait_for_timeout(1200)


def fresh(ctx, path):
    pg = ctx.pages[0]
    pg.goto(APP + path)
    pg.evaluate("localStorage.clear()")
    pg.goto(APP + path)
    pg.wait_for_timeout(500)
    return pg


def stored(pg, root):
    return pg.evaluate(
        "localStorage.getItem(document.getElementById('%s').dataset.storeKey)" % root
    )


def active(pg):
    return pg.evaluate(
        "(()=>{const a=document.activeElement;return a&&a.dataset&&a.dataset.addr||a.id||a.tagName})()"
    )


def shot(pg, name):
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, name + ".png"))


def slides_show(ctx):
    pg = fresh(ctx, "/slides/")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Agenda")
    pg.press("#sl-text", "Tab")
    pg.fill("#sl-notes", "private note")
    pg.press("#sl-notes", "Tab")
    pg.click("#sl-new")
    pg.click("#sl-add-rect")
    pg.click("#sl-new")
    pg.click("#sl-add-ellipse")
    pg.click("#sl-list button:nth-child(1)")
    before = stored(pg, "slide-root")
    pg.click("#sl-present")
    pg.wait_for_timeout(300)
    check(
        "slides: show opens on current slide",
        pg.evaluate("document.getElementById('sl-show').open")
        and pg.inner_text("#sl-show-count") == "Slide 1 of 3",
    )
    pg.keyboard.press("ArrowRight")
    check("slides: ArrowRight advances", pg.inner_text("#sl-show-count") == "Slide 2 of 3")
    pg.keyboard.press("End")
    pg.keyboard.press("ArrowRight")
    check("slides: stops at last slide", pg.inner_text("#sl-show-count") == "Slide 3 of 3")
    pg.keyboard.press("Home")
    check(
        "slides: Home works after Next became disabled",
        pg.inner_text("#sl-show-count") == "Slide 1 of 3",
    )
    pg.evaluate("document.activeElement.blur()")
    for k in ["Control+z", "Control+y", "Delete", "Backspace"]:
        pg.keyboard.press(k)
    pg.wait_for_timeout(150)
    check(
        "slides: editing shortcuts do nothing behind the show",
        pg.evaluate("document.getElementById('sl-show').open")
        and stored(pg, "slide-root") == before,
    )
    check(
        "slides: notes are hidden by default",
        pg.evaluate("document.getElementById('sl-show-notes-wrap').hidden"),
    )
    pg.check("#sl-show-notes-on")
    pg.wait_for_timeout(200)
    check("slides: notes show when ticked", "private note" in pg.inner_text("#sl-show-notes"))
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(150)
    check(
        "slides: Escape closes and focus returns to Present",
        not pg.evaluate("document.getElementById('sl-show').open") and active(pg) == "sl-present",
    )
    pg.click("#sl-present")
    pg.wait_for_timeout(200)
    check(
        "slides: notes are off again on the next show",
        not pg.evaluate("document.getElementById('sl-show-notes-on').checked")
        and pg.evaluate("document.getElementById('sl-show-notes-wrap').hidden"),
    )
    pg.mouse.click(1095, 500)
    pg.wait_for_timeout(150)
    check(
        "slides: click outside the dialog closes it",
        not pg.evaluate("document.getElementById('sl-show').open"),
    )
    check("slides: the show never changed the stored deck", stored(pg, "slide-root") == before)


def slides_edit(ctx):
    pg = fresh(ctx, "/slides/")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "\U0001f600" * 30)
    pg.press("#sl-text", "Tab")
    pg.fill("#sl-w", "300")
    pg.press("#sl-w", "Tab")
    broken = pg.evaluate(
        "(()=>{let n=0;document.querySelectorAll('#sl-canvas-wrap tspan').forEach(t=>{if(/[\\ud800-\\udbff](?![\\udc00-\\udfff])|(?<![\\ud800-\\udbff])[\\udc00-\\udfff]/.test(t.textContent))n++});return n})()"
    )
    check(
        "slides: emoji wrap keeps surrogate pairs whole",
        broken == 0 and pg.locator("#sl-canvas-wrap tspan").count() > 1,
    )
    pg.click("#sl-add-rect")
    pg.click("#sl-add-ellipse")
    order = lambda: pg.eval_on_selector_all(  # noqa: E731
        "#sl-items button", "b=>b.map(x=>x.textContent.split(':')[0].replace(/^[0-9. ]+/, ''))"
    )
    before = order()
    pg.click("#sl-back")
    after = order()
    check(
        "slides: Send backward swaps the item with its neighbour",
        before == ["Text", "Rectangle", "Ellipse"] and after == ["Text", "Ellipse", "Rectangle"],
        (before, after),
    )
    pg.click("#sl-dup")
    pg.wait_for_timeout(150)
    check(
        "slides: Duplicate adds a slide and focuses it in the list",
        pg.locator("#sl-list button").count() == 2
        and pg.evaluate("document.activeElement.getAttribute('aria-current')") == "true",
    )
    pg.reload()
    pg.wait_for_timeout(400)
    check("slides: both slides survive reload", pg.locator("#sl-list button").count() == 2)
    ids = pg.evaluate(
        "(()=>{const d=JSON.parse(localStorage.getItem(document.getElementById('slide-root').dataset.storeKey));const a=[];d.slides.forEach(s=>{a.push(s.id);s.elements.forEach(e=>a.push(e.id))});return [a.length,new Set(a).size]})()"
    )
    check("slides: ids stay unique after duplicate", ids[0] == ids[1] and ids[0] > 2, ids)


def sheets_sort_filter(ctx):
    pg = fresh(ctx, "/sheets/")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")
        pg.wait_for_timeout(80)

    for i, (n, q) in enumerate(
        [
            ("Name", "Qty"),
            ("pear", "3"),
            ("Apple", "10"),
            ("fig", "abc"),
            ("x", "1"),
            ("kiwi", "7"),
        ],
        1,
    ):
        setc("A%d" % i, n)
        setc("B%d" % i, q)
    col = lambda c: pg.eval_on_selector_all(  # noqa: E731
        'input[data-addr^="%s"]' % c, "(l)=>l.slice(0,6).map(x=>x.value)", c
    )
    vis = lambda: pg.evaluate(  # noqa: E731
        "Array.from(document.querySelectorAll('#sheet-root tr')).slice(1,8).map((r,i)=>r.hidden?null:i+1)"
    )
    setc("C2", "=B2*2")
    pg.fill("#sort-range", "A1:C6")
    pg.fill("#sort-col", "A")
    pg.click("#sort-go")
    pg.wait_for_timeout(120)
    check(
        "sheets: sort refuses a range with a formula",
        "formula" in pg.inner_text("#sheet-status") and col("A")[1] == "pear",
        pg.inner_text("#sheet-status"),
    )
    pg.fill("#sheet-bar", "")
    pg.click('input[data-addr="C2"]')
    pg.fill('input[data-addr="C2"]', "")
    pg.press('input[data-addr="C2"]', "Tab")
    pg.fill("#sort-range", "A1:B6")
    pg.fill("#sort-col", "B")
    pg.click("#sort-go")
    pg.wait_for_timeout(150)
    check(
        "sheets: sort by B puts numbers first and keeps rows together",
        col("B") == ["Qty", "1", "3", "7", "10", "abc"]
        and col("A") == ["Name", "x", "pear", "kiwi", "Apple", "fig"],
        (col("A"), col("B")),
    )
    pg.click("#sort-go")
    pg.wait_for_timeout(100)
    check(
        "sheets: sorting sorted data says nothing changed",
        "Already in that order" in pg.inner_text("#sheet-status"),
    )
    pg.click("#sheet-undo")
    pg.wait_for_timeout(100)
    check(
        "sheets: undo restores the old order",
        col("B") == ["Qty", "3", "10", "abc", "1", "7"],
        col("B"),
    )
    pg.select_option("#filter-op", "gt")
    pg.fill("#filter-val", "2")
    pg.fill("#filter-col", "B")
    pg.click("#filter-go")
    pg.wait_for_timeout(150)
    check(
        "sheets: filter hides non-matching rows only on screen",
        vis()[:6] == [1, 2, 3, None, None, 6],
        vis(),
    )
    with pg.expect_download() as dl:
        pg.click("#sheet-csv")
    rows = open(dl.value.path(), newline="").read().strip().split("\r\n")
    check(
        "sheets: CSV includes the rows the filter hides",
        len(rows) == 6 and rows[3] == "fig,abc",
        rows,
    )
    pg.click('input[data-addr="B1"]')
    pg.press('input[data-addr="B1"]', "Enter")
    check("sheets: Enter skips hidden rows", active(pg) == "B2", active(pg))
    pg.click('input[data-addr="B3"]')
    pg.fill('input[data-addr="B3"]', "0")
    pg.press('input[data-addr="B3"]', "Tab")
    pg.wait_for_timeout(200)
    check(
        "sheets: editing a row out of the filter moves focus to a visible row",
        active(pg) == "B2",
        active(pg),
    )
    check(
        "sheets: status explains the hidden row",
        "no longer matches" in pg.inner_text("#filter-status"),
    )
    pg.evaluate("document.activeElement.blur()")
    pg.keyboard.press("Control+z")
    pg.wait_for_timeout(200)
    check("sheets: undo shows the row again", vis()[2] == 3, vis())
    pg.click('input[data-addr="B3"]')
    pg.keyboard.press("Control+y")
    pg.wait_for_timeout(250)
    a = active(pg)
    check(
        "sheets: redo with grid focus that hides the row moves focus to a visible row",
        a == "B2",
        a,
    )
    pg.click("#filter-clear")
    check("sheets: show all rows restores every row", vis() == [1, 2, 3, 4, 5, 6, 7], vis())
    pg.fill("#filter-val", "")
    pg.click("#filter-go")
    check(
        "sheets: empty filter value is refused", "Enter a value" in pg.inner_text("#filter-status")
    )
    pg.fill("#filter-val", "2")
    pg.click("#filter-go")
    pg.reload()
    pg.wait_for_timeout(400)
    check("sheets: the filter resets on reload", vis() == [1, 2, 3, 4, 5, 6, 7], vis())


def selftest_scrub(ctx):
    """Forced failures that carry the real secrets. Only run when named; run.sh then greps the output for them."""
    pg = ctx.pages[0]
    check("selftest: scrub removes a password in a detail", False, "x " + SECRETS["emp_pw"] + " y")
    try:
        pg.fill("#does-not-exist", SECRETS["emp_pw"], timeout=300)
    except Exception as ex:
        check(
            "selftest: playwright error is described without the typed value", False, describe(ex)
        )
    raise RuntimeError("boom " + SECRETS["emp"] + " " + SECRETS["emp_pw"])


DRILLS = {
    "slides_show": slides_show,
    "slides_edit": slides_edit,
    "sheets_sort_filter": sheets_sort_filter,
    "selftest_scrub": selftest_scrub,
}


def main():
    names = sys.argv[1:] or [n for n in DRILLS if n != "selftest_scrub"]
    unknown = [n for n in names if n not in DRILLS]
    if unknown:
        print("unknown drill:", ", ".join(unknown), "| known:", ", ".join(DRILLS))
        return 2
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(
            channel="chromium",
            args=[
                "--no-proxy-server",
                "--host-resolver-rules=MAP app.salowtech.com 127.0.0.1, EXCLUDE localhost",
            ],
        )
        ctx = b.new_context(viewport={"width": 1100, "height": 900})
        ctx.set_default_timeout(8000)
        errs = []
        remote = []

        def only_loopback(route):
            host = route.request.url.split("/")[2].split(":")[0]
            if host in (
                "app.salowtech.com",
                "127.0.0.1",
                "localhost",
            ) or route.request.url.startswith(("data:", "blob:")):
                route.continue_()
            else:
                remote.append(host)
                route.abort()

        ctx.route("**/*", only_loopback)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append(scrub(e)))
        try:
            # Before any credential is typed: the server we reach must be on this machine.
            r = pg.goto(APP + "/health")
            addr = r.server_addr() if r else None
            if not addr or addr["ipAddress"] not in ("127.0.0.1", "::1"):
                print("ABORT: the app address is not loopback, nothing was typed")
                return 2
            login(pg)
        except Exception as ex:
            print("FAIL login:", describe(ex))
            return 1
        if "/sign-in" in pg.url:
            print("FAIL login: still on the sign-in page")
            return 1
        for n in names:
            try:
                DRILLS[n](ctx)
            except Exception as ex:  # a crashed drill is a failure, not a skip
                check(n + ": drill ran to the end", False, describe(ex))
        check("no uncaught page errors", not errs, errs[:2])
        check("no request left this machine", not remote, remote[:3])
        b.close()
    print("%d/%d checks passed in %.0f s" % (sum(results), len(results), time.time() - t0))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
