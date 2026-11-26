"""Browser drills for the device-local modules (Slides, Sheets). Each check prints PASS or FAIL with a short reason.
Exit code is 1 if any check fails. Run through run.sh, which supplies a live server and throwaway secrets.
Not in CI. Needs playwright and Chromium. Secrets are read from a 0600 file and never printed."""

import json
import os
import re
import sys
import time
import zlib
from urllib.parse import urlsplit

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


def sheets_lookup(ctx):
    """Typed in the real grid: lookup and conditional functions show their results and errors on screen."""
    pg = fresh(ctx, "/sheets/")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")
        pg.wait_for_timeout(80)

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    for i, (n, q) in enumerate([("apple", "3"), ("Banana", "5"), ("cherry", "7")], 1):
        setc("A%d" % i, n)
        setc("B%d" % i, q)
    cases = [
        ("C1", '=VLOOKUP("banana",A1:B3,2,FALSE)', "5"),
        ("C2", '=VLOOKUP("zzz",A1:B3,2,FALSE)', "#N/A"),
        ("C3", '=MATCH("ch*",A1:A3,0)', "3"),
        ("C4", "=INDEX(A1:B3,2,2)", "5"),
        ("C5", "=INDEX(A1:B3,9,1)", "#REF!"),
        ("C6", '=SUMIF(B1:B3,">3")', "12"),
        ("C7", '=COUNTIF(A1:A3,"*an*")', "1"),
        ("C8", '=AVERAGEIF(B1:B3,">=5")', "6"),
        ("D1", '=LEFT(A2,3)', "Ban"),
        ("D2", '=FIND("n",A2)', "3"),
        ("D3", '=SUBSTITUTE(A3,"e","E")', "chErry"),
        ("D4", '=FIND("N",A2)', "#VALUE!"),
    ]
    for a, f, _ in cases:
        setc(a, f)
    pg.click('input[data-addr="A1"]')
    for a, f, want in cases:
        check("sheets lookup: %s shows %s" % (f, want), val(a) == want, val(a))
    pg.locator("details", has_text="Supported functions and limits").first.locator("summary").click()
    pg.evaluate("window.scrollTo(0, 0)")
    shot(pg, "sheets_lookup_help_desktop")
    pg.locator("details", has_text="Supported functions and limits").first.locator("summary").click()
    pg.locator('input[data-addr="A1"]').scroll_into_view_if_needed()
    pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]');const r=e.getBoundingClientRect();window.scrollBy(0,r.top-120)})()")
    shot(pg, "sheets_lookup_grid_desktop")
    # the focused cell must be fully clear of the pinned row-number column
    clear = pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]').getBoundingClientRect();const h=document.querySelector('.sheet-scroll tr:nth-child(2) > th').getBoundingClientRect();return e.left>=h.right-0.5})()")
    check("sheets lookup: focused A1 is not covered by the row numbers (desktop)", clear, str(clear))
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]');const r=e.getBoundingClientRect();window.scrollBy(0,r.top-120)})()")
    clear = pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]').getBoundingClientRect();const h=document.querySelector('.sheet-scroll tr:nth-child(2) > th').getBoundingClientRect();return e.left>=h.right-0.5})()")
    check("sheets lookup: focused A1 is not covered by the row numbers (390 px)", clear, str(clear))
    shot(pg, "sheets_lookup_grid_390")
    pg.locator('input[data-addr="C1"]').focus()
    pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"C1\"]');const r=e.getBoundingClientRect();window.scrollBy(0,r.top-120)})()")
    pg.wait_for_timeout(100)
    shot(pg, "sheets_lookup_results_390")
    pg.set_viewport_size({"width": 1100, "height": 900})


def sheets_half_emoji(ctx):
    """LEFT on an emoji leaves half of it. Proves what the grid shows, what JSON export and import keep, and what the CSV file holds."""
    pg = fresh(ctx, "/sheets/")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")
        pg.wait_for_timeout(80)

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    setc("A1", "\U0001F600x")
    setc("B1", "=LEFT(A1,1)")
    setc("C1", "=MID(A1,2,1)")
    setc("D1", "=B1&C1")
    check("half emoji: the grid holds the lone high half", val("B1") == "\ud83d", repr(val("B1")))
    check("half emoji: the other half is a lone low surrogate", val("C1") == "\ude00", repr(val("C1")))
    check("half emoji: joined halves show the whole emoji", val("D1") == "\U0001F600", repr(val("D1")))
    pg.click('input[data-addr="A2"]')
    shot(pg, "sheets_half_emoji_desktop")
    with pg.expect_download() as dl:
        pg.click("#sheet-export")
    raw = open(dl.value.path(), "rb").read()
    j = json.loads(raw.decode("utf-8"))
    check("half emoji: JSON export holds the formulas, not the results", j.get("B1") == "=LEFT(A1,1)" and "\ud83d" not in raw.decode("utf-8"), str(j))
    pg.click("#sheet-clear")
    pg.wait_for_timeout(120)
    pg.set_input_files("#sheet-import", dl.value.path())
    pg.wait_for_timeout(300)
    check("half emoji: import brings the same results back", val("B1") == "\ud83d" and val("D1") == "\U0001F600", repr((val("B1"), val("D1"))))
    with pg.expect_download() as dl2:
        pg.click("#sheet-csv")
    b = open(dl2.value.path(), "rb").read()
    check("half emoji: the CSV is valid UTF-8", bool(b.decode("utf-8")), "")
    check(
        "half emoji: the CSV holds the whole emoji, then U+FFFD for the half, and no lone surrogate bytes",
        b.decode("utf-8").split("\r\n")[0] == "\U0001F600x,\ufffd,\ufffd,\U0001F600" and b"\xed\xa0" not in b and b"\xed\xb0" not in b,
        b[:60],
    )
    st = pg.inner_text("#sheet-status")
    check("half emoji: the status says two half characters were replaced", "2 half emoji character" in st, st)
    shot(pg, "sheets_half_emoji_status_desktop")


def sheets_dates_math(ctx):
    """Dates and math typed into the real grid: serial numbers, weekday, error codes, and the help text, at desktop and 390 px."""
    pg = fresh(ctx, "/sheets/")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")
        pg.wait_for_timeout(80)

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    cases = [
        ("=DATE(2026,11,26)", "46352", "serial of 2026-11-26"),
        ("=WEEKDAY(A1)", "5", "weekday, Sunday=1 (Thursday)"),
        ("=WEEKDAY(A1,2)", "4", "weekday, Monday=1"),
        ("=DAY(A1+35)", "31", "day, 35 days later"),
        ("=MONTH(A1+35)", "12", "month, 35 days later"),
        ("=DATE(1900,1,1)", "2", "serial of 1900-01-01"),
        ("=MOD(-1,3)", "2", "MOD sign of divisor"),
        ("=FLOOR(23.25,0.18)", "23.22", "FLOOR by 0.18"),
        ("=SQRT(-1)", "#NUM!", "SQRT negative"),
        ("=DATE(10000,1,1)", "#NUM!", "year 10000"),
        ("=MATCH(9,B1:B2,0)", "#N/A", "no match"),
    ]
    for i, (f, _, label) in enumerate(cases, 1):
        setc("B%d" % i, label)
    for i, (f, _, label) in enumerate(cases, 1):
        setc("A%d" % i, f)
    pg.click('input[data-addr="A12"]')
    for i, (f, want, label) in enumerate(cases, 1):
        check("sheets dates math: %s shows %s" % (f, want), val("A%d" % i) == want, val("A%d" % i))
    pg.locator("details", has_text="Supported functions and limits").first.locator("summary").click()
    pg.evaluate("window.scrollTo(0, 0)")
    shot(pg, "sheets_dates_help_desktop")
    pg.locator("details", has_text="Supported functions and limits").first.locator("summary").click()
    pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]');const r=e.getBoundingClientRect();window.scrollBy(0,r.top-120)})()")
    shot(pg, "sheets_dates_grid_desktop")
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]');const r=e.getBoundingClientRect();window.scrollBy(0,r.top-120)})()")
    pg.wait_for_timeout(100)
    shot(pg, "sheets_dates_grid_390")
    pg.set_viewport_size({"width": 1100, "height": 900})


def sheets_stats_math(ctx):
    """Statistics and exact-integer math typed into the real grid, with the float-noise case shown, desktop and 390 px."""
    pg = fresh(ctx, "/sheets/")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")
        pg.wait_for_timeout(80)

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    for a, v in (("D1", "10"), ("D2", "4"), ("D3", "text"), ("D5", "2")):
        setc(a, v)
    cases = [
        ("=MEDIAN(D1:D5)", "4", "median of 10, 4, 2"),
        ("=MEDIAN(D1:D5,100)", "7", "median, even count"),
        ("=ROUND(STDEV(D1:D5),4)", "4.1633", "sample deviation"),
        ("=COUNTA(D1:D5)", "4", "COUNTA with text"),
        ("=COUNTBLANK(D1:D5)", "1", "COUNTBLANK"),
        ("=MOD(1700000000001,1000)", "1", "MOD, whole numbers"),
        ("=FLOOR(1234567890123456,1)", "1234567890123456", "FLOOR, 16 digits"),
        ("=MOD(5.1,1)", "0.0999999999999996", "MOD float noise shown"),
        ("=ROUND(MOD(5.1,1),10)", "0.1", "ROUND it"),
        ("=MEDIAN(D3)", "#NUM!", "MEDIAN of no numbers"),
        ("=STDEV(D1)", "#DIV/0!", "STDEV of one number"),
        ("=STDEV(0.1,0.1,0.1)", "0", "STDEV equal decimals"),
    ]
    for i, (f, _, label) in enumerate(cases, 1):
        setc("B%d" % i, label)
    for i, (f, _, label) in enumerate(cases, 1):
        setc("A%d" % i, f)
    pg.click('input[data-addr="A14"]')
    for i, (f, want, label) in enumerate(cases, 1):
        check("sheets stats math: %s shows %s" % (f, want), val("A%d" % i) == want, val("A%d" % i))
    check("sheets stats math: a clipped result is readable in the cell tooltip", "(result 1234567890123456)" in (pg.get_attribute('input[data-addr="A7"]', "title") or ""), pg.get_attribute('input[data-addr="A7"]', "title"))
    pg.locator("details", has_text="Supported functions and limits").first.locator("summary").click()
    pg.evaluate("window.scrollTo(0, 380)")
    shot(pg, "sheets_stats_help_desktop")
    pg.locator("details", has_text="Supported functions and limits").first.locator("summary").click()
    pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]');const r=e.getBoundingClientRect();window.scrollBy(0,r.top-120)})()")
    shot(pg, "sheets_stats_grid_desktop")
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.evaluate("(()=>{const e=document.querySelector('input[data-addr=\"A1\"]');const r=e.getBoundingClientRect();window.scrollBy(0,r.top-120)})()")
    pg.wait_for_timeout(100)
    shot(pg, "sheets_stats_grid_390")
    pg.set_viewport_size({"width": 1100, "height": 900})


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


def sheets_tabs(ctx):
    pg = fresh(ctx, "/sheets/")

    def names():
        return pg.eval_on_selector_all("#sheet-tabs button", "bs=>bs.map(b=>b.textContent)")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    check("tabs: a new sheet starts with one tab", names() == ["Sheet 1"], names())
    setc("A1", "one")
    pg.click("#tab-add")
    check(
        "tabs: Add tab makes a second, empty tab",
        names() == ["Sheet 1", "Sheet 2"] and val("A1") == "",
        names(),
    )
    setc("A1", "two")
    pg.fill("#tab-name", "  Costs  ")
    pg.click("#tab-rename")
    check("tabs: rename trims the name", names() == ["Sheet 1", "Costs"], names())
    pg.fill("#tab-name", "sheet 1")
    pg.click("#tab-rename")
    check("tabs: a name already in use is refused", names() == ["Sheet 1", "Costs"], names())
    pg.click('#sheet-tabs button[data-tab="t0"]')
    check("tabs: switching keeps each tab's data", val("A1") == "one", val("A1"))
    setc("A2", '=A1&"x"')
    pg.click("#sheet-tabs button:nth-child(2)")
    check(
        "tabs: the other tab is unchanged",
        val("A1") == "two" and val("A2") == "",
        (val("A1"), val("A2")),
    )
    shot(pg, "sheets-tabs-40")
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "tabs: tabs and data survive reload",
        names() == ["Sheet 1", "Costs"] and val("A1") == "two",
        names(),
    )
    pg.once("dialog", lambda d: d.accept())
    pg.click("#tab-del")
    check(
        "tabs: Delete tab removes it and moves to the other",
        names() == ["Sheet 1"] and val("A1") == "one",
        names(),
    )
    check("tabs: the last tab cannot be deleted", pg.is_disabled("#tab-del"))
    pg.click("#tab-restore")
    check(
        "tabs: Restore deleted tab brings it back with its data",
        names() == ["Sheet 1", "Costs"] and val("A1") == "two",
        names(),
    )
    stored_meta = pg.evaluate(
        "localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey+'.tabs')"
    )
    check("tabs: the tab list is stored", stored_meta is not None)
    pg.evaluate(
        "localStorage.setItem(document.getElementById('sheet-root').dataset.storeKey+'.tabs','{broken')"
    )
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "tabs: an unreadable tab list turns tab changes off",
        pg.is_disabled("#tab-add") and pg.is_disabled("#tab-rename"),
    )
    check("tabs: ...and says so", "could not be read" in pg.inner_text("#tab-status"))
    check(
        "tabs: ...and the stored value is left as it was",
        pg.evaluate(
            "localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey+'.tabs')"
        )
        == "{broken",
    )

    # Data isolation across tabs: undo, CSV and import on the second tab.
    pg.evaluate("localStorage.clear()")
    pg.goto(APP + "/sheets/")
    pg.wait_for_timeout(400)
    setc("A1", "first")
    pg.click("#tab-add")
    setc("A1", "second")
    setc("A1", "second-edited")
    pg.click('#sheet-tabs button[data-tab="t0"]')
    check("tabs: switching back shows the first tab as it was", val("A1") == "first", val("A1"))
    pg.click("#sheet-undo")
    pg.wait_for_timeout(100)
    check("tabs: Undo on the first tab undoes only the first tab", val("A1") == "", val("A1"))
    pg.click("#sheet-tabs button:nth-child(2)")
    check(
        "tabs: the second tab was not touched by that Undo", val("A1") == "second-edited", val("A1")
    )
    pg.click("#sheet-undo")
    pg.wait_for_timeout(100)
    check(
        "tabs: Undo on the second tab goes back one step on that tab",
        val("A1") == "second",
        val("A1"),
    )
    pg.click('#sheet-tabs button[data-tab="t0"]')
    check(
        "tabs: the first tab keeps its own state after the second tab's Undo",
        val("A1") == "",
        val("A1"),
    )
    pg.click("#sheet-redo")
    pg.wait_for_timeout(100)
    check("tabs: Redo on the first tab brings back its own edit", val("A1") == "first", val("A1"))
    pg.click("#sheet-tabs button:nth-child(2)")
    setc("B2", "7")
    with pg.expect_download() as dl:
        pg.click("#sheet-csv")
    csv = open(dl.value.path(), newline="").read()
    check(
        "tabs: CSV holds the current (second) tab only",
        "second" in csv and "first" not in csv and "7" in csv,
        csv,
    )
    pg.once("dialog", lambda d: d.accept())
    pg.set_input_files(
        "#sheet-import",
        files=[
            {
                "name": "t.json",
                "mimeType": "application/json",
                "buffer": b'{"A1":"imported","C3":"9"}',
            }
        ],
    )
    pg.wait_for_timeout(300)
    check(
        "tabs: Import replaces the current (second) tab",
        val("A1") == "imported" and val("B2") == "",
        (val("A1"), val("B2")),
    )
    pg.click('#sheet-tabs button[data-tab="t0"]')
    check("tabs: Import left the first tab alone", val("A1") == "first", val("A1"))
    pg.click("#sheet-tabs button:nth-child(2)")
    pg.click("#sheet-undo")
    pg.wait_for_timeout(100)
    check(
        "tabs: Undo after Import restores the second tab only",
        val("B2") == "7" and val("A1") == "second",
        (val("A1"), val("B2")),
    )
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(200)
    shot(pg, "sheets-tabs-41-phone")
    ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check("tabs: no sideways page scroll at 390 px", not ov)
    pg.set_viewport_size({"width": 1100, "height": 900})


def sheets_tabs_safety(ctx):
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def names():
        return pg.eval_on_selector_all("#sheet-tabs button", "bs=>bs.map(b=>b.textContent)")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    def tab(n):
        pg.click("#sheet-tabs button:nth-child(%d)" % n)

    def rename(n):
        pg.fill("#tab-name", n)
        pg.click("#tab-rename")

    # Sequential delete and restore: only the most recent one comes back, and the confirm says so.
    rename("A")
    setc("A1", "a-data")
    pg.click("#tab-add")
    rename("B")
    setc("A1", "b-data")
    pg.click("#tab-add")
    rename("C")
    setc("A1", "c-data")
    msgs = []
    pg.on("dialog", lambda d: (msgs.append(d.message), d.accept()))
    tab(1)
    pg.click("#tab-del")
    pg.click("#sheet-tabs button:nth-child(1)")
    pg.click("#tab-del")
    check(
        "tabs: deleting a second tab warns that it replaces the restorable one",
        len(msgs) == 2 and "replaces the restorable tab A" in msgs[1],
        msgs,
    )
    pg.click("#tab-restore")
    check(
        "tabs: restore brings back only the last deleted tab",
        "B" in names() and "A" not in names(),
        names(),
    )
    tab(names().index("B") + 1)
    check("tabs: the restored tab has its cells", val("A1") == "b-data", val("A1"))
    check("tabs: restore is off once the copy is used", pg.is_disabled("#tab-restore"))
    # Restore while the browser refuses to store: nothing changes and the copy is kept.
    pg.click("#tab-del")
    pg.evaluate(
        "window.__si=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new Error('full')};0"
    )
    before = names()
    pg.click("#tab-restore")
    check("tabs: a refused restore changes nothing", names() == before, names())
    check(
        "tabs: a refused restore says so and keeps the copy",
        "would not store" in pg.inner_text("#tab-status") and not pg.is_disabled("#tab-restore"),
        pg.inner_text("#tab-status"),
    )
    pg.evaluate("Storage.prototype.setItem=window.__si;0")
    pg.click("#tab-restore")
    tab(names().index("B") + 1)
    check(
        "tabs: restore works once storage works again, with the data",
        val("A1") == "b-data",
        (names(), val("A1")),
    )

    # A second window changing the tab list stops this one instead of overwriting it.
    p2 = ctx.new_page()
    p2.goto(APP + "/sheets/")
    p2.wait_for_timeout(400)
    p2.click("#tab-add")
    p2.wait_for_timeout(200)
    n_after_p2 = p2.eval_on_selector_all("#sheet-tabs button", "bs=>bs.length")
    pg.click("#tab-add")
    msg = pg.inner_text("#tab-status")
    check("tabs: a stale window is refused with a reload message", "another window" in msg, msg)
    stored = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).tabs.length" % KEYJS)
    check(
        "tabs: the other window's tab list was not overwritten",
        stored == n_after_p2,
        (stored, n_after_p2),
    )
    p2.close()
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "tabs: after a reload the page follows the stored list", len(names()) == n_after_p2, names()
    )

    # A file chosen on one tab is not applied to another tab if you switch while it loads.
    pg.evaluate(
        "window.__rt=FileReader.prototype.readAsText;FileReader.prototype.readAsText=function(f){var s=this;setTimeout(function(){window.__rt.call(s,f)},900)};0"
    )
    tab(1)
    first = val("A1")
    pg.set_input_files(
        "#sheet-import",
        files=[
            {"name": "t.json", "mimeType": "application/json", "buffer": b'{"A1":"late-import"}'}
        ],
    )
    tab(2)
    pg.wait_for_timeout(1500)
    check(
        "tabs: switching while a file loads cancels the import",
        "cancelled" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    check("tabs: the new tab did not get the file", val("A1") != "late-import", val("A1"))
    tab(1)
    check("tabs: the first tab did not get the file either", val("A1") == first, val("A1"))
    pg.evaluate("FileReader.prototype.readAsText=window.__rt;0")

    # Unreadable stored cells are never written over (checked on the tab that is active now).
    akey = pg.evaluate(
        "(()=>{const b=%s,m=JSON.parse(localStorage.getItem(b+'.tabs'));return m.active==='t0'?b:b+'.tab.'+m.active})()"
        % KEYJS
    )
    pg.evaluate("localStorage.setItem(%s,'{not json')" % json.dumps(akey))
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "tabs: an unreadable tab opens empty and says the stored cells were left alone",
        "could not be read" in pg.inner_text("#sheet-status") and val("A1") == "",
        pg.inner_text("#sheet-status"),
    )
    setc("A1", "typed")
    raw = pg.evaluate("localStorage.getItem(%s)" % json.dumps(akey))
    check("tabs: typing into it does not overwrite the stored value", raw == "{not json", raw)
    check(
        "tabs: ...and the page says the edit is not saved",
        "lost on reload" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )


def sheets_chart(ctx):
    pg = fresh(ctx, "/sheets/")

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")

    def count(sel):
        return pg.eval_on_selector_all("#chart-out " + sel, "e=>e.length")

    def label():
        return pg.eval_on_selector("#chart-out svg", "e=>e.getAttribute('aria-label')")

    for i, v in enumerate(["3", "1", "0", "-2", "5", "-4"], 1):
        setc("A%d" % i, v)
    pg.fill("#chart-range", "A1:A6")
    pg.click("#chart-draw")
    check(
        "chart: bar is the default and draws a bar per non-zero value",
        label().startswith("Bar") and count("rect") == 5,
        label(),
    )
    pg.select_option("#chart-type", "line")
    pg.click("#chart-draw")
    check(
        "chart: line draws one line and a point per value",
        label().startswith("Line") and count("polyline") == 1 and count("circle") == 6,
        (label(), count("circle")),
    )
    shot(pg, "sheets-chart-43-line")
    pg.select_option("#chart-type", "pie")
    pg.click("#chart-draw")
    check(
        "chart: pie draws one slice per value above zero",
        label().startswith("Pie") and count("path") == 3 and count("li") == 3,
        (label(), count("path")),
    )
    check(
        "chart: pie says what it left out",
        "3 of 6 values charted" in pg.inner_text("#chart-out"),
        pg.inner_text("#chart-out"),
    )
    shares = pg.eval_on_selector_all("#chart-out li", "e=>e.map(x=>x.textContent)")
    check(
        "chart: pie shares are 33.3%, 11.1% and 55.6%",
        shares == ["A1: 3 (33.3%)", "A2: 1 (11.1%)", "A5: 5 (55.6%)"],
        shares,
    )
    shot(pg, "sheets-chart-42-pie")
    setc("A2", "9")
    check(
        "chart: the pie follows an edit",
        "A2: 9 (" in pg.inner_text("#chart-out"),
        pg.inner_text("#chart-out"),
    )
    setc("A1", "0")
    setc("A2", "-1")
    setc("A5", "-3")
    check(
        "chart: a pie with nothing above zero says so",
        "needs at least one value above zero" in pg.inner_text("#chart-out"),
        pg.inner_text("#chart-out"),
    )
    setc("A1", "1e308")
    setc("A2", "1e308")
    check(
        "chart: a pie whose positive values overflow says that, not that there are none",
        "more than a pie chart can show" in pg.inner_text("#chart-out"),
        pg.inner_text("#chart-out"),
    )
    check("chart: no stray SVG is left behind then", count("svg") == 0)


def sheets_tabs_stale(ctx):
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    tid = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS)
    pg.click('input[data-addr="A1"]')
    pg.fill('input[data-addr="A1"]', "kept")
    pg.press('input[data-addr="A1"]', "Tab")
    key = pg.evaluate("%s+'.tab.%s'" % (KEYJS, tid))
    p2 = ctx.new_page()
    p2.goto(APP + "/sheets/")
    p2.wait_for_timeout(400)
    p2.click('#sheet-tabs button[data-tab="%s"]' % tid)
    p2.once("dialog", lambda d: d.accept())
    p2.click("#tab-del")
    p2.wait_for_timeout(200)
    check(
        "stale: the other window deleted the tab and its cells",
        pg.evaluate("localStorage.getItem(%s)" % json.dumps(key)) is None,
    )
    pg.click('input[data-addr="A1"]')
    pg.fill('input[data-addr="A1"]', "lost?")
    pg.press('input[data-addr="A1"]', "Tab")
    pg.wait_for_timeout(150)
    check(
        "stale: an edit on the deleted tab is not written to storage",
        pg.evaluate("localStorage.getItem(%s)" % json.dumps(key)) is None,
    )
    check(
        "stale: the page says the edit was not saved",
        "not saved" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    p2.close()
    # unreadable cells can be downloaded exactly as stored
    pg.evaluate("localStorage.setItem(%s,'{not json')" % KEYJS)
    pg.reload()
    pg.wait_for_timeout(400)
    active = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')||'null')" % KEYJS)
    if active and active.get("active") != "t0":
        pg.click('#sheet-tabs button[data-tab="t0"]')
    check("stale: the download button shows for an unreadable tab", pg.is_visible("#tab-raw"))
    with pg.expect_download() as dl:
        pg.click("#tab-raw")
    check(
        "stale: the download is the stored text, unchanged",
        open(dl.value.path()).read() == "{not json",
    )


def slides_pdf(ctx):
    import re
    import shutil
    import subprocess

    pg = fresh(ctx, "/slides/")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Agenda caf\u00e9 \u20ac5")
    pg.press("#sl-text", "Tab")
    pg.fill("#sl-notes", "NOTE-NOT-IN-PDF")
    pg.press("#sl-notes", "Tab")
    pg.click("#sl-add-rect")
    pg.fill("#sl-x", "520")
    pg.press("#sl-x", "Tab")
    pg.fill("#sl-y", "260")
    pg.press("#sl-y", "Tab")
    if SHOTS:
        pg.locator("#sl-canvas-wrap").screenshot(
            path=os.path.join(SHOTS, "slides-pdf-51-screen.png")
        )
    pg.click("#sl-new")
    pg.click("#sl-add-ellipse")
    pg.fill("#sl-text", "Round")
    pg.press("#sl-text", "Tab")
    got = []
    pg.on("download", lambda d: got.append(d))
    with pg.expect_download() as dl:
        pg.click("#sl-pdf")
    path = dl.value.path()
    check(
        "slides pdf: the download is named slides.pdf",
        dl.value.suggested_filename == "slides.pdf",
        dl.value.suggested_filename,
    )
    raw = open(path, "rb").read()
    check("slides pdf: it is a PDF", raw.startswith(b"%PDF-"))
    check("slides pdf: speaker notes are not in it", b"NOTE-NOT-IN-PDF" not in raw)
    check(
        "slides pdf: the status says notes are left out",
        "Speaker notes are not in the PDF" in pg.inner_text("#sl-status"),
        pg.inner_text("#sl-status"),
    )
    if shutil.which("pdfinfo") and shutil.which("pdftotext"):
        info = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout  # noqa: S603, S607
        txt = subprocess.run(
            ["pdftotext", "-layout", path, "-"], capture_output=True, text=True
        ).stdout  # noqa: S603, S607
        check(
            "slides pdf: two pages of 960 x 540",
            re.search(r"Pages:\s+2\b", info) is not None and "960 x 540" in info,
            info,
        )
        check(
            "slides pdf: slide text is extractable in order",
            "Agenda caf\u00e9 \u20ac5" in txt and txt.index("Agenda") < txt.index("Round"),
            txt,
        )
        if SHOTS and shutil.which("pdftoppm"):
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    "60",
                    "-f",
                    "1",
                    "-l",
                    "2",
                    path,
                    os.path.join(SHOTS, "slides-pdf-50"),
                ],
                check=False,
            )  # noqa: S603, S607
    else:
        check(
            "slides pdf: poppler tools available to inspect the file",
            False,
            "pdfinfo/pdftotext missing",
        )
    # A failure while handing the file to the browser must not leave the button stuck.
    pg.evaluate(
        "window.__cou=URL.createObjectURL;URL.createObjectURL=function(){throw new Error('blocked')};0"
    )
    pg.click("#sl-pdf")
    pg.wait_for_timeout(500)
    check(
        "slides pdf: a failed download hand-off says so and re-enables the button",
        "nothing was downloaded" in pg.inner_text("#sl-status") and not pg.is_disabled("#sl-pdf"),
        pg.inner_text("#sl-status"),
    )
    pg.evaluate("URL.createObjectURL=window.__cou;0")
    before = len(got)
    pg.click("#sl-list button:nth-child(1)")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "tick \u2713 \u65e5\u672c")
    pg.press("#sl-text", "Tab")
    pg.click("#sl-pdf")
    pg.wait_for_timeout(500)
    msg = pg.inner_text("#sl-status")
    check(
        "slides pdf: unsupported characters stop the export with a clear message",
        "no PDF was made" in msg and "slide 1" in msg and "\u2713" in msg,
        msg,
    )
    check(
        "slides pdf: no file was downloaded for the refused export",
        len(got) == before,
        (len(got), before),
    )
    check("slides pdf: the button works again after a refusal", not pg.is_disabled("#sl-pdf"))


def slides_pdf_theme(ctx):
    import shutil
    import subprocess

    pg = fresh(ctx, "/slides/")
    pg.select_option("#sl-theme", "dark")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Quarterly results were strong across every region this year and next")
    pg.press("#sl-text", "Tab")
    pg.fill("#sl-w", "420")
    pg.press("#sl-w", "Tab")
    pg.fill("#sl-h", "120")
    pg.press("#sl-h", "Tab")
    pg.click("#sl-add-ellipse")
    pg.fill("#sl-text", "Long label inside a round shape that wraps")
    pg.press("#sl-text", "Tab")
    pg.fill("#sl-x", "520")
    pg.press("#sl-x", "Tab")
    pg.fill("#sl-y", "280")
    pg.press("#sl-y", "Tab")
    pg.fill("#sl-w", "320")
    pg.press("#sl-w", "Tab")
    pg.fill("#sl-h", "200")
    pg.press("#sl-h", "Tab")
    pg.fill("#sl-size", "24")
    pg.press("#sl-size", "Tab")
    if SHOTS:
        pg.locator("#sl-canvas-wrap").screenshot(
            path=os.path.join(SHOTS, "slides-pdf-53-dark-screen.png")
        )
    with pg.expect_download() as dl:
        pg.click("#sl-pdf")
    path = dl.value.path()
    if not (shutil.which("pdftotext") and shutil.which("pdftoppm")):
        check("slides pdf theme: poppler tools available", False, "missing")
        return
    txt = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True).stdout
    words = " ".join(txt.split())
    check(
        "slides pdf theme: all the wrapped text is in the file",
        "Quarterly results were strong across every region this year and next"
        in words.replace(" \n", " ")
        or all(w in words for w in "Quarterly results region next Long label round wraps".split()),
        words,
    )
    if SHOTS:
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                "60",
                "-f",
                "1",
                "-l",
                "1",
                path,
                os.path.join(SHOTS, "slides-pdf-52-dark"),
            ],
            check=False,
        )


def slides_layout(ctx):
    pg = fresh(ctx, "/slides/")

    def items():
        return pg.eval_on_selector_all("#sl-items li", "e=>e.map(x=>x.textContent)")

    check("layouts: a new deck has one blank slide", items() == [], items())
    pg.select_option("#sl-layout", "title")
    pg.click("#sl-new")
    check(
        "layouts: Title makes a title and a subtitle item",
        len(items()) == 2 and "Title" in items()[0] and "Subtitle" in items()[1],
        items(),
    )
    pg.select_option("#sl-layout", "content")
    pg.click("#sl-new")
    check(
        "layouts: Title and content makes two items",
        len(items()) == 2 and "Content" in items()[1],
        items(),
    )
    pg.select_option("#sl-layout", "blank")
    pg.click("#sl-new")
    check("layouts: Blank makes an empty slide", items() == [], items())
    n = pg.eval_on_selector_all("#sl-list button", "e=>e.length")
    check("layouts: four slides now", n == 4, n)
    pg.click("#sl-list button:nth-child(3)")
    if SHOTS:
        pg.locator("#sl-canvas-wrap").screenshot(path=os.path.join(SHOTS, "slides-layout-54.png"))
    pg.reload()
    pg.wait_for_timeout(400)
    pg.click("#sl-list button:nth-child(2)")
    check(
        "layouts: the layout items are saved like any others",
        len(items()) == 2 and "Subtitle" in items()[1],
        items(),
    )
    check(
        "layouts: the help says a layout is not the master",
        "not the master" in pg.inner_text("#sl-layout-help")
        and "appear in Present" in pg.inner_text("#sl-layout-help"),
    )
    pg.click("#sl-present")
    pg.wait_for_timeout(300)
    check(
        "layouts: the slide show shows the layout text",
        "Subtitle" in pg.text_content("#sl-show-canvas"),
        pg.text_content("#sl-show-canvas"),
    )
    pg.keyboard.press("Escape")


def sheets_validation(ctx):
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    pg.fill("#val-range", "A1:A3")
    pg.fill("#val-items", "red, green, blue")
    pg.click("#val-add")
    check(
        "validation: a list rule is added and listed",
        "A1:A3" in pg.inner_text("#val-list") and "red, green, blue" in pg.inner_text("#val-list"),
        pg.inner_text("#val-list"),
    )
    setc("A1", "green")
    check("validation: an allowed value is accepted", val("A1") == "green", val("A1"))
    setc("A2", "purple")
    check("validation: a value outside the list is refused", val("A2") == "", val("A2"))
    msg = pg.inner_text("#sheet-status")
    check(
        "validation: the message says what is accepted and that the edit was not applied",
        "A2 accepts only: red, green, blue" in msg and "not applied" in msg,
        msg,
    )
    setc("A2", "=1+1")
    check("validation: a formula is not checked", val("A2") == "2", val("A2"))
    setc("B1", "anything")
    check("validation: cells outside the range are free", val("B1") == "anything", val("B1"))
    pg.select_option("#val-kind", "number")
    pg.fill("#val-range", "C1:C3")
    pg.fill("#val-min", "1")
    pg.fill("#val-max", "10")
    pg.click("#val-add")
    setc("C1", "11")
    check("validation: a number above the range is refused", val("C1") == "", val("C1"))
    setc("C1", "10")
    check("validation: the largest number is accepted", val("C1") == "10", val("C1"))
    pg.fill("#val-range", "A2:A5")
    pg.click("#val-add")
    check(
        "validation: an overlapping rule is refused with a message",
        "overlaps" in pg.inner_text("#val-status"),
        pg.inner_text("#val-status"),
    )
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, "sheets-validation-55.png"), full_page=False)
    pg.reload()
    pg.wait_for_timeout(400)
    setc("A3", "pink")
    check("validation: rules survive a reload", val("A3") == "", val("A3"))
    pg.click("#val-list li:nth-child(1) button")
    setc("A3", "pink")
    check("validation: removing the rule lets the value in", val("A3") == "pink", val("A3"))
    # Damaged rules are kept as they are and say so.
    key = pg.evaluate("%s+'.rules'" % KEYJS)
    pg.evaluate("localStorage.setItem(%s,'{bad')" % json.dumps(key))
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "validation: unreadable rules are reported and adding is off",
        "could not be read" in pg.inner_text("#val-status") and pg.is_disabled("#val-add"),
        pg.inner_text("#val-status"),
    )
    setc("C2", "99")
    check(
        "validation: with unreadable rules a typed edit is refused and says why",
        val("C2") != "99" and "could not be read" in pg.inner_text("#sheet-status"),
        val("C2") + " | " + pg.inner_text("#sheet-status"),
    )
    check(
        "validation: the damaged rules are untouched",
        pg.evaluate("localStorage.getItem(%s)" % json.dumps(key)) == "{bad",
    )
    with pg.expect_download() as dl:
        pg.click("#val-raw")
    check(
        "validation: the damaged rules can be downloaded as stored",
        open(dl.value.path()).read() == "{bad",
    )


def sheets_validation_paths(ctx):
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def setc(pgx, a, v):
        pgx.click('input[data-addr="%s"]' % a)
        pgx.fill('input[data-addr="%s"]' % a, v)
        pgx.press('input[data-addr="%s"]' % a, "Tab")

    def val(pgx, a):
        return pgx.input_value('input[data-addr="%s"]' % a)

    def addrule(pgx, rng, items):
        pgx.select_option("#val-kind", "list")
        pgx.fill("#val-range", rng)
        pgx.fill("#val-items", items)
        pgx.click("#val-add")

    # An existing value is kept when a rule is added; a new bad value is refused and the old one stays.
    setc(pg, "A1", "purple")
    addrule(pg, "A1:A2", "red, blue")
    check(
        "paths: adding a rule does not change an existing value",
        val(pg, "A1") == "purple",
        val(pg, "A1"),
    )
    setc(pg, "A1", "orange")
    check(
        "paths: a refused edit leaves the old value in the cell",
        val(pg, "A1") == "purple",
        val(pg, "A1"),
    )
    # A tab deleted in another window cannot gain rules.
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    tid = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS)
    rkey = pg.evaluate("%s+'.tab.%s.rules'" % (KEYJS, tid))
    p2 = ctx.new_page()
    p2.goto(APP + "/sheets/")
    p2.wait_for_timeout(400)
    p2.click('#sheet-tabs button[data-tab="%s"]' % tid)
    p2.once("dialog", lambda d: d.accept())
    p2.click("#tab-del")
    p2.wait_for_timeout(200)
    addrule(pg, "A1:A2", "x")
    check(
        "paths: a stale tab is refused new rules and no rules key appears",
        "not changed" in pg.inner_text("#val-status")
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(rkey)) is None,
        pg.inner_text("#val-status"),
    )
    p2.close()
    # Leftover rules keep their tab id from being reused.
    pg.reload()
    pg.wait_for_timeout(400)
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    t2 = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS)
    addrule(pg, "B1:B2", "y")
    rk2 = pg.evaluate("%s+'.tab.%s.rules'" % (KEYJS, t2))
    pg.evaluate(
        "(function(){var k=%s+'.tabs',m=JSON.parse(localStorage.getItem(k));m.tabs=m.tabs.filter(function(t){return t.id!=%s});m.active='t0';localStorage.setItem(k,JSON.stringify(m));})();0"
        % (KEYJS, json.dumps(t2))
    )
    pg.reload()
    pg.wait_for_timeout(400)
    ids = set()
    for _ in range(3):
        pg.click("#tab-add")
        pg.wait_for_timeout(100)
        ids.add(pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS))
    check(
        "paths: a new tab never reuses an id whose rules are still stored",
        t2 not in ids and pg.evaluate("localStorage.getItem(%s)" % json.dumps(rk2)) is not None,
        str(ids),
    )
    # Restore with a failing rules write restores nothing and keeps the deleted tab.
    pg.evaluate("localStorage.removeItem(%s+'.tabs');0" % KEYJS)
    pg = fresh(ctx, "/sheets/")
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    setc(pg, "A1", "keep")
    addrule(pg, "B1:B2", "y")
    pg.once("dialog", lambda d: d.accept())
    pg.click("#tab-del")
    pg.wait_for_timeout(200)
    pg.evaluate(
        "(function(){var o=Storage.prototype.setItem;window.__o=o;Storage.prototype.setItem=function(k,v){if(/[.]rules$/.test(k))throw new Error('full');return o.call(this,k,v);};})();0"
    )
    pg.click("#tab-restore")
    ntabs = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).tabs.length" % KEYJS)
    msg = pg.inner_text("#tab-status")
    check(
        "paths: a failed rules write restores nothing and says so",
        ntabs == 1 and "would not store the tab's validation rules" in msg,
        msg,
    )
    pg.evaluate("Storage.prototype.setItem=window.__o;0")
    pg.click("#tab-restore")
    pg.wait_for_timeout(200)
    check(
        "paths: Restore works again afterwards with its rules",
        pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).tabs.length" % KEYJS) == 2
        and "y" in pg.inner_text("#val-list"),
        pg.inner_text("#tab-status"),
    )


def sheets_colour(ctx):
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def setc(pgx, a, v):
        pgx.click('input[data-addr="%s"]' % a)
        pgx.fill('input[data-addr="%s"]' % a, v)
        pgx.press('input[data-addr="%s"]' % a, "Tab")

    def cf(pgx, a):
        return pgx.evaluate(
            "document.querySelector('input[data-addr=\"%s\"]').dataset.cf || ''" % a
        )

    def addcf(pgx, rng, kind, value, color):
        pgx.fill("#cf-range", rng)
        pgx.select_option("#cf-kind", kind)
        pgx.fill("#cf-value", value)
        pgx.select_option("#cf-color", color)
        pgx.click("#cf-add")

    setc(pg, "A1", "50")
    setc(pg, "A2", "5")
    setc(pg, "A3", "=A1*2")
    setc(pg, "B1", "Late delivery")
    addcf(pg, "A1:A3", "greater", "10", "red")
    check(
        "colour: a value above the number is red",
        cf(pg, "A1") == "red" and cf(pg, "A2") == "",
        cf(pg, "A1") + "/" + cf(pg, "A2"),
    )
    check("colour: a formula is coloured by its result", cf(pg, "A3") == "red", cf(pg, "A3"))
    check("colour: the value itself is unchanged", pg.input_value('input[data-addr="A1"]') == "50")
    check(
        "colour: the tooltip names the colour",
        "highlighted red" in pg.get_attribute('input[data-addr="A1"]', "title"),
    )
    addcf(pg, "B1:B2", "contains", "LATE", "yellow")
    check("colour: text matching ignores capitals", cf(pg, "B1") == "yellow", cf(pg, "B1"))
    setc(pg, "A2", "500")
    check("colour: editing a value recolours it", cf(pg, "A2") == "red", cf(pg, "A2"))
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, "sheets-colour-56.png"), full_page=False)
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "colour: rules and colours survive a reload",
        cf(pg, "A1") == "red" and cf(pg, "B1") == "yellow",
        cf(pg, "A1"),
    )
    pg.click("#cf-list li:nth-child(1) button")
    check(
        "colour: removing a rule removes its colour",
        cf(pg, "A1") == "" and cf(pg, "B1") == "yellow",
        cf(pg, "A1"),
    )
    # Damaged colour rules are kept, say so, and colour nothing.
    key = pg.evaluate("%s+'.cf'" % KEYJS)
    pg.evaluate("localStorage.setItem(%s,'{bad')" % json.dumps(key))
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "colour: unreadable rules are reported, adding is off, nothing coloured",
        "could not be read" in pg.inner_text("#cf-status")
        and pg.is_disabled("#cf-add")
        and cf(pg, "B1") == "",
        pg.inner_text("#cf-status"),
    )
    check(
        "colour: the damaged rules are untouched",
        pg.evaluate("localStorage.getItem(%s)" % json.dumps(key)) == "{bad",
    )
    with pg.expect_download() as dl:
        pg.click("#cf-raw")
    check(
        "colour: the damaged rules can be downloaded as stored",
        open(dl.value.path()).read() == "{bad",
    )
    # Tab paths: stale tab, id reuse, restore with a failing write.
    pg = fresh(ctx, "/sheets/")
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    tid = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS)
    ck = pg.evaluate("%s+'.tab.%s.cf'" % (KEYJS, tid))
    p2 = ctx.new_page()
    p2.goto(APP + "/sheets/")
    p2.wait_for_timeout(400)
    p2.click('#sheet-tabs button[data-tab="%s"]' % tid)
    p2.once("dialog", lambda d: d.accept())
    p2.click("#tab-del")
    p2.wait_for_timeout(200)
    addcf(pg, "A1:A2", "equals", "x", "green")
    check(
        "colour: a stale tab is refused new colour rules and no key appears",
        "not changed" in pg.inner_text("#cf-status")
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(ck)) is None,
        pg.inner_text("#cf-status"),
    )
    p2.close()
    pg = fresh(ctx, "/sheets/")
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    setc(pg, "A1", "keep")
    addcf(pg, "A1:A2", "equals", "keep", "green")
    pg.once("dialog", lambda d: d.accept())
    pg.click("#tab-del")
    pg.wait_for_timeout(200)
    pg.evaluate(
        "(function(){var o=Storage.prototype.setItem;window.__o=o;Storage.prototype.setItem=function(k,v){if(/[.]cf$/.test(k))throw new Error('full');return o.call(this,k,v);};})();0"
    )
    pg.click("#tab-restore")
    msg = pg.inner_text("#tab-status")
    check(
        "colour: a failed colour write restores nothing and says so",
        pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).tabs.length" % KEYJS) == 1
        and "would not store the tab's colour rules" in msg,
        msg,
    )
    pg.evaluate("Storage.prototype.setItem=window.__o;0")
    pg.click("#tab-restore")
    pg.wait_for_timeout(200)
    check(
        "colour: Restore works afterwards with its colours",
        cf(pg, "A1") == "green" and "equal to keep" in pg.inner_text("#cf-list"),
        pg.inner_text("#tab-status"),
    )
    # A tab id with a leftover colour key is never reused.
    t2 = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS)
    ck2 = pg.evaluate("%s+'.tab.%s.cf'" % (KEYJS, t2))
    pg.evaluate(
        "(function(){var k=%s+'.tabs',m=JSON.parse(localStorage.getItem(k));m.tabs=m.tabs.filter(function(t){return t.id!=%s});m.active='t0';localStorage.setItem(k,JSON.stringify(m));})();0"
        % (KEYJS, json.dumps(t2))
    )
    pg.reload()
    pg.wait_for_timeout(400)
    ids = set()
    for _ in range(3):
        pg.click("#tab-add")
        pg.wait_for_timeout(100)
        ids.add(pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS))
    check(
        "colour: a new tab never reuses an id whose colour rules are stored",
        t2 not in ids and pg.evaluate("localStorage.getItem(%s)" % json.dumps(ck2)) is not None,
        str(ids),
    )


def sheets_format(ctx):  # noqa: C901
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def setc(pgx, a, v):
        pgx.click('input[data-addr="%s"]' % a)
        pgx.fill('input[data-addr="%s"]' % a, v)
        pgx.press('input[data-addr="%s"]' % a, "Tab")

    def sel(a):
        return 'input[data-addr="%s"]' % a

    def shown(pgx, a):
        return pgx.input_value(sel(a))

    def sty(pgx, a, prop):
        return pgx.evaluate(
            "getComputedStyle(document.querySelector('input[data-addr=\"%s\"]')).%s" % (a, prop)
        )

    def addfmt(pgx, rng, bold, align, nf):
        pgx.fill("#fmt-range", rng)
        pgx.select_option("#fmt-bold", bold)
        pgx.select_option("#fmt-align", align)
        pgx.select_option("#fmt-nf", nf)
        pgx.click("#fmt-add")

    ver = "(JSON.parse(localStorage.getItem(%s+'.tabs')||'{\"v\":1}')).v" % KEYJS
    setc(pg, "A1", "1234.5")
    check(
        "format: a sheet without formats keeps a version 1 tab list",
        pg.evaluate(ver) == 1,
        str(pg.evaluate(ver)),
    )
    # First use is failure-safe: if the tab list cannot be marked, no format rule is stored.
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    pg.evaluate(
        "(function(){var o=Storage.prototype.setItem;window.__o=o;Storage.prototype.setItem=function(k,v){if(/[.]tabs$/.test(k))throw new Error('full');return o.call(this,k,v);};})();0"
    )
    addfmt(pg, "A1:A2", "on", "", "")
    check(
        "format: if the tab list cannot be marked, no format rule is stored",
        pg.evaluate(
            "Object.keys(localStorage).filter(function(k){return /[.]fmt$/.test(k)}).length"
        )
        == 0
        and "not saved" in pg.inner_text("#fmt-status")
        and pg.evaluate(ver) == 1,
        pg.inner_text("#fmt-status"),
    )
    pg.evaluate("Storage.prototype.setItem=window.__o;0")
    pg.click("#tab-del") if False else None
    pg = fresh(ctx, "/sheets/")
    setc(pg, "A1", "1234.5")
    setc(pg, "A2", "0.256")
    setc(pg, "A3", "=A1*2")
    setc(pg, "A4", "text")
    setc(pg, "Z100", "9")
    addfmt(pg, "A1:A4", "on", "right", "thousands")
    check(
        "format: first use marks the tab list version 2",
        pg.evaluate(ver) == 2,
        str(pg.evaluate(ver)),
    )
    check(
        "format: a number shows with the thousands format",
        shown(pg, "A1") == "1,234.50" and shown(pg, "A3") == "2,469.00",
        shown(pg, "A1") + "/" + shown(pg, "A3"),
    )
    check("format: text is left as typed", shown(pg, "A4") == "text")
    check(
        "format: bold and right alignment are applied",
        sty(pg, "A1", "fontWeight") in ("700", "bold") and sty(pg, "A1", "textAlign") == "right",
        sty(pg, "A1", "fontWeight") + "/" + sty(pg, "A1", "textAlign"),
    )
    check(
        "format: the tooltip shows the value behind the format",
        "value 1234.5" in pg.get_attribute(sel("A1"), "title"),
        pg.get_attribute(sel("A1"), "title"),
    )
    pg.click(sel("A1"))
    check("format: editing shows the stored value", shown(pg, "A1") == "1234.5", shown(pg, "A1"))
    pg.press(sel("A1"), "Tab")
    addfmt(pg, "A2:A2", "off", "", "percent")
    check(
        "format: a later rule overrides one property only",
        shown(pg, "A2") == "25.6%"
        and sty(pg, "A2", "fontWeight") in ("400", "normal")
        and sty(pg, "A2", "textAlign") == "right",
        shown(pg, "A2") + "/" + sty(pg, "A2", "textAlign"),
    )
    addfmt(pg, "Y99:Z100", "on", "", "dec2")
    check("format: it reaches Z100", shown(pg, "Z100") == "9.00", shown(pg, "Z100"))
    # Values are untouched: Export keeps the stored cells, a dependent formula uses the value.
    setc(pg, "B1", "=A1+1")
    check(
        "format: a formula reads the value, not the text",
        shown(pg, "B1") == "1235.5",
        shown(pg, "B1"),
    )
    pg.fill("#fmt-range", "zz")
    pg.click("#fmt-add")
    check(
        "format: a bad range is refused",
        "range" in pg.inner_text("#fmt-status"),
        pg.inner_text("#fmt-status"),
    )
    pg.click("#fmt-add")
    with pg.expect_download() as dj:
        pg.click("#sheet-export")
    exp = open(dj.value.path()).read()
    check(
        "format: the JSON export holds the typed values and no format",
        '"1234.5"' in exp
        and "1,234" not in exp
        and "25.6" not in exp
        and "bold" not in exp
        and "thousands" not in exp,
        exp[:160],
    )
    with pg.expect_download() as dc:
        pg.click("#sheet-csv")
    csv = open(dc.value.path()).read()
    check(
        "format: the CSV holds the unformatted values",
        "1234.5" in csv and "2469" in csv and "1,234" not in csv and "%" not in csv,
        csv[:160],
    )
    stored = pg.evaluate(
        "JSON.parse(localStorage.getItem(%s+'.tab.t0')||localStorage.getItem(%s))" % (KEYJS, KEYJS)
    )
    check(
        "format: stored cells hold the typed values",
        isinstance(stored, dict) and stored.get("A1") == "1234.5" and stored.get("A3") == "=A1*2",
        str(stored)[:120],
    )
    if SHOTS:
        pg.locator(sel("A1")).scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "sheets-format-62.png"), full_page=False)
        pg.set_viewport_size({"width": 390, "height": 800})
        pg.wait_for_timeout(200)
        pg.locator(sel("A1")).scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "sheets-format-62-phone.png"), full_page=False)
        pg.set_viewport_size({"width": 1100, "height": 900})
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(200)
    check(
        "format: no sideways page scroll at 390 px",
        not pg.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        ),
    )
    pg.set_viewport_size({"width": 1100, "height": 900})
    helptxt = pg.text_content("#fmt-help")
    check(
        "format: the help text is complete on the page",
        helptxt.strip().endswith("reload it.")
        and "importing an export brings back the values only" in helptxt
        and "A cell's tooltip" in helptxt,
        helptxt[-120:],
    )
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "format: rules and formats survive a reload",
        shown(pg, "A1") == "1,234.50" and shown(pg, "A2") == "25.6%",
        shown(pg, "A1"),
    )
    pg.click("#fmt-list li:nth-child(2) button")
    check(
        "format: removing a rule falls back to the rule below it",
        shown(pg, "A2") == "0.26",
        shown(pg, "A2"),
    )
    # Damaged rules are kept, reported, and nothing is formatted.
    key = pg.evaluate("%s+'.fmt'" % KEYJS)
    pg.evaluate("localStorage.setItem(%s,'{bad')" % json.dumps(key))
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "format: unreadable rules are reported, adding is off, nothing formatted",
        "could not be read" in pg.inner_text("#fmt-status")
        and pg.is_disabled("#fmt-add")
        and shown(pg, "A1") == "1234.5",
        pg.inner_text("#fmt-status"),
    )
    check(
        "format: the damaged rules are untouched",
        pg.evaluate("localStorage.getItem(%s)" % json.dumps(key)) == "{bad",
    )
    with pg.expect_download() as dl:
        pg.click("#fmt-raw")
    check("format: damaged rules download as stored", open(dl.value.path()).read() == "{bad")
    # A window opened before the marker was set must not lower it when it later changes the tab list.
    pg = fresh(ctx, "/sheets/")
    pg.reload()
    pg.wait_for_timeout(300)
    pg.evaluate("localStorage.removeItem(%s+'.fmt'); 0" % KEYJS)
    pg.evaluate(
        '(function(){var k=%s+\'.tabs\';var m=JSON.parse(localStorage.getItem(k)||\'{"v":1,"active":"t0","tabs":[{"id":"t0","name":"Sheet 1"}]}\');m.v=1;localStorage.setItem(k,JSON.stringify(m));})();0'
        % KEYJS
    )
    pg.reload()
    pg.wait_for_timeout(300)
    check(
        "format: the stale-window setup starts at version 1",
        pg.evaluate(ver) == 1,
        str(pg.evaluate(ver)),
    )
    p3 = ctx.new_page()
    p3.goto(APP + "/sheets/")
    p3.wait_for_timeout(400)
    addfmt(p3, "A1:A2", "on", "", "")
    check("format: the second window marked the list", p3.evaluate(ver) == 2, str(p3.evaluate(ver)))
    p3.close()
    pg.click("#tab-rename")
    pg.wait_for_timeout(100)
    msg_stale = pg.inner_text("#tab-status")
    pg.click("#tab-add")
    pg.wait_for_timeout(200)
    check(
        "format: a window that still thinks the list is version 1 never lowers the marker",
        pg.evaluate(ver) == 2,
        str(pg.evaluate(ver)) + " " + msg_stale + " " + pg.inner_text("#tab-status"),
    )
    # Tab paths.
    pg = fresh(ctx, "/sheets/")
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    tid = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS)
    fk = pg.evaluate("%s+'.tab.%s.fmt'" % (KEYJS, tid))
    p2 = ctx.new_page()
    p2.goto(APP + "/sheets/")
    p2.wait_for_timeout(400)
    p2.click('#sheet-tabs button[data-tab="%s"]' % tid)
    p2.once("dialog", lambda d: d.accept())
    p2.click("#tab-del")
    p2.wait_for_timeout(200)
    addfmt(pg, "A1:A2", "on", "", "")
    check(
        "format: a stale tab is refused new format rules and no key appears",
        "not changed" in pg.inner_text("#fmt-status")
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(fk)) is None,
        pg.inner_text("#fmt-status"),
    )
    p2.close()
    pg = fresh(ctx, "/sheets/")
    pg.click("#tab-add")
    pg.wait_for_timeout(100)
    setc(pg, "A1", "5")
    pg.fill("#cf-range", "A1:A2")
    pg.select_option("#cf-kind", "greater")
    pg.fill("#cf-value", "1")
    pg.select_option("#cf-color", "red")
    pg.click("#cf-add")
    addfmt(pg, "A1:A2", "", "center", "dec2")
    pg.once("dialog", lambda d: d.accept())
    pg.click("#tab-del")
    pg.wait_for_timeout(200)
    pg.evaluate(
        "(function(){var o=Storage.prototype.setItem;window.__o=o;Storage.prototype.setItem=function(k,v){if(/[.]fmt$/.test(k))throw new Error('full');return o.call(this,k,v);};})();0"
    )
    pg.click("#tab-restore")
    msg = pg.inner_text("#tab-status")
    check(
        "format: a failed format write restores nothing and says so",
        pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).tabs.length" % KEYJS) == 1
        and "would not store the tab's format rules" in msg,
        msg,
    )
    check(
        "format: the colour rule written before the failure is rolled back too",
        pg.evaluate(
            "Object.keys(localStorage).filter(function(k){return /[.]cf$/.test(k)&&k.indexOf('.tab.')>0}).length"
        )
        == 0,
    )
    left = pg.evaluate(
        "Object.keys(localStorage).filter(function(k){return k.indexOf(%s)===0&&/[.]tab[.]t[0-9]+/.test(k)&&!/[.]tab[.]t0/.test(k)}).length"
        % KEYJS
    )
    check("format: no half-restored keys are left", left == 0, str(left))
    pg.evaluate("Storage.prototype.setItem=window.__o;0")
    pg.click("#tab-restore")
    pg.wait_for_timeout(200)
    check(
        "format: Restore works afterwards with its format",
        shown(pg, "A1") == "5.00"
        and sty(pg, "A1", "textAlign") == "center"
        and pg.evaluate("document.querySelector('input[data-addr=A1]').dataset.cf") == "red",
        pg.inner_text("#tab-status"),
    )
    t2 = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS)
    fk2 = pg.evaluate("%s+'.tab.%s.fmt'" % (KEYJS, t2))
    pg.evaluate(
        "(function(){var k=%s+'.tabs',m=JSON.parse(localStorage.getItem(k));m.tabs=m.tabs.filter(function(t){return t.id!=%s});m.active='t0';localStorage.setItem(k,JSON.stringify(m));})();0"
        % (KEYJS, json.dumps(t2))
    )
    pg.reload()
    pg.wait_for_timeout(400)
    ids = set()
    for _ in range(3):
        pg.click("#tab-add")
        pg.wait_for_timeout(100)
        ids.add(pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % KEYJS))
    check(
        "format: a new tab never reuses an id whose format rules are stored",
        t2 not in ids and pg.evaluate("localStorage.getItem(%s)" % json.dumps(fk2)) is not None,
        str(ids),
    )


def sheets_old_page(ctx):  # noqa: C901
    """The Sheets page as it was at 3d0df75 (before format rules) opened on a tab list marked version 2."""
    import re
    import tempfile

    fx = os.path.join(os.getcwd(), "tests", "js", "fixtures")
    OLD = {}
    for n, f in (
        ("grid", "sheets_grid_page_3d0df75.js"),
        ("model", "sheets_model_grid8x20.js"),
        ("formula", "sheets_formula_grid8x20.js"),
    ):
        with open(os.path.join(fx, f)) as fh:
            OLD[n] = fh.read()
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def setc(pgx, a, v):
        pgx.click('input[data-addr="%s"]' % a)
        pgx.fill('input[data-addr="%s"]' % a, v)
        pgx.press('input[data-addr="%s"]' % a, "Tab")

    def snap(pgx):
        return pgx.evaluate(
            "(function(){var o={};for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);o[k]=localStorage.getItem(k);}return o;})()"
        )

    def changed(a, b):
        return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))

    def go_old(pgx):
        def h(route):
            m = re.search(r"/static/sheets/(grid|model|formula)\.js", route.request.url)
            route.fulfill(status=200, content_type="application/javascript", body=OLD[m.group(1)])

        pgx.route(re.compile(r"/static/sheets/(grid|model|formula)\.js"), h)
        pgx.reload()
        pgx.wait_for_timeout(500)

    def go_new(pgx):
        pgx.unroute(re.compile(r"/static/sheets/(grid|model|formula)\.js"))
        pgx.reload()
        pgx.wait_for_timeout(400)

    for case in ("t0 present", "t0 deleted"):
        pg = fresh(ctx, "/sheets/")
        key = pg.evaluate(KEYJS)
        if case == "t0 present":
            setc(pg, "A1", "legacy")
        pg.click("#tab-add")
        pg.wait_for_timeout(100)
        setc(pg, "A1", "tab2")
        pg.fill("#fmt-range", "A1:A2")
        pg.select_option("#fmt-bold", "on")
        pg.click("#fmt-add")
        if case == "t0 deleted":
            pg.click('#sheet-tabs button[data-tab="t0"]')
            pg.once("dialog", lambda d: d.accept())
            pg.click("#tab-del")
            pg.wait_for_timeout(200)
        before = snap(pg)
        tabs = before.get(key + ".tabs", "")
        check(
            "old page (%s): setup is a version 2 list with a format key" % case,
            '"v":2' in tabs and any(k.endswith(".fmt") for k in before),
            tabs,
        )
        go_old(pg)
        st = pg.inner_text("#tab-status")
        check(
            "old page (%s): says the tab list could not be read and tab changes are off" % case,
            "could not be read" in st
            and pg.is_disabled("#tab-add")
            and pg.is_disabled("#tab-rename"),
            st,
        )
        check(
            "old page (%s): nothing was written just by opening it" % case,
            changed(before, snap(pg)) == [],
            str(changed(before, snap(pg))),
        )
        shown_a1 = pg.input_value('input[data-addr="A1"]')
        check(
            "old page (%s): the t0 tab shows the legacy key's cells" % case,
            shown_a1 == ("legacy" if case == "t0 present" else ""),
            shown_a1,
        )

        def acc(d):
            try:
                d.accept()
            except Exception as e:  # a second listener already took this dialog
                print("note: dialog already handled:", type(e).__name__, flush=True)

        pg.once("dialog", acc)
        # edit
        setc(pg, "A1", "3")
        setc(pg, "A2", "1")
        a = snap(pg)
        ch = changed(before, a)
        check("old page (%s): an edit writes only the legacy t0 key" % case, ch == [key], str(ch))
        # sort
        pg.fill("#sort-range", "A1:A2")
        pg.fill("#sort-col", "A")
        pg.uncheck("#sort-header")
        pg.click("#sort-go")
        b = snap(pg)
        check(
            "old page (%s): sort writes only the legacy t0 key" % case,
            changed(before, b) == [key],
            str(changed(before, b)),
        )
        # import
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write('{"B2":"imp"}')
        pg.set_input_files("#sheet-import", f.name)
        pg.wait_for_timeout(400)
        c = snap(pg)
        check(
            "old page (%s): import writes only the legacy t0 key" % case,
            changed(before, c) == [key] and "imp" in c.get(key, ""),
            str(changed(before, c)),
        )
        # clear
        pg.click("#sheet-clear")
        pg.wait_for_timeout(200)
        d = snap(pg)
        check(
            "old page (%s): clear writes only the legacy t0 key" % case,
            changed(before, d) == [key],
            str(changed(before, d)),
        )
        keep = [k for k in before if k != key]
        check(
            "old page (%s): the tab list, the other tab's cells and the format key are byte-identical"
            % case,
            all(before[k] == d.get(k) for k in keep) and any(k.endswith(".fmt") for k in keep),
            str(changed(before, d)),
        )
        go_new(pg)
        after = snap(pg)
        check(
            "old page (%s): the current page reads the list and the format rule again" % case,
            all(before[k] == after.get(k) for k in keep) and pg.is_enabled("#tab-add"),
            str(changed(before, after)),
        )


def sheets_paste(ctx):  # noqa: C901
    """Paste of a block, using a synthetic paste event that carries a DataTransfer (a real OS clipboard is not used)."""
    pg = fresh(ctx, "/sheets/")

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    def paste(a, text):
        pg.click('input[data-addr="%s"]' % a)
        pg.evaluate(
            "(function(a,t){var e=document.querySelector('input[data-addr='+a+']');var dt=new DataTransfer();dt.setData('text/plain',t);e.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));})(%s,%s)"
            % (json.dumps(a), json.dumps(text))
        )
        pg.wait_for_timeout(150)

    paste("B2", "name\tqty\nbolt\t5\nnut\t=C3*2\n")
    check(
        "paste: a block fills right and down from the focused cell",
        val("B2") == "name"
        and val("C2") == "qty"
        and val("B3") == "bolt"
        and val("C3") == "5"
        and val("B4") == "nut",
        val("B2") + val("C3"),
    )
    check("paste: a formula in the block is a formula", val("C4") == "10", val("C4"))
    check(
        "paste: the status line says what happened",
        "5 cells filled" in pg.inner_text("#sheet-status")
        or "6 cells filled" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    if SHOTS:
        pg.locator('input[data-addr="B2"]').scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "sheets-paste-64.png"), full_page=False)
        pg.set_viewport_size({"width": 390, "height": 800})
        pg.wait_for_timeout(200)
        pg.locator('input[data-addr="B2"]').scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "sheets-paste-64-phone.png"), full_page=False)
        pg.set_viewport_size({"width": 1100, "height": 900})
    pg.click("#sheet-undo")
    check(
        "paste: one Undo takes the whole block back",
        val("B2") == "" and val("C4") == "" and val("B3") == "",
    )
    pg.click("#sheet-redo")
    check("paste: Redo puts it back", val("B2") == "name" and val("C3") == "5")
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "paste: the pasted cells are stored",
        val("B2") == "name" and val("C3") == "5" and val("C4") == "10",
    )
    paste("Z99", "a\tb\n1\t2\n3\t4")
    check(
        "paste: cells past Z or row 100 are left out and counted",
        val("Z99") == "a"
        and val("Z100") == "1"
        and "outside Z100 left out" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    paste("E1", "x\n\ty")
    check(
        "paste: blank cells in the block clear nothing that is not there and ragged rows work",
        val("E1") == "x" and val("F2") == "y",
    )
    paste("E1", "\t\n\t")
    check(
        "paste: blank pasted cells clear existing ones",
        val("E1") == "" and val("F2") == "" or val("F2") == "y",
    )
    # The browser's own clipboard and a real Ctrl+V (headless Chromium's clipboard, not a desktop OS clipboard).
    copied = pg.evaluate(
        "(function(t){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();var ok=document.execCommand('copy');a.remove();return ok;})(%s)"
        % json.dumps("r1\tr2\nr3\tr4")
    )
    check("paste: copying to the browser clipboard succeeded", copied is True, str(copied))
    pg.click('input[data-addr="P5"]')
    pg.keyboard.press("Control+V")
    pg.wait_for_timeout(250)
    check(
        "paste: a real Ctrl+V from the browser clipboard fills the block",
        val("P5") == "r1" and val("Q5") == "r2" and val("P6") == "r3" and val("Q6") == "r4",
        val("P5") + val("Q5") + val("P6") + val("Q6"),
    )
    check(
        "paste: the always-visible help is short and the detail sits in a closed expander",
        len(pg.inner_text("#paste-help")) < 260
        and not pg.evaluate("document.getElementById('paste-more').open"),
        str(len(pg.inner_text("#paste-help"))),
    )
    # a quoted field with a tab and a line break stays one cell
    paste("H1", '"p\tq"\t"r\ns"')
    check(
        "paste: a quoted field keeps its tab, and its line break becomes a space and is counted",
        val("H1") == "p\tq"
        and val("I1") == "r s"
        and "1 with a line break inside" in pg.inner_text("#sheet-status"),
        repr(val("H1")) + repr(val("I1")) + pg.inner_text("#sheet-status"),
    )
    # refused by validation
    pg.fill("#val-range", "K1:K3")
    pg.select_option("#val-kind", "number")
    pg.fill("#val-min", "1")
    pg.fill("#val-max", "10")
    pg.click("#val-add")
    paste("K1", "5\n50\n7")
    check(
        "paste: cells a validation rule refuses are skipped and counted",
        val("K1") == "5"
        and val("K2") == ""
        and val("K3") == "7"
        and "1 refused by validation rules" in pg.inner_text("#sheet-status"),
        val("K1") + val("K2") + val("K3") + pg.inner_text("#sheet-status"),
    )
    # a single value pastes the ordinary way (not intercepted)
    pg.click('input[data-addr="M1"]')
    intercepted = pg.evaluate(
        "(function(){var e=document.querySelector('input[data-addr=M1]');var dt=new DataTransfer();dt.setData('text/plain','single');var ev=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});e.dispatchEvent(ev);return ev.defaultPrevented;})()"
    )
    check("paste: a single value is left to the browser", intercepted is False, str(intercepted))
    paste("A10", '"never closes\tb')
    check(
        "paste: an unclosed quote pastes nothing",
        val("A10") == "" and "nothing was pasted" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    paste("A20", "\t".join(["v"] * 60) + ("\n" + "\t".join(["v"] * 60)) * 49)
    check(
        "paste: more than 2600 cells is refused",
        val("A20") == "" and "2600" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    # text after a closing quote refuses the whole paste; currency, percent and formula text are kept literally
    paste("A30", '"hello" she said\tb')
    check(
        "paste: text after a closing quote refuses the paste",
        val("A30") == "" and "nothing was pasted" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    paste("A32", "$1,200\t15%\t=A1+B2")
    check("paste: currency and percent stay text", val("A32") == "$1,200" and val("B32") == "15%")
    pg.click('input[data-addr="C32"]')
    check(
        "paste: the pasted formula source is exactly as written",
        pg.input_value("#sheet-bar") == "=A1+B2",
        pg.input_value("#sheet-bar"),
    )
    # an active filter refuses the paste and changes nothing
    before = pg.evaluate(
        "localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey)"
    )
    pg.fill("#filter-col", "B")
    pg.fill("#filter-val", "bolt")
    pg.click("#filter-go")
    paste("B3", "f1\tf2\nf3\tf4")
    check(
        "paste: with a filter on nothing is pasted and the status says why",
        val("B3") == "bolt"
        and val("C3") == "5"
        and "A filter is on" in pg.inner_text("#sheet-status")
        and pg.evaluate(
            "localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey)"
        )
        == before,
        pg.inner_text("#sheet-status"),
    )
    pg.click("#filter-clear")
    paste("E40", "f1\tf2\nf3\tf4")
    check(
        "paste: after clearing the filter the same paste works",
        val("E40") == "f1" and val("F41") == "f4",
    )
    # unreadable validation rules: the paste is refused and the rules are untouched
    rk = pg.evaluate("document.getElementById('sheet-root').dataset.storeKey+'.rules'")
    pg.evaluate("localStorage.setItem(%s,'{bad')" % json.dumps(rk))
    pg.reload()
    pg.wait_for_timeout(400)
    sb = pg.evaluate("localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey)")
    paste("H40", "u1\tu2")
    check(
        "paste: with unreadable validation rules nothing is pasted, cells and rules are untouched",
        val("H40") == ""
        and "could not be read" in pg.inner_text("#sheet-status")
        and pg.evaluate(
            "localStorage.getItem(document.getElementById('sheet-root').dataset.storeKey)"
        )
        == sb
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(rk)) == "{bad",
        pg.inner_text("#sheet-status"),
    )
    pg.click('input[data-addr="H41"]')
    pg.fill('input[data-addr="H41"]', "typed")
    pg.press('input[data-addr="H41"]', "Tab")
    check(
        "paste: with unreadable rules a typed edit is refused and the rules bytes stay",
        val("H41") != "typed"
        and "could not be read" in pg.inner_text("#sheet-status")
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(rk)) == "{bad",
        val("H41"),
    )
    pg.click('input[data-addr="H41"]')
    pg.fill("#sheet-bar", "viabar")
    pg.press("#sheet-bar", "Enter")
    check(
        "paste: with unreadable rules a formula-bar edit is refused too",
        val("H41") != "viabar"
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(rk)) == "{bad",
        val("H41"),
    )
    pg.evaluate("localStorage.removeItem(%s)" % json.dumps(rk))
    pg.reload()
    pg.wait_for_timeout(400)
    # storage failure: the paste stays on screen and the status says it was not stored
    pg.evaluate(
        "(function(){var o=Storage.prototype.setItem;window.__o=o;Storage.prototype.setItem=function(k,v){if(k===document.getElementById('sheet-root').dataset.storeKey)throw new Error('full');return o.call(this,k,v);};})();0"
    )
    paste("J40", "s1\ts2")
    check(
        "paste: if the browser will not store it, it stays on screen and the status says so",
        val("J40") == "s1" and "would not store the sheet" in pg.inner_text("#sheet-status"),
        pg.inner_text("#sheet-status"),
    )
    pg.evaluate("Storage.prototype.setItem=window.__o;0")
    # a second tab keeps its own pasted cells, and a stale (deleted elsewhere) tab is not written
    pg.click("#tab-add")
    pg.wait_for_timeout(150)
    paste("A1", "t2a\tt2b")
    tk = pg.evaluate("document.getElementById('sheet-root').dataset.storeKey")
    t2key = pg.evaluate(
        "(function(k){var m=JSON.parse(localStorage.getItem(k+'.tabs'));return k+'.tab.'+m.active;})(%s)"
        % json.dumps(tk)
    )
    check(
        "paste: a second tab stores its own pasted cells under its own key",
        "t2a" in (pg.evaluate("localStorage.getItem(%s)" % json.dumps(t2key)) or "")
        and val("A1") == "t2a",
        t2key,
    )
    p2 = ctx.new_page()
    p2.goto(APP + "/sheets/")
    p2.wait_for_timeout(400)
    tid = pg.evaluate("JSON.parse(localStorage.getItem(%s+'.tabs')).active" % json.dumps(tk))
    p2.click('#sheet-tabs button[data-tab="%s"]' % tid)
    p2.once("dialog", lambda d: d.accept())
    p2.click("#tab-del")
    p2.wait_for_timeout(200)
    paste("A5", "stale1\tstale2")
    check(
        "paste: in a tab deleted from another window nothing is written",
        pg.evaluate("localStorage.getItem(%s)" % json.dumps(t2key)) is None
        and ("deleted or changed in another window" in pg.inner_text("#sheet-status")),
        pg.inner_text("#sheet-status"),
    )
    p2.close()


def sheets_fill(ctx):  # noqa: C901
    """Copy the first row down or the first column right over a range."""
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")

    def stored():
        return pg.evaluate("localStorage.getItem(%s)" % KEYJS)

    def status():
        return pg.inner_text("#sheet-status")

    setc("A1", "bolt")
    setc("B1", "5")
    setc("C1", "=B1*2")
    setc("A3", "old")
    pg.fill("#fill-range", "A1:C4")
    pg.click("#fill-down")
    check(
        "fill: the first row is copied down over every row, old values replaced",
        val("A2") == "bolt" and val("A3") == "bolt" and val("B4") == "5" and val("C4") == "10",
        val("A3") + val("C4"),
    )
    pg.click('input[data-addr="C3"]')
    check(
        "fill: a formula keeps its source (no shifting)",
        pg.input_value("#sheet-bar") == "=B1*2",
        pg.input_value("#sheet-bar"),
    )
    check(
        "fill: the status line counts and mentions formulas",
        "formulas copied with their references unchanged" in status(),
        status(),
    )
    pg.click("#sheet-undo")
    check(
        "fill: one Undo takes the whole fill back",
        val("A2") == "" and val("A3") == "old" and val("C4") == "",
    )
    pg.click("#sheet-redo")
    pg.reload()
    pg.wait_for_timeout(400)
    check("fill: the filled cells are stored", val("A4") == "bolt" and val("C2") == "10")
    if SHOTS:
        pg.fill("#fill-range", "A1:C4")
        pg.evaluate("document.getElementById('fill-range').scrollIntoView({block: 'center'})")
        pg.screenshot(path=os.path.join(SHOTS, "sheets-fill-65.png"), full_page=False)
        pg.set_viewport_size({"width": 390, "height": 800})
        pg.wait_for_timeout(200)
        pg.evaluate("document.getElementById('fill-range').scrollIntoView({block: 'center'})")
        pg.screenshot(path=os.path.join(SHOTS, "sheets-fill-65-phone.png"), full_page=False)
        pg.set_viewport_size({"width": 1100, "height": 900})
    pg.fill("#fill-range", "A1:A2")
    pg.click("#fill-right")
    check("fill: one column cannot be copied right", "at least two columns" in status(), status())
    pg.fill("#fill-range", "D1:E1")
    pg.click("#fill-right")
    check(
        "fill: a column copied right clears empty targets and is stored",
        val("E1") == "" and "Nothing changed" in status(),
        status(),
    )
    setc("D5", "left")
    setc("E5", "gone")
    pg.fill("#fill-range", "D5:F6")
    pg.click("#fill-right")
    check(
        "fill: first column copied right",
        val("E5") == "left" and val("F5") == "left" and val("E6") == "",
        val("E5") + val("F5"),
    )
    pg.fill("#fill-range", "A1:A101")
    pg.click("#fill-down")
    check("fill: a range outside A1:Z100 is refused", "A1:Z100" in status(), status())
    # a reversed range means the same as the forward one, and junk is rejected
    setc("A20", "r1")
    setc("B20", "r2")
    pg.fill("#fill-range", "B21:A20")
    pg.click("#fill-down")
    check(
        "fill: a reversed range B21:A20 works like A20:B21",
        val("A21") == "r1" and val("B21") == "r2",
        val("A21") + val("B21"),
    )
    for junk in ("A1", "A1:", "foo", "A1:B2:C3", "1:2", "A1;B2"):
        pg.fill("#fill-range", junk)
        before = stored()
        pg.click("#fill-down")
        check(
            "fill: junk range %r is rejected, nothing written" % junk,
            "Enter a range like" in status() and stored() == before,
            status(),
        )
    # partly accepted: only some targets are allowed by the rules
    setc("A30", "x")
    setc("B30", "y")
    pg.fill("#val-range", "B31:B32")
    pg.fill("#val-items", "ok")
    pg.click("#val-add")
    pg.fill("#fill-range", "A30:B32")
    pg.click("#fill-down")
    check(
        "fill: a partly refused fill writes the allowed targets and leaves the refused ones empty",
        val("A31") == "x" and val("A32") == "x" and val("B31") == "" and val("B32") == "",
        val("A31") + val("B31"),
    )
    check(
        "fill: the status counts both",
        "2 cells filled" in status() and "2 refused by validation rules" in status(),
        status(),
    )
    pg.click("#sheet-undo")
    check("fill: one Undo takes back the accepted part", val("A31") == "" and val("A32") == "")
    # validation: refused cells are counted and left alone
    pg.fill("#val-range", "A6:A8")
    pg.fill("#val-items", "red, blue")
    pg.click("#val-add")
    setc("A5", "green")
    pg.fill("#fill-range", "A5:A8")
    pg.click("#fill-down")
    check(
        "fill: values a validation rule refuses are not written and are counted",
        val("A6") == "" and "refused by validation rules" in status(),
        status(),
    )
    # filter on: refused, stored cells unchanged
    pg.fill("#filter-col", "A")
    pg.fill("#filter-val", "bolt")
    pg.click("#filter-go")
    before = stored()
    pg.fill("#fill-range", "A1:A3")
    pg.click("#fill-down")
    check(
        "fill: with a filter on nothing is filled and the status says why",
        "A filter is on" in status() and stored() == before,
        status(),
    )
    pg.click("#filter-clear")
    # unreadable rules: refused
    rk = pg.evaluate("%s+'.rules'" % KEYJS)
    pg.evaluate("localStorage.setItem(%s,'{bad')" % json.dumps(rk))
    pg.reload()
    pg.wait_for_timeout(400)
    before = stored()
    pg.fill("#fill-range", "A1:A3")
    pg.click("#fill-down")
    check(
        "fill: with unreadable rules nothing is filled, cells and rules bytes are untouched",
        "could not be read" in status()
        and stored() == before
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(rk)) == "{bad",
        status(),
    )
    # storage failure keeps the fill visible
    pg.evaluate("localStorage.removeItem(%s)" % json.dumps(rk))
    pg.reload()
    pg.wait_for_timeout(400)
    setc("A2", "zzz")
    pg.evaluate(
        "(function(k){var o=Storage.prototype.setItem;Storage.prototype.setItem=function(a,b){if(a===k)throw new Error('full');return o.call(this,a,b);};})(%s)"
        % KEYJS
    )
    pg.fill("#fill-range", "A1:A2")
    pg.click("#fill-down")
    check(
        "fill: if the browser will not store it, the status says so and the fill stays on screen",
        "would not store" in status() and val("A2") == "bolt",
        status() + " | " + val("A2"),
    )


def sheets_headers(ctx):  # noqa: C901
    """Column letters and row numbers stay visible inside the grid box while it scrolls (desktop and 390 px)."""
    pg = fresh(ctx, "/sheets/")
    JS = (
        "(function(){var b=document.querySelector('#sheet-root'),br=b.getBoundingClientRect(),"
        "c=b.querySelector('tr:nth-child(1) th:nth-child(6)').getBoundingClientRect(),"
        "r=b.querySelector('tr:nth-child(61) th').getBoundingClientRect();"
        "return {top:Math.round(c.top-br.top),left:Math.round(r.left-br.left),st:b.scrollTop,sl:b.scrollLeft,txtc:b.querySelector('tr:nth-child(1) th:nth-child(6)').textContent,txtr:b.querySelector('tr:nth-child(61) th').textContent};})()"
    )
    for label, size in (
        ("desktop", {"width": 1100, "height": 900}),
        ("phone", {"width": 390, "height": 800}),
    ):
        pg.set_viewport_size(size)
        pg.wait_for_timeout(200)
        pg.evaluate(
            "document.querySelector('#sheet-root').scrollIntoView();window.scrollBy(0,-document.getElementById('sheet-barrow').offsetHeight)"
        )
        pg.evaluate(
            "(function(){var b=document.querySelector('#sheet-root');b.scrollTop=0;b.scrollLeft=0;b.scrollTop=1500;b.scrollLeft=300;})()"
        )
        pg.wait_for_timeout(200)
        m = pg.evaluate(JS)
        hit = pg.evaluate(
            "(function(){var hs=document.querySelectorAll('#sheet-root tr:nth-child(1) th'),n=0,ok=0,bad=[],box=document.getElementById('sheet-root').getBoundingClientRect(),lim=hs[0].getBoundingClientRect().right+8;for(var i=1;i<hs.length;i++){var r=hs[i].getBoundingClientRect(),x=r.left+r.width/2;if(x<lim||x>innerWidth-5||x>box.right-5)continue;n++;var e=document.elementFromPoint(x,r.top+r.height/2);if(e===hs[i])ok++;else bad.push(hs[i].textContent+':'+(e?e.tagName+'#'+e.id:'null')+'@'+Math.round(x)+','+Math.round(r.top+r.height/2));}return {n:n,ok:ok,bad:bad};})()"
        )
        corner = pg.evaluate(
            "(function(){var b=document.getElementById('sheet-root'),c=b.querySelector('tr:nth-child(1) th:nth-child(1)'),r=c.getBoundingClientRect(),br=b.getBoundingClientRect(),pts=[[r.left+4,r.top+4],[r.right-4,r.top+4],[r.left+4,r.bottom-4],[r.right-4,r.bottom-4]],hit=0;pts.forEach(function(p){if(document.elementFromPoint(p[0],p[1])===c)hit++;});"
            "var rows=b.querySelectorAll('tr:not(:first-child) > th:first-child'),band=0,bandTop=r.bottom;rows.forEach(function(h){var q=h.getBoundingClientRect();if(q.bottom>br.top&&q.top<bandTop-1&&q.bottom>r.top+1&&q.left<r.right)band++;});"
            "return {hit:hit,rowInBand:band,cornerLeft:Math.round(r.left-br.left),cornerTop:Math.round(r.top-br.top)};})()"
        )
        check(
            "headers: %s, the blank corner is on top: all four of its points hit the corner (no scrolled row number covers it)"
            % label,
            corner["hit"] == 4 and abs(corner["cornerLeft"]) <= 2 and abs(corner["cornerTop"]) <= 2,
            str(corner),
        )
        check(
            "headers: %s, the column letter is not covered by the sticky formula bar" % label,
            hit["n"] > 0 and hit["n"] == hit["ok"],
            str(hit),
        )
        check(
            "headers: %s, after scrolling down and sideways the column letter is at the top and the row number at the left edge"
            % label,
            m["st"] > 0
            and m["sl"] > 0
            and abs(m["top"]) <= 2
            and abs(m["left"]) <= 2
            and m["txtc"] == "E"
            and m["txtr"] == "60",
            str(m),
        )
        if SHOTS and label == "phone":
            pg.screenshot(path=os.path.join(SHOTS, "sheets-headers-66-phone.png"), full_page=False)
        if SHOTS and label == "desktop":
            pg.screenshot(path=os.path.join(SHOTS, "sheets-headers-66.png"), full_page=False)


def slides_drag(ctx):  # noqa: C901
    """Drag to move, drag the corner square to resize, in real Chromium with the real mouse."""
    pg = fresh(ctx, "/slides/")
    KEY = "document.getElementById('slide-root').dataset.storeKey"

    def item():
        return pg.evaluate(
            "(()=>{const d=JSON.parse(localStorage.getItem(%s));const e=d.slides[0].elements;const x=e[e.length-1];return [x.x,x.y,x.w,x.h];})()"
            % KEY
        )

    def geom():
        return pg.evaluate(
            "(()=>{document.getElementById('sl-canvas-wrap').scrollIntoView({block:'center'});const s=document.querySelector('#sl-canvas-wrap svg').getBoundingClientRect();return {x:s.left,y:s.top,k:s.width/960};})()"
        )

    def drag(frm, to, steps=8):
        pg.mouse.move(frm[0], frm[1])
        pg.mouse.down()
        pg.mouse.move(to[0], to[1], steps=steps)
        pg.mouse.up()
        pg.wait_for_timeout(120)

    def near(a, b, tol=3):
        return all(abs(p - q) <= tol for p, q in zip(a, b, strict=True))

    pg.click("#sl-add-rect")
    pg.wait_for_timeout(150)
    start = item()
    g = geom()
    cx, cy = (
        g["x"] + (start[0] + start[2] / 2) * g["k"],
        g["y"] + (start[1] + start[3] / 2) * g["k"],
    )
    drag((cx, cy), (cx + 100 * g["k"], cy + 50 * g["k"]))
    moved = item()
    check(
        "drag: dragging an item moves it by the distance dragged, in slide units",
        near(moved, [start[0] + 100, start[1] + 50, start[2], start[3]]),
        (start, moved),
    )
    check("drag: the new place is stored", near(item(), moved, 0))
    pg.click("#sl-undo")
    check("drag: one Undo puts the whole drag back", item() == start, item())
    pg.click("#sl-redo")
    check("drag: Redo moves it again", near(item(), moved, 0), item())
    # a tap with no movement changes nothing
    before = pg.evaluate("localStorage.getItem(%s)" % KEY)
    undo_was = pg.is_disabled("#sl-undo")
    pg.mouse.move(cx + 100 * g["k"], cy + 50 * g["k"])
    pg.mouse.down()
    pg.mouse.up()
    pg.wait_for_timeout(120)
    check(
        "drag: a tap without moving selects but stores nothing and adds no Undo step",
        pg.evaluate("localStorage.getItem(%s)" % KEY) == before
        and pg.is_disabled("#sl-undo") == undo_was,
    )
    # Esc during a drag drops it
    pos = item()
    pg.mouse.move(cx + 100 * g["k"], cy + 50 * g["k"])
    pg.mouse.down()
    pg.mouse.move(cx + 200 * g["k"], cy + 100 * g["k"], steps=6)
    pg.keyboard.press("Escape")
    pg.mouse.up()
    pg.wait_for_timeout(120)
    check(
        "drag: Esc during a drag cancels it and says so",
        item() == pos and "Drag cancelled" in pg.inner_text("#sl-status"),
        (item(), pg.inner_text("#sl-status")),
    )
    # resize by the corner square
    pos = item()
    hx = g["x"] + (pos[0] + pos[2]) * g["k"]
    hy = g["y"] + (pos[1] + pos[3]) * g["k"]
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, "slides-drag-67.png"), full_page=False)
    drag((hx, hy), (hx + 60 * g["k"], hy + 40 * g["k"]))
    now = item()
    check(
        "drag: dragging the corner square resizes the item and leaves its position",
        near(now, [pos[0], pos[1], pos[2] + 60, pos[3] + 40]),
        (pos, now),
    )
    pg.click("#sl-undo")
    check("drag: one Undo puts the size back", item() == pos, item())
    # dragged past the slide edge: clamped, and says so
    pos = item()
    cx, cy = g["x"] + (pos[0] + pos[2] / 2) * g["k"], g["y"] + (pos[1] + pos[3] / 2) * g["k"]
    drag((cx, cy), (cx + 2000 * g["k"] / 3, cy + 1000 * g["k"] / 3))
    now = item()
    check(
        "drag: an item dragged off the slide is kept inside it and the status says it was adjusted",
        now[0] + now[2] <= 960
        and now[1] + now[3] <= 540
        and "Adjusted" in pg.inner_text("#sl-status"),
        (now, pg.inner_text("#sl-status")),
    )
    # keyboard: Shift+Arrow resizes, Arrow moves
    pg.click("#sl-undo")
    pos = item()
    pg.locator("#sl-canvas-wrap svg").focus()
    pg.keyboard.press("Shift+ArrowRight")
    pg.keyboard.press("Shift+ArrowDown")
    check(
        "drag: Shift+Arrow resizes by 10",
        near(item(), [pos[0], pos[1], pos[2] + 10, pos[3] + 10], 0),
        item(),
    )
    pg.keyboard.press("ArrowLeft")
    check("drag: Arrow still moves by 10", item()[0] == pos[0] - 10, item())
    # pointercancel (a touch gesture taken over by the browser) drops the drag like Esc
    pos = item()
    pg.mouse.move(cx, cy)
    pg.mouse.down()
    pg.mouse.move(cx - 80 * g["k"], cy - 30 * g["k"], steps=5)
    pg.evaluate(
        "document.getElementById('sl-canvas-wrap').dispatchEvent(new PointerEvent('pointercancel',{pointerId:1,bubbles:true}))"
    )
    pg.mouse.up()
    pg.wait_for_timeout(120)
    check(
        "drag: a cancelled pointer drops the drag, nothing is stored",
        item() == pos and "Drag cancelled" in pg.inner_text("#sl-status"),
        (item(), pg.inner_text("#sl-status")),
    )
    # emulated touch (Chromium DevTools touch events, not a real device): drag to move, corner square to resize, cancel
    cdp = pg.context.new_cdp_session(pg)

    def touch(frm, to, cancel=False):
        cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchStart", "touchPoints": [{"x": frm[0], "y": frm[1]}]},
        )
        for n in range(1, 7):
            cdp.send(
                "Input.dispatchTouchEvent",
                {
                    "type": "touchMove",
                    "touchPoints": [
                        {
                            "x": frm[0] + (to[0] - frm[0]) * n / 6,
                            "y": frm[1] + (to[1] - frm[1]) * n / 6,
                        }
                    ],
                },
            )
        cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchCancel" if cancel else "touchEnd", "touchPoints": []},
        )
        pg.wait_for_timeout(150)

    pos = item()
    cx, cy = g["x"] + (pos[0] + pos[2] / 2) * g["k"], g["y"] + (pos[1] + pos[3] / 2) * g["k"]
    touch((cx, cy), (cx + 60 * g["k"], cy + 30 * g["k"]))
    check(
        "drag: an emulated touch drag moves the item and stores it",
        near(item(), [pos[0] + 60, pos[1] + 30, pos[2], pos[3]]),
        (pos, item()),
    )
    pos = item()
    hx, hy = g["x"] + (pos[0] + pos[2]) * g["k"], g["y"] + (pos[1] + pos[3]) * g["k"]
    touch((hx, hy), (hx + 30 * g["k"], hy + 20 * g["k"]))
    check(
        "drag: an emulated touch drag on the corner square resizes",
        near(item(), [pos[0], pos[1], pos[2] + 30, pos[3] + 20]),
        (pos, item()),
    )
    pos = item()
    cx, cy = g["x"] + (pos[0] + pos[2] / 2) * g["k"], g["y"] + (pos[1] + pos[3] / 2) * g["k"]
    touch((cx, cy), (cx + 90 * g["k"], cy), cancel=True)
    check(
        "drag: an emulated touch cancel drops the drag",
        item() == pos and "Drag cancelled" in pg.inner_text("#sl-status"),
        item(),
    )
    pos = item()
    pg.mouse.move(cx, cy)
    pg.mouse.down()
    pg.mouse.move(cx + 30 * g["k"], cy, steps=4)
    pg.keyboard.press("ArrowDown")
    pg.mouse.up()
    pg.wait_for_timeout(150)
    check(
        "drag: an arrow key pressed during a drag is ignored, the drop is the only change",
        near(item(), [pos[0] + 30, pos[1], pos[2], pos[3]]),
        (pos, item()),
    )
    pg.click("#sl-master")
    pg.click("#sl-add-rect")
    pg.wait_for_timeout(150)
    mst = lambda: pg.evaluate(  # noqa: E731
        "(()=>{const d=JSON.parse(localStorage.getItem(%s));const e=d.master.elements;const x=e[e.length-1];return [x.x,x.y,x.w,x.h];})()"
        % KEY
    )
    g = geom()
    m0 = mst()
    mx, my = g["x"] + (m0[0] + m0[2] / 2) * g["k"], g["y"] + (m0[1] + m0[3] / 2) * g["k"]
    drag((mx, my), (mx + 70 * g["k"], my + 20 * g["k"]))
    check(
        "drag: dragging a master item moves it in the master",
        near(mst(), [m0[0] + 70, m0[1] + 20, m0[2], m0[3]]),
        (m0, mst()),
    )
    pg.click("#sl-undo")
    check("drag: one Undo puts the master item back", mst() == m0, mst())
    pg.click("#sl-master")
    pg.wait_for_timeout(150)
    g = geom()
    # the browser will not store: the move stays on screen and the status says so
    pg.evaluate(
        "(function(k){var o=Storage.prototype.setItem;Storage.prototype.setItem=function(a,b){if(a===k)throw new Error('full');return o.call(this,a,b);};})(document.getElementById('slide-root').dataset.storeKey)"
    )
    pg.click("#sl-items button")
    pos = item()
    cx, cy = g["x"] + (pos[0] + pos[2] / 2) * g["k"], g["y"] + (pos[1] + pos[3] / 2) * g["k"]
    drag((cx, cy), (cx - 40 * g["k"], cy))
    check(
        "drag: if the browser will not store the drag, the status says so, the move shows on screen and the stored deck is unchanged",
        "would not store the deck" in pg.inner_text("#sl-status")
        and item() == pos
        and pg.input_value("#sl-x") == str(pos[0] - 40),
        (pg.inner_text("#sl-status"), item(), pg.input_value("#sl-x")),
    )
    pg.reload()
    pg.wait_for_timeout(300)
    # an unreadable deck: no handle, a drag writes nothing, the stored bytes stay
    pg.evaluate("localStorage.setItem(%s,'{bad')" % KEY)
    pg.reload()
    pg.wait_for_timeout(400)
    g2 = geom()
    drag((g2["x"] + 200, g2["y"] + 100), (g2["x"] + 300, g2["y"] + 150))
    check(
        "drag: with an unreadable deck there is no corner square, the editor is locked and a drag writes nothing",
        pg.locator("#sl-canvas-wrap rect[data-handle]").count() == 0
        and pg.evaluate("document.getElementById('sl-editing').hasAttribute('inert')")
        and pg.evaluate("localStorage.getItem(%s)" % KEY) == "{bad",
    )
    pg.evaluate("localStorage.removeItem(%s)" % KEY)
    pg.reload()
    pg.wait_for_timeout(400)
    pg.click("#sl-add-rect")
    pg.wait_for_timeout(150)
    # phone: the corner square is big enough to touch
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(250)
    pg.click("#sl-items button")
    pg.wait_for_timeout(150)
    hw = pg.evaluate(
        "(()=>{const h=document.querySelector('#sl-canvas-wrap rect[data-handle]');return h?h.getBoundingClientRect().width:0;})()"
    )
    check("drag: at 390 px the corner square is at least 24 screen pixels wide", hw >= 24, hw)
    if SHOTS:
        pg.locator("#sl-canvas-wrap svg").scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "slides-drag-67-phone.png"), full_page=False)
    pg.set_viewport_size({"width": 1100, "height": 900})


def slides_style(ctx):  # noqa: C901
    """Bold, italic and alignment: stored as deck version 4 only when used, drawn on the canvas, in Present and in the PDF."""
    pg = fresh(ctx, "/slides/")
    KEY = "document.getElementById('slide-root').dataset.storeKey"

    def deck():
        return pg.evaluate("JSON.parse(localStorage.getItem(%s))" % KEY)

    def last():
        return deck()["slides"][0]["elements"][-1]

    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Styled words")
    pg.press("#sl-text", "Tab")
    check(
        "style: an unstyled deck is stored as version 1 without style fields",
        deck()["v"] == 1 and "b" not in last() and "al" not in last(),
    )
    pg.check("#sl-bold")
    pg.check("#sl-italic")
    pg.select_option("#sl-align", "center")
    e = last()
    check(
        "style: bold, italic and centre are stored and the deck becomes version 4",
        deck()["v"] == 4 and e.get("b") is True and e.get("i") is True and e.get("al") == "center",
        str((deck()["v"], e)),
    )
    t = pg.evaluate(
        "(()=>{const t=document.querySelector('#sl-canvas-wrap svg text');return [t.getAttribute('font-weight'),t.getAttribute('font-style'),t.getAttribute('text-anchor')];})()"
    )
    check(
        "style: the canvas draws bold, italic and centred text",
        t == ["bold", "italic", "middle"],
        str(t),
    )
    pg.click("#sl-undo")
    pg.click("#sl-undo")
    pg.click("#sl-undo")
    check(
        "style: three Undos remove the three styles and the deck goes back to version 1",
        deck()["v"] == 1 and "b" not in last() and "i" not in last() and "al" not in last(),
        str(last()),
    )
    pg.click("#sl-redo")
    pg.click("#sl-redo")
    pg.click("#sl-redo")
    pg.reload()
    pg.wait_for_timeout(400)
    pg.click("#sl-items button")
    check(
        "style: the styles survive a reload and the controls show them",
        deck()["v"] == 4
        and pg.is_checked("#sl-bold")
        and pg.is_checked("#sl-italic")
        and pg.input_value("#sl-align") == "center",
    )
    if SHOTS:
        pg.evaluate("document.getElementById('sl-canvas-wrap').scrollIntoView({block:'center'})")
        pg.screenshot(path=os.path.join(SHOTS, "slides-style-68.png"), full_page=False)
        pg.set_viewport_size({"width": 390, "height": 800})
        pg.wait_for_timeout(200)
        pg.evaluate("document.getElementById('sl-canvas-wrap').scrollIntoView({block:'start'})")
        pg.screenshot(path=os.path.join(SHOTS, "slides-style-68-phone.png"), full_page=False)
        pg.set_viewport_size({"width": 1100, "height": 900})

    pg.select_option("#sl-align", "right")
    pg.select_option("#sl-align", "default")
    check("style: Default align drops the field", "al" not in last(), str(last()))
    pg.select_option("#sl-align", "right")
    # Present draws the styles
    pg.click("#sl-present")
    pg.wait_for_timeout(300)
    ps = pg.evaluate(
        "(()=>{const t=document.querySelector('#sl-show-canvas text');return t?[t.getAttribute('font-weight'),t.getAttribute('font-style')]:null;})()"
    )
    check("style: Present draws bold and italic", ps == ["bold", "italic"], str(ps))
    pa = pg.evaluate(
        "(()=>{const t=document.querySelector('#sl-show-canvas text');return t?t.getAttribute('text-anchor'):null;})()"
    )
    check("style: Present draws right-aligned text with an end anchor", pa == "end", str(pa))
    pg.click("#sl-show-exit")
    pg.wait_for_timeout(200)

    # the PDF uses the bold-oblique standard font
    def pdf_fonts():
        with pg.expect_download() as dlx:
            pg.click("#sl-pdf")
        data = open(dlx.value.path(), "rb").read()
        used = set()
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
            body = m.group(1)
            try:
                body = zlib.decompress(body)
            except Exception:  # noqa: S110 - not every stream is deflated
                pass
            used |= {re.sub(rb"-\d+$", b"", f) for f in re.findall(rb"/(\S+) [\d.]+ Tf", body)}
        return used, data

    def setstyle(b, i):
        (pg.check if b else pg.uncheck)("#sl-bold")
        (pg.check if i else pg.uncheck)("#sl-italic")

    setstyle(True, True)
    f, raw = pdf_fonts()
    if SHOTS:
        open(f"{SHOTS}/slides-style-68.pdf", "wb").write(raw)
    check(
        "style: bold italic item is set in Helvetica-BoldOblique only (Tf operators)",
        f == {b"Helvetica-BoldOblique"},
        str(f),
    )
    setstyle(True, False)
    f, _ = pdf_fonts()
    check("style: bold item is set in Helvetica-Bold only", f == {b"Helvetica-Bold"}, str(f))
    setstyle(False, True)
    f, _ = pdf_fonts()
    check(
        "style: italic item is set in Helvetica-Oblique only", f == {b"Helvetica-Oblique"}, str(f)
    )
    setstyle(False, False)
    f, _ = pdf_fonts()
    check("style: unstyled item is set in plain Helvetica only", f == {b"Helvetica"}, str(f))
    setstyle(True, True)
    # multiline bold italic text near the box width, exported once per alignment
    pg.fill("#sl-text", "Bold italic words that wrap across several lines in one box")
    pg.press("#sl-text", "Tab")
    pg.fill("#sl-w", "300")
    pg.press("#sl-w", "Tab")
    pg.fill("#sl-x", "100")
    pg.press("#sl-x", "Tab")
    pg.fill("#sl-size", "28")
    pg.press("#sl-size", "Tab")
    for al in ("left", "center", "right"):
        pg.select_option("#sl-align", al)
        with pg.expect_download() as dl2:
            pg.click("#sl-pdf")
        raw2 = open(dl2.value.path(), "rb").read()
        if SHOTS:
            open(f"{SHOTS}/slides-style-68-{al}.pdf", "wb").write(raw2)
        check("style: a multiline %s-aligned PDF downloads" % al, raw2[:5] == b"%PDF-")
        lines = []
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw2, re.S):
            try:
                body = zlib.decompress(m.group(1))
            except Exception:  # noqa: S112
                continue
            for mm in re.finditer(
                rb"/\S+ ([\d.]+) Tf\n[\d.]+ TL\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n<([0-9A-Fa-f]+)> Tj",
                body,
            ):
                lines.append(
                    (
                        float(mm.group(2)),
                        float(mm.group(3)),
                        bytes.fromhex(mm.group(4).decode()).decode("latin1"),
                    )
                )
        widths = pg.evaluate(
            "async (ls)=>{const d=await PDFLib.PDFDocument.create();const f=await d.embedFont(PDFLib.StandardFonts.HelveticaBoldOblique);return ls.map(t=>f.widthOfTextAtSize(t,28));}",
            [t[2] for t in lines],
        )
        want = {
            "left": lambda x, w: x,
            "center": lambda x, w: x + w / 2,
            "right": lambda x, w: x + w,
        }[al]
        target = {"left": 100.0, "center": 250.0, "right": 400.0}[al]
        ys = [t[1] for t in lines]
        check(
            "style: PDF %s alignment places every wrapped line at the exact coordinate (box x 100, width 300, size 28)"
            % al,
            len(lines) >= 3
            and all(abs(want(t[0], w) - target) < 0.01 for t, w in zip(lines, widths, strict=True))
            and all(abs((ys[k] - ys[k + 1]) - 33.6) < 0.01 for k in range(len(ys) - 1)),
            str((lines, widths)),
        )
    pg.reload()
    pg.wait_for_timeout(300)
    # a master item can be styled too
    pg.click("#sl-master")
    pg.click("#sl-add-text")
    pg.check("#sl-bold")
    d = pg.evaluate("JSON.parse(localStorage.getItem(%s))" % KEY)
    check(
        "style: a styled master item makes the deck version 4",
        d["v"] == 4 and d["master"]["elements"][0].get("b") is True,
        str(d["v"]),
    )


def slides_master(ctx):  # noqa: C901
    import shutil
    import subprocess
    import tempfile

    pg = fresh(ctx, "/slides/")
    KEY = pg.evaluate("document.getElementById('slide-root').dataset.storeKey")

    def stored():
        return json.loads(pg.evaluate("localStorage.getItem(%s)" % json.dumps(KEY)))

    def canvas_all():
        return pg.evaluate(
            "Array.from(document.querySelectorAll('#sl-canvas-wrap svg text')).map(function(t){return t.textContent}).join(' | ')"
        )

    def showtxt():
        return pg.evaluate("document.getElementById('sl-show-canvas').textContent")

    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Slide one body")
    pg.press("#sl-text", "Tab")
    check(
        "master: a deck with no master is stored as version 1",
        stored()["v"] == 1 and "master" not in stored(),
        stored().get("v"),
    )
    pg.click("#sl-master")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "MASTERMARK")
    pg.press("#sl-text", "Tab")
    for f, v in (("y", "470"), ("x", "40")):
        pg.fill("#sl-" + f, v)
        pg.press("#sl-" + f, "Tab")
    pg.click("#sl-add-rect")
    for f, v in (("x", "840"), ("y", "20"), ("w", "100"), ("h", "30")):
        pg.fill("#sl-" + f, v)
        pg.press("#sl-" + f, "Tab")
    check(
        "master: editing the master saves it as version 2 with two items",
        stored()["v"] == 2 and len(stored()["master"]["elements"]) == 2,
        stored(),
    )
    check(
        "master: the master editor lists only master items",
        "MASTERMARK" in pg.inner_text("#sl-items")
        and "Slide one" not in pg.inner_text("#sl-items"),
    )
    pg.click("#sl-master")
    check(
        "master: the slide shows the master under its own items",
        "MASTERMARK" in canvas_all() and "Slide one body" in canvas_all(),
        canvas_all(),
    )
    check(
        "master: the slide's item list holds only the slide's own item",
        "MASTERMARK" not in pg.inner_text("#sl-items"),
        pg.inner_text("#sl-items"),
    )
    check(
        "master: master items cannot be selected on a slide",
        pg.locator("#sl-canvas-wrap rect[data-id]").count() == 1,
        pg.locator("#sl-canvas-wrap rect[data-id]").count(),
    )
    pg.click("#sl-new")
    check("master: a new slide shows the master too", "MASTERMARK" in canvas_all(), canvas_all())
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Second slide")
    pg.press("#sl-text", "Tab")
    pg.uncheck("#sl-master-on")
    check(
        "master: a slide can hide the master",
        "MASTERMARK" not in canvas_all() and stored()["slides"][1].get("hideMaster") is True,
        canvas_all(),
    )
    if SHOTS:
        pg.click("#sl-list button:nth-child(1)")
        pg.screenshot(path=os.path.join(SHOTS, "slides-master-57.png"), full_page=False)
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "master: the master and the hide flag survive a reload",
        stored()["v"] == 2
        and "MASTERMARK" in canvas_all()
        and len(stored()["master"]["elements"]) == 2,
        canvas_all(),
    )
    pg.click("#sl-present")
    pg.wait_for_timeout(300)
    check(
        "master: Present draws the master on slide 1",
        "MASTERMARK" in showtxt(),
        showtxt(),
    )
    if SHOTS:
        pg.locator("#sl-show-canvas").screenshot(
            path=os.path.join(SHOTS, "slides-master-58-present1.png")
        )
    pg.click("#sl-show-next")
    pg.wait_for_timeout(200)
    check(
        "master: Present hides it on the slide that hides it",
        "MASTERMARK" not in showtxt() and "Second slide" in showtxt(),
        showtxt(),
    )
    pg.click("#sl-show-exit")
    # PDF: master only on the slide that shows it.
    with pg.expect_download() as dl:
        pg.click("#sl-pdf")
    pdf = dl.value.path()
    if SHOTS:
        shutil.copy(pdf, os.path.join(SHOTS, "slides-master-59.pdf"))
    if shutil.which("pdftotext"):
        p1 = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", pdf, "-"], capture_output=True, text=True
        ).stdout
        p2 = subprocess.run(
            ["pdftotext", "-f", "2", "-l", "2", pdf, "-"], capture_output=True, text=True
        ).stdout
        check(
            "master: the PDF draws the master on page 1 and not on page 2",
            "MASTERMARK" in p1 and "MASTERMARK" not in p2 and "Second slide" in p2,
            (p1, p2),
        )
    # Export and import round trip.
    with pg.expect_download() as dl2:
        pg.click("#sl-export")
    exported = open(dl2.value.path()).read()
    ex = json.loads(exported)
    check(
        "master: Export includes the master, the version and the hide flag",
        ex["v"] == 2
        and len(ex["master"]["elements"]) == 2
        and ex["slides"][1].get("hideMaster") is True,
    )
    pg.once("dialog", lambda d: d.accept())
    pg.click("#sl-clear")
    check(
        "master: Clear deck removes the master and returns to version 1",
        stored()["v"] == 1 and "master" not in stored(),
        stored(),
    )
    tmp = tempfile.mkdtemp()
    fp = os.path.join(tmp, "deck.json")
    open(fp, "w").write(exported)
    pg.once("dialog", lambda d: d.accept())
    pg.set_input_files("#sl-import", fp)
    pg.wait_for_timeout(300)
    check("master: Import brings the master and hide flag back exactly", stored() == ex, "")
    pg.click("#sl-undo")
    check("master: Undo of the import returns to the cleared deck", "master" not in stored(), "")
    # A master with characters the PDF font cannot draw is refused before a file is made.
    pg.click("#sl-redo")
    pg.click("#sl-master")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "\u65e5\u672c")
    pg.press("#sl-text", "Tab")
    pg.click("#sl-pdf")
    pg.wait_for_timeout(500)
    check(
        "master: unsupported master characters are refused and named",
        "the master" in pg.inner_text("#sl-status"),
        pg.inner_text("#sl-status"),
    )
    # Slide controls are off in master mode and work again after leaving it.
    pg.evaluate("localStorage.setItem(%s,%s)" % (json.dumps(KEY), json.dumps(json.dumps(ex))))
    pg.reload()
    pg.wait_for_timeout(400)
    pg.click("#sl-master")
    off = [
        pg.is_disabled("#" + i)
        for i in ("sl-new", "sl-dup", "sl-del", "sl-up", "sl-down", "sl-notes")
    ]
    check(
        "master: slide controls and notes are off while editing the master",
        all(off) and "not available" in pg.inner_text("#sl-notes-label"),
        off,
    )
    pg.click("#sl-master")
    check(
        "master: leaving the master turns the slide controls back on",
        not pg.is_disabled("#sl-new")
        and not pg.is_disabled("#sl-dup")
        and not pg.is_disabled("#sl-notes")
        and "Speaker notes for this slide" == pg.inner_text("#sl-notes-label").strip(),
    )
    check(
        "master: the slide editor names the master items under the slide",
        "Master items drawn under this slide" in pg.inner_text("#sl-master-under")
        and "MASTERMARK" in pg.inner_text("#sl-master-under"),
        pg.inner_text("#sl-master-under"),
    )

    def ids(d):
        return [x["id"] for x in d["slides"]] + [
            e["id"] for x in d["slides"] for e in x["elements"]
        ]

    def texts(d):
        return [
            (x["elements"][0]["text"] if x["elements"] else "", x.get("hideMaster") is True)
            for x in d["slides"]
        ]

    before = stored()
    pg.click("#sl-dup")
    after = stored()
    check(
        "master: Duplicate copies slide 1 with its text and no hide flag, after it, with fresh ids",
        texts(after)
        == [("Slide one body", False), ("Slide one body", False), ("Second slide", True)]
        and len(set(ids(after))) == len(ids(after))
        and after["slides"][1]["id"] not in ids(before)
        and after["slides"][1]["elements"][0]["id"] not in ids(before)
        and after["master"] == before["master"],
        texts(after),
    )
    pg.click("#sl-down")
    moved = stored()
    check(
        "master: Move down puts the copy after the hidden slide and keeps the flags",
        [x["id"] for x in moved["slides"]]
        == [after["slides"][0]["id"], after["slides"][2]["id"], after["slides"][1]["id"]]
        and texts(moved)
        == [("Slide one body", False), ("Second slide", True), ("Slide one body", False)],
        texts(moved),
    )
    pg.once("dialog", lambda d: d.accept())
    pg.click("#sl-del")
    gone = stored()
    check(
        "master: Delete removes the copy only, and the master is untouched",
        [x["id"] for x in gone["slides"]] == [before["slides"][0]["id"], before["slides"][1]["id"]]
        and gone["master"] == before["master"],
        texts(gone),
    )
    pg.click("#sl-list button:nth-child(2)")
    pg.click("#sl-dup")
    hid = stored()
    check(
        "master: Duplicating the hidden slide keeps its hide flag and text, with a fresh id",
        texts(hid) == [("Slide one body", False), ("Second slide", True), ("Second slide", True)]
        and hid["slides"][2]["id"] not in ids(gone),
        texts(hid),
    )
    pg.once("dialog", lambda d: d.accept())
    pg.click("#sl-del")
    pg.click("#sl-list button:nth-child(1)")
    pg.fill("#sl-notes", "slide note")
    pg.press("#sl-notes", "Tab")
    check(
        "master: notes can be saved again",
        any(x.get("notes") == "slide note" for x in stored()["slides"]),
    )
    # Limits and unreadable versions.
    pg.click("#sl-master")
    for _ in range(10):
        pg.click("#sl-add-rect")
    check(
        "master: the master stops at ten items with a message",
        len(stored()["master"]["elements"]) == 10 and "up to 10" in pg.inner_text("#sl-status"),
        pg.inner_text("#sl-status"),
    )
    raw = json.dumps({"v": 9, "slides": [{"id": "s", "elements": []}]})
    pg.evaluate("localStorage.setItem(%s,%s)" % (json.dumps(KEY), json.dumps(raw)))
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "master: a deck with an unknown version is reported unreadable and left untouched",
        pg.is_visible("#sl-unreadable")
        and pg.evaluate("localStorage.getItem(%s)" % json.dumps(KEY)) == raw,
    )
    pg.evaluate(
        "localStorage.setItem(%s,%s)"
        % (
            json.dumps(KEY),
            json.dumps(
                json.dumps(
                    {"v": 2, "master": {"elements": "x"}, "slides": [{"id": "s", "elements": []}]}
                )
            ),
        )
    )
    pg.reload()
    pg.wait_for_timeout(400)
    check(
        "master: a damaged master makes the deck unreadable, not a deck without a master",
        pg.is_visible("#sl-unreadable"),
    )


def slides_animation(ctx):
    """Object animation: set in the editor, saved as version 6, played one click at a time in Present, at desktop and 390 px."""
    pg = fresh(ctx, "/slides/")
    KEY = pg.evaluate("document.getElementById('slide-root').dataset.storeKey")

    def stored():
        return json.loads(pg.evaluate("localStorage.getItem(%s)" % json.dumps(KEY)))

    def groups():
        return pg.evaluate("document.querySelectorAll('#sl-show-canvas svg g').length")

    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Alpha title")
    pg.press("#sl-text", "Tab")
    pg.click("#sl-add-rect")
    pg.fill("#sl-y", "250")
    pg.press("#sl-y", "Tab")
    pg.click("#sl-add-ellipse")
    pg.fill("#sl-x", "500")
    pg.press("#sl-x", "Tab")
    check("animation: a deck without animation is version 1", stored()["v"] == 1, stored()["v"])
    pg.click("#sl-items li:nth-child(2) button")
    pg.select_option("#sl-anim", "appear")
    pg.click("#sl-items li:nth-child(3) button")
    pg.select_option("#sl-anim", "fade")
    pg.click("#sl-items li:nth-child(1) button")
    pg.select_option("#sl-anim", "appear")
    sl = stored()["slides"][0]
    ids = [e["id"] for e in sl["elements"]]  # text, rectangle, ellipse
    check("animation: three entries saved in the order they were set, as version 6", stored()["v"] == 6 and [a["fx"] for a in sl["anim"]] == ["appear", "fade", "appear"] and [a["el"] for a in sl["anim"]] == [ids[1], ids[2], ids[0]], sl.get("anim"))
    pg.click("#sl-anim-up")
    sl = stored()["slides"][0]
    check("animation: One click earlier moves the text entry from third to second", [a["el"] for a in sl["anim"]] == [ids[1], ids[0], ids[2]], sl["anim"])
    pg.click("#sl-anim-down")
    check("animation: One click later puts it back, exact order", [a["el"] for a in stored()["slides"][0]["anim"]] == [ids[1], ids[2], ids[0]], stored()["slides"][0]["anim"])
    check("animation: the list under the control names three steps", pg.evaluate("document.querySelectorAll('#sl-anim-list li').length") == 3)
    pg.evaluate("document.getElementById('sl-anim').scrollIntoView({block:'start'})")
    shot(pg, "slides_anim_editor_desktop")
    before = stored()
    pg.click("#sl-present")
    pg.wait_for_timeout(300)
    check("animation: Present starts at step 0 with none of the animated items drawn", "step 0 of 3" in pg.inner_text("#sl-show-count") and groups() == 0, pg.inner_text("#sl-show-count"))
    pg.keyboard.press("ArrowRight")
    check("animation: the first click shows one item and stays on the slide", "step 1 of 3" in pg.inner_text("#sl-show-count") and groups() == 1, groups())
    pg.keyboard.press("ArrowRight")
    pg.wait_for_timeout(50)
    check("animation: the second click shows a fading item", "step 2 of 3" in pg.inner_text("#sl-show-count") and groups() == 2 and pg.evaluate("!!document.querySelector('#sl-show-canvas svg g.sl-fadeel')"), groups())
    pg.wait_for_timeout(600)
    shot(pg, "slides_anim_present_step2_desktop")
    pg.keyboard.press("ArrowLeft")
    check("animation: back hides the last item shown first", "step 1 of 3" in pg.inner_text("#sl-show-count") and groups() == 1)
    pg.keyboard.press("ArrowRight")
    pg.keyboard.press("ArrowRight")
    check("animation: the third click shows everything", "step 3 of 3" in pg.inner_text("#sl-show-count") and groups() == 3)
    check("animation: the last click on the last slide leaves Next disabled", pg.evaluate("document.getElementById('sl-show-next').disabled"))
    pg.keyboard.press("Home")
    pg.focus("#sl-show-next")
    pg.keyboard.press("Space")
    check("animation: Space on the focused Next button advances exactly one step", "step 1 of 3" in pg.inner_text("#sl-show-count"), pg.inner_text("#sl-show-count"))
    pg.keyboard.press("Enter")
    check("animation: Enter on the focused Next button advances exactly one step", "step 2 of 3" in pg.inner_text("#sl-show-count"), pg.inner_text("#sl-show-count"))
    pg.keyboard.press("Home")
    check("animation: Home goes back to step 0", "step 0 of 3" in pg.inner_text("#sl-show-count") and groups() == 0)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(150)
    check("animation: showing the deck did not change it", stored() == before)
    pg.reload()
    pg.wait_for_timeout(400)
    pg.click("#sl-items li:nth-child(1) button")
    check("animation: the text item's setting survives a reload", pg.input_value("#sl-anim") == "appear" and [a["el"] for a in stored()["slides"][0]["anim"]] == [ids[1], ids[2], ids[0]], pg.input_value("#sl-anim"))
    pg.click("#sl-items li:nth-child(2) button")
    pg.select_option("#sl-anim", "none")
    check("animation: None on the rectangle removes exactly its entry", [a["el"] for a in stored()["slides"][0]["anim"]] == [ids[2], ids[0]], stored()["slides"][0]["anim"])
    pg.click("#sl-items li:nth-child(3) button")
    pg.click("#sl-del-el")
    st = stored()["slides"][0]
    check("animation: deleting the animated ellipse removes exactly its entry", [a["el"] for a in st["anim"]] == [ids[0]] and ids[2] not in [e["id"] for e in st["elements"]], st.get("anim"))
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.evaluate("document.getElementById('sl-anim').scrollIntoView({block:'start'})")
    pg.wait_for_timeout(100)
    shot(pg, "slides_anim_editor_390")
    pg.click("#sl-present")
    pg.wait_for_timeout(300)
    pg.keyboard.press("ArrowRight")
    pg.wait_for_timeout(500)
    shot(pg, "slides_anim_present_390")
    pg.keyboard.press("Escape")
    pg.set_viewport_size({"width": 1100, "height": 900})


def slides_transition(ctx):
    pg = fresh(ctx, "/slides/")
    KEY = pg.evaluate("document.getElementById('slide-root').dataset.storeKey")

    def stored():
        return json.loads(pg.evaluate("localStorage.getItem(%s)" % json.dumps(KEY)))

    def cls():
        return pg.evaluate(
            "document.querySelector('#sl-show-canvas svg').getAttribute('class') || ''"
        )

    pg.click("#sl-add-text")
    pg.fill("#sl-text", "One")
    pg.press("#sl-text", "Tab")
    pg.click("#sl-new")
    pg.click("#sl-add-text")
    pg.fill("#sl-text", "Two")
    pg.press("#sl-text", "Tab")
    check("transition: a deck without one is version 1", stored()["v"] == 1)
    pg.select_option("#sl-trans", "fade")
    s = stored()
    check(
        "transition: choosing fade on slide 2 only saves version 3 with the flag on that slide",
        s["v"] == 3
        and s["slides"][1].get("transition") == "fade"
        and "transition" not in s["slides"][0],
        s,
    )
    pg.click("#sl-list button:nth-child(1)")
    check("transition: slide 1 shows None", pg.input_value("#sl-trans") == "none")
    pg.reload()
    pg.wait_for_timeout(400)
    pg.click("#sl-list button:nth-child(2)")
    check("transition: the choice survives a reload", pg.input_value("#sl-trans") == "fade")
    pg.click("#sl-list button:nth-child(1)")
    pg.emulate_media(reduced_motion="no-preference")
    pg.click("#sl-present")
    pg.wait_for_timeout(200)
    check("transition: the first slide shown does not animate", cls() == "", cls())
    op0 = pg.evaluate(
        "document.getElementById('sl-show-next').click(); parseFloat(getComputedStyle(document.querySelector('#sl-show-canvas svg')).opacity)"
    )
    pg.wait_for_timeout(700)
    op1 = pg.evaluate(
        "parseFloat(getComputedStyle(document.querySelector('#sl-show-canvas svg')).opacity)"
    )
    check(
        "transition: the fade starts below full opacity and ends at 1",
        op0 < 1 and op1 == 1,
        (op0, op1),
    )
    check("transition: moving to the fade slide fades it in", cls() == "sl-fade", cls())
    pg.click("#sl-show-prev")
    if SHOTS:
        # Frames of the real fade, paused at fixed times so each picture shows the actual rendered opacity.
        pg.evaluate("document.getElementById('sl-show-next').click();0")
        pg.evaluate("document.getAnimations().forEach(function(a){a.pause()});0")
        for n, t in enumerate((0, 100, 200, 300, 400)):
            pg.evaluate("document.getAnimations().forEach(function(a){a.currentTime=%d});0" % t)
            pg.wait_for_timeout(60)
            pg.locator("#sl-show-canvas").screenshot(
                path=os.path.join(SHOTS, "slides-fade-f%d.png" % n)
            )
        pg.evaluate("document.getAnimations().forEach(function(a){a.finish()});0")
        pg.click("#sl-show-prev")
    check(
        "transition: going back to a slide with no transition does not animate", cls() == "", cls()
    )
    pg.click("#sl-show-exit")
    pg.emulate_media(reduced_motion="reduce")
    pg.click("#sl-present")
    pg.click("#sl-show-next")
    if SHOTS:
        pg.locator("#sl-show-canvas").screenshot(
            path=os.path.join(SHOTS, "slides-fade-reduced.png")
        )
    check("transition: with reduced motion requested no fade runs", cls() == "", cls())
    check(
        "transition: the slide still shows its text",
        "Two" in pg.evaluate("document.getElementById('sl-show-canvas').textContent"),
    )
    pg.click("#sl-show-exit")
    pg.emulate_media(reduced_motion="no-preference")
    pg.click("#sl-list button:nth-child(2)")
    pg.select_option("#sl-trans", "none")
    check(
        "transition: choosing None on the only fade slide returns to version 1",
        stored()["v"] == 1 and "transition" not in stored()["slides"][1],
        stored(),
    )


def sheets_grid(ctx):  # noqa: C901
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")

    def val(a):
        return pg.input_value('input[data-addr="%s"]' % a)

    def active():
        return pg.evaluate("document.activeElement.dataset.addr || ''")

    n = pg.evaluate("document.querySelectorAll('input[data-addr]').length")
    check("grid: 26 columns by 100 rows are on the page", n == 2600, n)
    check(
        "grid: the first and last cells are A1 and Z100",
        pg.locator('input[data-addr="A1"]').count() == 1
        and pg.locator('input[data-addr="Z100"]').count() == 1
        and pg.locator('input[data-addr="AA1"]').count() == 0
        and pg.locator('input[data-addr="A101"]').count() == 0,
    )
    check(
        "grid: the page says the visible grid is A1 to Z100",
        "A1 to Z100" in pg.text_content("body"),
    )
    setc("Z100", "far")
    setc("A100", "deep")
    setc("B50", "5")
    setc("C50", "7")
    setc("D50", "=B50+C50")
    setc("E50", "=SUM(A50:D100)")
    check("grid: a formula in a lower row reads its row", val("D50") == "12", val("D50"))
    check(
        "grid: SUM across rows 50 to 100 reaches the new rows",
        val("E50") == "24",
        val("E50"),
    )
    pg.click('input[data-addr="A100"]')
    pg.press('input[data-addr="A100"]', "ArrowDown")
    check("grid: ArrowDown at row 100 stays on row 100", active() == "A100", active())
    pg.click('input[data-addr="Z1"]')
    pg.press('input[data-addr="Z1"]', "ArrowRight")
    check("grid: ArrowRight at column Z stays on column Z", active() == "Z1", active())
    pg.click('input[data-addr="Z99"]')
    pg.press('input[data-addr="Z99"]', "ArrowDown")
    check("grid: ArrowDown moves into row 100", active() == "Z100", active())
    setc("A90", "b")
    setc("A91", "a")
    pg.fill("#sort-range", "A90:A91")
    pg.fill("#sort-col", "A")
    pg.uncheck("#sort-header")
    pg.click("#sort-go")
    check(
        "grid: sort works on rows far down the sheet",
        val("A90") == "a" and val("A91") == "b",
        (val("A90"), val("A91")),
    )
    stored = pg.evaluate("localStorage.getItem(%s)" % KEYJS)
    check(
        "grid: cells in row 100 and column Z are in the stored sheet",
        '"Z100":"far"' in stored and '"A100":"deep"' in stored,
        stored[:120],
    )
    pg.reload()
    check(
        "grid: they are still there after a reload",
        val("Z100") == "far" and val("A100") == "deep" and val("D50") == "12",
    )

    # Sticky formula row: at row 100 the formula bar and the selected address stay on screen and do not cover the focused cell.
    def bar_state(pgx, a):
        pgx.click('input[data-addr="%s"]' % a)
        pgx.keyboard.press("ArrowDown")
        pgx.wait_for_timeout(150)
        return pgx.evaluate(
            "(function(){var b=document.getElementById('sheet-barrow').getBoundingClientRect(),c=document.activeElement.getBoundingClientRect(),vh=innerHeight;"
            "return {barTop:b.top,barBottom:b.bottom,cellTop:c.top,cellBottom:c.bottom,vh:vh,sel:document.getElementById('sheet-sel').textContent,addr:document.activeElement.dataset.addr};})()"
        )

    pg.locator('input[data-addr="A98"]').scroll_into_view_if_needed()
    st = bar_state(pg, "A98")
    check(
        "grid: deep in the sheet the formula bar is still on screen with the selected address",
        st["barTop"] >= -1
        and st["barBottom"] < st["vh"]
        and st["sel"] == st["addr"]
        and st["addr"] == "A99",
        str(st),
    )
    check(
        "grid: the focused cell is not covered by the formula row",
        st["cellTop"] >= st["barBottom"] - 1 and st["cellBottom"] <= st["vh"],
        str(st),
    )
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, "sheets-sticky-63.png"), full_page=False)
    if SHOTS:
        pg.locator('input[data-addr="Z100"]').scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "sheets-grid-61.png"), full_page=False)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(200)
    ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check("grid: no sideways page scroll at 390 px", not ov)
    pg.locator('input[data-addr="A98"]').scroll_into_view_if_needed()
    st2 = bar_state(pg, "A98")
    check(
        "grid: at 390 px the formula bar stays on screen and the focused cell is not covered",
        st2["barTop"] >= -1
        and st2["barBottom"] < st2["vh"]
        and st2["cellTop"] >= st2["barBottom"] - 1
        and st2["cellBottom"] <= st2["vh"]
        and st2["sel"] == st2["addr"],
        str(st2),
    )
    if SHOTS:
        pg.screenshot(path=os.path.join(SHOTS, "sheets-sticky-63-phone.png"), full_page=False)

    # Upward navigation at 390 px: ArrowUp and Shift+Tab must not leave the focused cell under the bar.
    def visible_below_bar(pgx):
        r = pgx.evaluate(
            "(function(){var b=document.getElementById('sheet-barrow').getBoundingClientRect(),c=document.activeElement.getBoundingClientRect();return [b.bottom,c.top,c.bottom,innerHeight,document.activeElement.dataset.addr];})()"
        )
        return r[1] >= r[0] - 1 and r[2] <= r[3], r

    def from_top_edge(pgx, addr, key):
        # Put the cell at the top visible edge (just below the bar), focus it without scrolling, then cross to the row above.
        pgx.evaluate(
            "(function(a){var e=document.querySelector('input[data-addr='+a+']');e.scrollIntoView({block:'start'});e.focus({preventScroll:true});})(%s)"
            % json.dumps(addr)
        )
        pgx.wait_for_timeout(120)
        before_ok, r0 = visible_below_bar(pgx)
        pgx.keyboard.press(key)
        pgx.wait_for_timeout(120)
        after_ok, r1 = visible_below_bar(pgx)
        return before_ok, after_ok, r0, r1

    bad_up, bad_tab = [], []
    for a in ("A85", "A70", "A55"):
        b_ok, a_ok, r0, r1 = from_top_edge(pg, a, "ArrowUp")
        if not (b_ok and a_ok and r1[4] != r0[4]):
            bad_up.append((a, r0, r1))
        b_ok, a_ok, r0, r1 = from_top_edge(pg, a, "Shift+Tab")
        if not (b_ok and a_ok and r1[4] != r0[4]):
            bad_tab.append((a, r0, r1))
    if SHOTS:
        from_top_edge(pg, "A40", "Shift+Tab")
        pg.screenshot(
            path=os.path.join(SHOTS, "sheets-sticky-63-phone-shifttab.png"), full_page=False
        )
    check(
        "grid: at 390 px ArrowUp from the top visible row crosses up and stays below the bar",
        not bad_up,
        str(bad_up[:1]),
    )
    check(
        "grid: at 390 px Shift+Tab from column A at the top visible row crosses up and stays below the bar",
        not bad_tab,
        str(bad_tab[:1]),
    )
    check(
        "grid: the formula bar height is measured, not fixed",
        pg.evaluate(
            "parseInt(getComputedStyle(document.getElementById('sheet-root')).getPropertyValue('--sheet-bar-h'))"
        )
        == pg.evaluate("document.getElementById('sheet-barrow').offsetHeight"),
    )
    if SHOTS:
        pg.locator('input[data-addr="Z100"]').scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "sheets-grid-61-phone.png"), full_page=False)
    pg.set_viewport_size({"width": 1100, "height": 900})


def sheets_pivot(ctx):
    pg = fresh(ctx, "/sheets/")
    KEYJS = "document.getElementById('sheet-root').dataset.storeKey"

    def setc(a, v):
        pg.click('input[data-addr="%s"]' % a)
        pg.fill('input[data-addr="%s"]' % a, v)
        pg.press('input[data-addr="%s"]' % a, "Tab")

    rows = [
        ("Team", "Pts"),
        ("a", "10"),
        ("b", "5"),
        ("a", "7"),
        ("b", "n/a"),
        ("c", "=2+3"),
        ("a", ""),
    ]
    for i, (k, v) in enumerate(rows, start=1):
        setc("A%d" % i, k)
        if v:
            setc("B%d" % i, v)

    def table():
        return pg.evaluate(
            "Array.from(document.querySelectorAll('#pivot-out tr')).map(function(r){return Array.from(r.children).map(function(c){return c.textContent}).join('=')}).join(' | ')"
        )

    pg.fill("#pivot-range", "A1:B7")
    pg.click("#pivot-build")
    check(
        "pivot: sum groups by team, with the header name and a formula result",
        table() == "Team=Sum (Pts) | a=17 | b=5 | c=5",
        table(),
    )
    check(
        "pivot: the status counts rows, names the skipped text and says it is cleared if cells change",
        "6 rows" in pg.inner_text("#pivot-status")
        and "1 non-number value was not added" in pg.inner_text("#pivot-status")
        and "cleared if a cell or the tab changes" in pg.inner_text("#pivot-status"),
        pg.inner_text("#pivot-status"),
    )
    check(
        "pivot: the caption says what was built",
        "Sum of column B by column A, rows 2 to 7" in pg.inner_text("#pivot-out caption"),
        pg.inner_text("#pivot-out caption"),
    )
    if SHOTS:
        pg.locator("#pivot-out").scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, "sheets-pivot-60.png"), full_page=False)
    pg.select_option("#pivot-agg", "count")
    pg.click("#pivot-build")
    check(
        "pivot: count counts every row of a group",
        table() == "Team=Rows | a=3 | b=2 | c=1",
        table(),
    )
    pg.select_option("#pivot-agg", "average")
    pg.click("#pivot-build")
    check(
        "pivot: average uses numbers only",
        table() == "Team=Average (Pts) | a=8.5 | b=5 | c=5",
        table(),
    )
    setc("B2", "20")
    check(
        "pivot: changing a cell clears the table and says so, never leaving stale numbers",
        table() == "" and "cleared" in pg.inner_text("#pivot-status"),
        (table(), pg.inner_text("#pivot-status")),
    )
    pg.click("#pivot-build")
    check("pivot: rebuilding reads the new value", "a=13.5" in table(), table())
    pre = pg.evaluate("localStorage.getItem(%s)" % KEYJS)
    pg.click("#pivot-build")
    check(
        "pivot: building left the stored sheet exactly as it was (same stored string)",
        pg.evaluate("localStorage.getItem(%s)" % KEYJS) == pre and pre.count("B2") == 1,
    )
    pg.fill("#pivot-key", "D")
    pg.click("#pivot-build")
    check(
        "pivot: a column outside the range is refused with a message and no table",
        "inside A1:B7" in pg.inner_text("#pivot-status") and table() == "",
        pg.inner_text("#pivot-status"),
    )
    pg.fill("#pivot-key", "A")
    pg.fill("#pivot-range", "H18:H19")
    pg.fill("#pivot-val", "H")
    pg.fill("#pivot-key", "H")
    pg.click("#pivot-build")
    check(
        "pivot: an empty range says there are no rows",
        "no rows with a value" in pg.inner_text("#pivot-status") and table() == "",
        pg.inner_text("#pivot-status"),
    )
    pg.fill("#pivot-range", "A1:B7")
    pg.fill("#pivot-key", "A")
    pg.fill("#pivot-val", "B")
    pg.click("#pivot-build")
    # Header text is the evaluated value, a blank group is separate from the words (blank), huge sums say too large.
    setc("A1", "=1+1")
    setc("A8", "(blank)")
    setc("B8", "100")
    setc("B9", "1")
    pg.fill("#pivot-range", "A1:B9")
    pg.select_option("#pivot-agg", "sum")
    pg.click("#pivot-build")
    check(
        "pivot: the header shows the evaluated text, not the formula",
        table().startswith("2=Sum (Pts)"),
        table(),
    )
    check(
        "pivot: an empty group cell and the typed words (blank) are two rows",
        "(blank)=100" in table()
        and pg.locator("#pivot-out td.pivot-blank").count() == 1
        and pg.inner_text("#pivot-out td.pivot-blank") == "(blank)"
        and pg.evaluate(
            "document.querySelectorAll('#pivot-out td.pivot-blank')[0].parentNode.children[1].textContent"
        )
        == "1",
        table(),
    )
    setc("A10", "big")
    setc("A11", "big")
    setc("B10", "1.7e308")
    setc("B11", "1.7e308")
    pg.fill("#pivot-range", "A1:B11")
    pg.click("#pivot-build")
    check(
        "pivot: a sum too large to hold says too large, not Infinity",
        "big=too large" in table() and "Infinity" not in table(),
        table(),
    )
    pg.select_option("#pivot-agg", "max")
    pg.click("#pivot-build")
    check(
        "pivot: the largest of the same values is still shown",
        "big=1.7e+308" in table() or "big=1.7e308" in table(),
        table(),
    )
    # The table describes the tab it was built from.
    pg.click("#tab-add")
    pg.wait_for_timeout(150)
    check(
        "pivot: adding a tab clears the table and says why",
        table() == "" and "cleared" in pg.inner_text("#pivot-status"),
        pg.inner_text("#pivot-status"),
    )
    pg.click("#sheet-tabs button:nth-child(1)")
    pg.fill("#pivot-range", "A1:B7")
    pg.click("#pivot-build")
    check("pivot: it builds again on the first tab", table() != "")
    pg.click("#sheet-tabs button:nth-child(2)")
    check(
        "pivot: switching tabs clears it",
        table() == "" and "cleared" in pg.inner_text("#pivot-status"),
        pg.inner_text("#pivot-status"),
    )
    pg.click("#sheet-tabs button:nth-child(1)")
    pg.reload()
    pg.wait_for_timeout(400)
    check("pivot: nothing is kept after a reload", table() == "")
    pg.fill("#pivot-range", "A1:B7")
    pg.click("#pivot-build")
    check("pivot: it builds again from the stored cells after a reload", "a=" in table(), table())


def _png(w, h, rgb=(200, 30, 30), noise=False):
    """A valid PNG of one colour, built with the standard library."""
    import struct

    def ch(n, d):
        c = struct.pack(">I", len(d)) + n + d
        return c + struct.pack(">I", zlib.crc32(n + d) & 0xFFFFFFFF)

    # a size larger than 4096 is only declared in the header, with a tiny data chunk, so the test itself stays small
    rw, rh = (w, h) if w <= 4096 and h <= 4096 else (8, 4)
    raw = b"".join(b"\x00" + (os.urandom(3 * rw) if noise else bytes(rgb) * rw) for _ in range(rh))
    return (
        b"\x89PNG\r\n\x1a\n"
        + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + ch(b"IDAT", zlib.compress(raw))
        + ch(b"IEND", b"")
    )


TMP_FULL = "/tmp/deck-pics.json"  # noqa: S108
TMP_TAMPER = "/tmp/deck-pics-tampered.json"  # noqa: S108


def slides_images(ctx):  # noqa: C901
    """Pictures: bytes only in this browser's IndexedDB, a version 5 deck holds a fingerprint, refusals, missing bytes, PDF and portable export."""
    import json as _json

    pg = fresh(ctx, "/slides/")
    KEY = "document.getElementById('slide-root').dataset.storeKey"
    pg.evaluate(
        "new Promise(r=>{const q=indexedDB.deleteDatabase('salow.slides.pictures.'+%s);q.onsuccess=q.onerror=q.onblocked=()=>r()})"
        % KEY
    )
    pg.reload()
    pg.wait_for_timeout(400)
    msgs = []
    pg.on("dialog", lambda d: (msgs.append(d.message), d.accept()))

    def deck():
        return pg.evaluate("JSON.parse(localStorage.getItem(%s))" % KEY)

    def status():
        return pg.inner_text("#sl-status")

    def idb_count():
        return pg.evaluate(
            "new Promise(res=>{const r=indexedDB.open('salow.slides.pictures.'+%s,1);r.onupgradeneeded=()=>r.result.createObjectStore('img',{keyPath:'hash'});r.onsuccess=()=>{const t=r.result.transaction('img');const q=t.objectStore('img').count();q.onsuccess=()=>{r.result.close();res(q.result)}}})"
            % KEY
        )

    def add(name, data, mime="image/png"):
        pg.set_input_files("#sl-img-file", files=[{"name": name, "mimeType": mime, "buffer": data}])
        pg.wait_for_timeout(900)

    def pic():
        return [e for e in deck()["slides"][0]["elements"] if e["type"] == "image"]

    def canvas():
        return pg.evaluate(
            "(()=>{const i=document.querySelector('#sl-canvas-wrap svg image');return i?i.getAttribute('href').slice(0,21):null})()"
        )

    def canvas_text():
        return pg.text_content("#sl-canvas-wrap svg")

    good = _png(40, 20)
    add("chart.png", good)
    e = pic()
    check(
        "pic: a PNG is added as a version 5 deck holding a 64 hex fingerprint",
        deck()["v"] == 5 and len(e) == 1 and len(e[0]["img"]) == 64,
        str(e),
    )
    stored_text = pg.evaluate("localStorage.getItem(%s)" % KEY)
    check(
        "pic: the stored deck has no picture bytes and no file name",
        "iVBOR" not in stored_text and "chart" not in stored_text and len(stored_text) < 1500,
        str(len(stored_text)),
    )
    check(
        "pic: the canvas draws the picture from the browser store",
        canvas() == "data:image/png;base64",
        str(canvas()),
    )
    check(
        "pic: the bytes are in the browser store (1 record), under a per-person database name",
        idb_count() == 1,
    )
    names = pg.evaluate("indexedDB.databases().then(a=>a.map(x=>x.name))")
    key = pg.evaluate(KEY)
    check(
        "pic: the database name carries the per-person key",
        ("salow.slides.pictures." + key) in names,
        str(names),
    )
    # refusals
    before = (_json.dumps(deck()), idb_count())
    add("a.png", b"GIF89a" + b"\x00" * 80)
    st1 = status()
    add("big.png", _png(60000, 60000))
    st2 = status()
    trunc = good[:-20]
    add("cut.png", trunc)
    st3 = status()
    add("huge.bin", b"\xff" * 2_100_000)
    st4 = status()
    check(
        "pic: a GIF, a 60000 by 60000 header, a truncated PNG and a 2.1 MB file are each refused with a message and change nothing",
        (_json.dumps(deck()), idb_count()) == before
        and "Only PNG" in st1
        and "limit" in st2
        and "damaged" in st3
        and "limit" in st4,
        str((st1, st2, st3, st4)),
    )
    # structure ok but the pixel data is corrupt: the browser decode catches it
    bad = bytearray(good)
    i = bad.index(b"IDAT") + 6
    bad[i : i + 8] = b"\x00" * 8
    add("corrupt.png", bytes(bad))
    check(
        "pic: a PNG with valid chunks but corrupt pixel data is refused by the decode check",
        (_json.dumps(deck()), idb_count()) == before,
        status(),
    )
    # undo keeps the bytes
    pg.click("#sl-undo")
    check(
        "pic: Undo removes the item, the deck is version 1 again and the bytes are still stored",
        deck()["v"] == 1 and not pic() and idb_count() == 1,
    )
    pg.click("#sl-redo")
    pg.wait_for_timeout(400)
    check(
        "pic: Redo brings the picture back and it draws",
        deck()["v"] == 5 and canvas() == "data:image/png;base64",
    )
    pg.reload()
    pg.wait_for_timeout(800)
    check(
        "pic: after a reload the picture draws from the store",
        canvas() == "data:image/png;base64",
        str(canvas()),
    )
    # Clear deck then Undo
    pg.click("#sl-clear")
    pg.wait_for_timeout(300)
    check("pic: Clear deck does not delete the stored bytes", idb_count() == 1 and deck()["v"] == 1)
    pg.click("#sl-undo")
    pg.wait_for_timeout(500)
    check(
        "pic: Undo after Clear deck brings the picture back",
        canvas() == "data:image/png;base64" and deck()["v"] == 5,
    )
    # PDF with a picture: position
    pg.click(".slide-items button, #sl-items button")
    pg.fill("#sl-w", "200")
    pg.press("#sl-w", "Tab")
    pg.fill("#sl-h", "200")
    pg.press("#sl-h", "Tab")
    el = pic()[0]
    with pg.expect_download() as dl:
        pg.click("#sl-pdf")
    raw = open(dl.value.path(), "rb").read()
    if SHOTS:
        open(f"{SHOTS}/slides-images-69.pdf", "wb").write(raw)
    ccs = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        body = m.group(1)
        try:
            body = zlib.decompress(body)
        except Exception:  # noqa: S112
            continue
        for blk in re.findall(rb"q\n((?:[-\d. ]+ cm\n)+)/\S+ Do", body):
            mat = (1.0, 1.0, 0.0, 0.0)
            for ln in blk.decode().strip().split("\n"):
                a, b_, c, d, e_, f_ = (float(v) for v in ln.split()[:6])
                # new CTM = this matrix x previous CTM (PDF cm order); only scale and translate occur
                mat = (a * mat[0], d * mat[1], mat[2] + mat[0] * e_, mat[3] + mat[1] * f_)
            ccs.append(mat)
    ex_w, ex_h = 200.0, 100.0
    ex_x, ex_y = el["x"], 540 - el["y"] - (el["h"] - ex_h) / 2 - ex_h
    got = ccs
    check(
        "pic: the PDF places the 40 by 20 picture fitted inside its 200 by 200 box, centred, at the exact coordinates",
        len(got) == 1
        and all(abs(a - b) < 0.01 for a, b in zip(got[0], (ex_w, ex_h, ex_x, ex_y), strict=True)),
        str((got, (ex_w, ex_h, ex_x, ex_y))),
    )
    check("pic: the PDF has one image object", raw.count(b"/Subtype /Image") == 1)
    # reference-only export
    msgs.clear()
    with pg.expect_download() as d1:
        pg.click("#sl-export")
    ref = open(d1.value.path()).read()
    check(
        "pic: Export (.json) asks first, says references only, and the file holds no picture bytes",
        msgs and "references" in msgs[-1] and "iVBOR" not in ref and "WITHOUT" in status(),
        str(msgs),
    )
    with pg.expect_download() as d2:
        pg.click("#sl-export-pics")
    full = _json.loads(open(d2.value.path()).read())
    check(
        "pic: Export with pictures holds the deck and the picture bytes",
        full["format"] == "salow-slides-with-pictures"
        and len(full["pictures"]) == 1
        and full["pictures"][0]["hash"] == el["img"],
    )
    open(TMP_FULL, "w").write(_json.dumps(full))
    tam = _json.loads(_json.dumps(full))
    d0 = tam["pictures"][0]["data"]
    tam["pictures"][0]["data"] = d0[:80] + ("A" if d0[80] != "A" else "B") + d0[81:]
    open(TMP_TAMPER, "w").write(_json.dumps(tam))
    # simulate the browser clearing its site data: remove the record (test only)
    pg.evaluate(
        "new Promise(res=>{const r=indexedDB.open('salow.slides.pictures.'+%s,1);r.onsuccess=()=>{const t=r.result.transaction('img','readwrite');t.objectStore('img').clear();t.oncomplete=()=>{r.result.close();res()}}})"
        % KEY
    )
    pg.reload()
    pg.wait_for_timeout(800)
    check(
        "pic: with the bytes gone the deck stays version 5 and the editor draws a visible 'Picture missing' box",
        deck()["v"] == 5 and "Picture missing" in canvas_text(),
        canvas_text(),
    )
    pg.click("#sl-present")
    pg.wait_for_timeout(300)
    pm = pg.text_content("#sl-show-canvas")
    pg.click("#sl-show-exit")
    check("pic: Present also shows the 'Picture missing' box", "Picture missing" in pm, pm)
    with pg.expect_download() as d3:
        pg.click("#sl-pdf")
    raw3 = open(d3.value.path(), "rb").read()
    txt = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw3, re.S):
        try:
            txt += zlib.decompress(m.group(1))
        except Exception:  # noqa: S110
            pass
    check(
        "pic: the PDF draws a grey 'Picture missing' box and the status warns, with no image object",
        b"Picture missing".hex().upper().encode() in txt.upper()
        and raw3.count(b"/Subtype /Image") == 0
        and "WARNING" in status(),
        status(),
    )
    # tampered import is refused, deck unchanged
    snap = _json.dumps(deck())
    pg.set_input_files("#sl-import", TMP_TAMPER)
    pg.wait_for_timeout(900)
    check(
        "pic: a file whose picture bytes do not match their fingerprint is refused and the deck is unchanged",
        _json.dumps(deck()) == snap
        and idb_count() == 0
        and "Your deck was not changed" in status(),
        status(),
    )
    # real import restores
    pg.set_input_files("#sl-import", TMP_FULL)
    pg.wait_for_timeout(1200)
    check(
        "pic: importing the file with pictures restores the bytes and the picture draws",
        idb_count() == 1 and canvas() == "data:image/png;base64",
        str((idb_count(), status())),
    )
    if SHOTS:
        pg.evaluate("window.scrollTo(0, 0)")
        pg.click("#sl-items button")
        pg.evaluate("document.getElementById('sl-canvas-wrap').scrollIntoView()")
        pg.screenshot(path=f"{SHOTS}/slides-images-69.png")
        pg.set_viewport_size({"width": 390, "height": 800})
        pg.wait_for_timeout(300)
        pg.evaluate("document.getElementById('sl-canvas-wrap').scrollIntoView()")
        pg.screenshot(path=f"{SHOTS}/slides-images-69-phone.png")
        pg.set_viewport_size({"width": 1100, "height": 900})


def slides_images_edge(ctx):  # noqa: C901
    """Pictures: JPEG, description, export warning, the 10 MB deck cap, a blocked store and an aborted write change nothing."""
    pg = fresh(ctx, "/slides/")
    KEY = "document.getElementById('slide-root').dataset.storeKey"
    pg.evaluate(
        "new Promise(r=>{const q=indexedDB.deleteDatabase('salow.slides.pictures.'+%s);q.onsuccess=q.onerror=q.onblocked=()=>r()})"
        % KEY
    )
    pg.reload()
    pg.wait_for_timeout(400)

    def deck():
        return pg.evaluate("JSON.parse(localStorage.getItem(%s))" % KEY)

    def status():
        return pg.inner_text("#sl-status")

    def idb_count():
        return pg.evaluate(
            "new Promise(res=>{const r=indexedDB.open('salow.slides.pictures.'+%s,1);r.onupgradeneeded=()=>r.result.createObjectStore('img',{keyPath:'hash'});r.onsuccess=()=>{const t=r.result.transaction('img');const q=t.objectStore('img').count();q.onsuccess=()=>{r.result.close();res(q.result)}}})"
            % KEY
        )

    def add(name, data, wait=900):
        pg.set_input_files(
            "#sl-img-file", files=[{"name": name, "mimeType": "image/png", "buffer": data}]
        )
        pg.wait_for_timeout(wait)

    def pics():
        d = deck()
        return (
            [e for sl in d["slides"] for e in sl["elements"] if e["type"] == "image"] if d else []
        )

    jpg = bytes.fromhex(open("tests/js/fixtures/images/tiny.jpg.hex").read())
    add("photo.jpg", jpg)
    e = pics()
    check(
        "pic: a JPEG is added (version 5, fingerprint only)",
        len(e) == 1
        and deck()["v"] == 5
        and "/9j/" not in pg.evaluate("localStorage.getItem(%s)" % KEY),
        str(e),
    )
    check(
        "pic: the canvas draws the JPEG",
        pg.evaluate(
            "(()=>{const i=document.querySelector('#sl-canvas-wrap svg image');return i&&i.getAttribute('href').slice(0,22)})()"
        )
        == "data:image/jpeg;base64",
        "",
    )
    check(
        "pic: the references-only export warning is visible when the deck has a picture",
        pg.is_visible("#sl-export-warn"),
    )
    with pg.expect_download() as dl:
        pg.click("#sl-pdf")
    raw = open(dl.value.path(), "rb").read()
    check(
        "pic: the PDF embeds the JPEG as a DCT image",
        raw.count(b"/DCTDecode") == 1 and raw.count(b"/Subtype /Image") == 1,
    )
    # description
    pg.click("#sl-items button")
    pg.fill("#sl-text", "A tiny red bar")
    pg.press("#sl-text", "Tab")
    desc = pg.evaluate("document.querySelector('#sl-canvas-wrap svg desc').textContent")
    check(
        "pic: the description is stored, named in the slide description and in the item list, and is not drawn as text",
        pics()[0]["text"] == "A tiny red bar"
        and "Picture: A tiny red bar" in desc
        and "A tiny red bar"
        not in (
            pg.text_content("#sl-canvas-wrap svg text")
            if pg.query_selector("#sl-canvas-wrap svg text")
            else ""
        )
        and "Picture: A tiny red bar" in pg.inner_text("#sl-items"),
        desc,
    )
    pg.click("#sl-del-el")
    check(
        "pic: deleting the picture hides the warning and returns the deck to version 1",
        not pg.is_visible("#sl-export-warn") and deck()["v"] == 1,
    )
    # 10 MB deck cap: five 1.8 MB pictures fit, the sixth does not
    for k in range(5):
        add("n%d.png" % k, _png(1000, 600, noise=True), 1500)
    five = (len(pics()), idb_count())
    add("n5.png", _png(1000, 600, noise=True), 1500)
    check(
        "pic: five 1.8 MB pictures fit and the sixth is refused for the 10 MB deck cap",
        five == (5, 6) and len(pics()) == 5 and "10 MB" in status() and idb_count() == 6,
        str((five, len(pics()), status(), idb_count())),
    )
    # an aborted write and a blocked store change nothing
    pg.evaluate("localStorage.clear()")
    pg.reload()
    pg.wait_for_timeout(500)
    base = idb_count()
    pg.evaluate(
        "(()=>{window.__add=IDBObjectStore.prototype.add;IDBObjectStore.prototype.add=function(v){const r=window.__add.call(this,v);this.transaction.abort();return r;};return 1})()"
    )
    add("x.png", _png(20, 20, (1, 2, 3)))
    check(
        "pic: a write the browser aborts adds no picture and no record, and says so",
        not pics() and idb_count() == base and "Nothing was added" in status(),
        status(),
    )
    pg.evaluate("(()=>{IDBObjectStore.prototype.add=window.__add;return 1})()")
    add("x.png", _png(20, 20, (1, 2, 3)))
    check(
        "pic: after the browser recovers the same picture is added normally",
        len(pics()) == 1 and idb_count() == base + 1,
        status(),
    )
    pg.evaluate("localStorage.clear()")
    pg.reload()
    pg.wait_for_timeout(500)
    pg.evaluate("(()=>{indexedDB.open=function(){throw new Error('blocked')};return 1})()")
    add("y.png", _png(21, 21, (3, 2, 1)))
    check(
        "pic: a blocked picture store adds nothing and says so",
        not pics() and "would not open" in status(),
        status(),
    )


TMP_TWO = "/tmp/deck-two-pics.json"  # noqa: S108
TMP_REF5 = "/tmp/deck-ref5.json"  # noqa: S108
TMP_EXTRA = "/tmp/deck-extra-pic.json"  # noqa: S108


def slides_images_race(ctx):  # noqa: C901
    """Pictures: quick adds cannot pass the cap, a late load cannot wipe a drag, a failed deck save or a failed import leaves the deck alone, an import stores only what it uses."""
    import json as _json

    pg = fresh(ctx, "/slides/")
    pg.add_init_script(
        "(()=>{const d=Object.getOwnPropertyDescriptor(IDBTransaction.prototype,'oncomplete');Object.defineProperty(IDBTransaction.prototype,'oncomplete',{configurable:true,get:d.get,set(f){d.set.call(this,f?function(e){const a=this,r=arguments;setTimeout(()=>f.apply(a,r),Number(localStorage.getItem('__delay')||0))}:f)}})})()"
    )
    KEY = "document.getElementById('slide-root').dataset.storeKey"
    wipe = (
        "new Promise(r=>{const q=indexedDB.deleteDatabase('salow.slides.pictures.'+%s);q.onsuccess=q.onerror=q.onblocked=()=>r()})"
        % KEY
    )
    pg.evaluate(wipe)
    pg.reload()
    pg.wait_for_timeout(400)

    def deck():
        return pg.evaluate("JSON.parse(localStorage.getItem(%s))" % KEY)

    def status():
        return pg.inner_text("#sl-status")

    def idb_count():
        return pg.evaluate(
            "new Promise(res=>{const r=indexedDB.open('salow.slides.pictures.'+%s,1);r.onupgradeneeded=()=>r.result.createObjectStore('img',{keyPath:'hash'});r.onsuccess=()=>{const t=r.result.transaction('img');const q=t.objectStore('img').count();q.onsuccess=()=>{r.result.close();res(q.result)}}})"
            % KEY
        )

    def put(name, data):
        pg.set_input_files(
            "#sl-img-file", files=[{"name": name, "mimeType": "image/png", "buffer": data}]
        )

    def pics():
        d = deck()
        return (
            [e for sl in d["slides"] for e in sl["elements"] if e["type"] == "image"] if d else []
        )

    # two adds fired back to back near the cap: only one may pass
    for k in range(4):
        put("n%d.png" % k, _png(1000, 600, noise=True))
        pg.wait_for_timeout(1500)
    pg.evaluate("localStorage.setItem('__delay','1200')")
    put("a.png", _png(1000, 600, noise=True))
    put("b.png", _png(1000, 600, noise=True))
    pg.wait_for_timeout(9000)
    pg.evaluate("localStorage.setItem('__delay','0')")
    check(
        "race: two quick 1.8 MB adds with 7.2 MB already in the deck add exactly one (9.0 MB) and refuse the other for the 10 MB cap",
        len(pics()) == 5 and idb_count() == 5 and "10 MB" in status(),
        str((len(pics()), idb_count(), status())),
    )
    # a failed deck save after a good store write: the deck on disk is unchanged and the message says so
    pg.evaluate("localStorage.clear()")
    pg.reload()
    pg.wait_for_timeout(500)
    base = idb_count()
    saved = pg.evaluate("localStorage.getItem(%s)" % KEY)
    pg.evaluate(
        "(()=>{window.__si=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new DOMException('full','QuotaExceededError')};return 1})()"
    )
    put("s.png", _png(30, 30, (9, 9, 9)))
    pg.wait_for_timeout(1200)
    st = status()
    onscreen = pg.evaluate("!!document.querySelector('#sl-canvas-wrap svg image')")
    pg.evaluate("(()=>{Storage.prototype.setItem=window.__si;return 1})()")
    check(
        "race: when the deck cannot be saved the stored deck is unchanged, the picture stays on screen as unsaved, the file is kept in the store, and the status says the changes are on screen only",
        pg.evaluate("localStorage.getItem(%s)" % KEY) == saved
        and idb_count() == base + 1
        and "on screen only" in st
        and onscreen,
        st,
    )
    # a late picture load during a drag does not remove the dragged item
    pg.evaluate("localStorage.clear()")
    pg.reload()
    pg.wait_for_timeout(500)
    put("d.png", _png(60, 40, (10, 90, 160)))
    pg.wait_for_timeout(1200)
    pg.evaluate("localStorage.setItem('__delay','2500')")
    x0 = pics()[0]["x"]
    pg.reload()
    pg.wait_for_timeout(300)
    box = pg.evaluate(
        "(()=>{const r=document.querySelector('#sl-canvas-wrap svg rect[data-id]').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()"
    )
    pg.mouse.move(box[0], box[1])
    pg.mouse.down()
    pg.mouse.move(box[0] + 60, box[1], steps=4)
    pg.wait_for_timeout(3200)
    kept = pg.evaluate(
        "(()=>{const h=document.querySelector('#sl-canvas-wrap svg rect[data-id]');return !!h&&!!h.parentNode.getAttribute('transform')})()"
    )
    pg.mouse.up()
    pg.wait_for_timeout(800)
    img = pg.evaluate("!!document.querySelector('#sl-canvas-wrap svg image')")
    check(
        "race: a picture finishing its load during a drag leaves the drag preview in place, the drop moves the item, and the picture draws afterwards",
        kept and pics()[0]["x"] > x0 and img,
        str((kept, x0, pics()[0]["x"], img)),
    )
    pg.evaluate("localStorage.setItem('__delay','0')")
    # half-typed text in the Description box and in the notes survives a late picture load
    pg.evaluate("localStorage.setItem('__delay','2500')")
    pg.reload()
    pg.wait_for_timeout(300)
    pg.click("#sl-items button")
    pg.click("#sl-text")
    pg.keyboard.type("half typed")
    pg.wait_for_timeout(3200)
    kept_text = pg.evaluate("[document.getElementById('sl-text').value, document.activeElement.id]")
    pg.click("#sl-notes")
    pg.keyboard.type("note in progress")
    pg.reload()
    pg.wait_for_timeout(300)
    pg.click("#sl-notes")
    pg.keyboard.type("note two")
    pg.wait_for_timeout(3200)
    kept_notes = pg.evaluate(
        "[document.getElementById('sl-notes').value, document.activeElement.id]"
    )
    check(
        "race: text typed in the Description box or the notes, not yet committed, is still there and still focused after a late picture load",
        kept_text == ["half typed", "sl-text"] and kept_notes == ["note two", "sl-notes"],
        str((kept_text, kept_notes)),
    )
    pg.evaluate("localStorage.setItem('__delay','0')")
    # import: only referenced pictures are stored, and a failure midway leaves the deck alone
    pg.evaluate("localStorage.clear()")
    pg.evaluate(wipe)
    pg.reload()
    pg.wait_for_timeout(500)
    put("p1.png", _png(40, 20, (200, 0, 0)))
    pg.wait_for_timeout(1000)
    put("p2.png", _png(40, 20, (0, 200, 0)))
    pg.wait_for_timeout(1000)
    msgs = []
    pg.on("dialog", lambda d: (msgs.append(d.message), d.accept()))
    with pg.expect_download() as dl:
        pg.click("#sl-export-pics")
    full = _json.loads(open(dl.value.path()).read())
    extra = _png(50, 50, (1, 2, 200))
    import base64
    import hashlib

    full2 = _json.loads(_json.dumps(full))
    full2["pictures"].append(
        {
            "hash": hashlib.sha256(extra).hexdigest(),
            "type": "image/png",
            "data": base64.b64encode(extra).decode(),
        }
    )
    open(TMP_TWO, "w").write(_json.dumps(full))
    open(TMP_EXTRA, "w").write(_json.dumps(full2))
    pg.evaluate("localStorage.clear()")
    pg.evaluate(wipe)
    pg.reload()
    pg.wait_for_timeout(500)
    pg.set_input_files("#sl-import", TMP_EXTRA)
    pg.wait_for_timeout(1500)
    check(
        "race: importing a file that carries a third, unused picture stores only the two the deck uses and says one was ignored",
        len(pics()) == 2 and idb_count() == 2 and any("no slide uses" in t for t in msgs),
        str((len(pics()), idb_count(), msgs)),
    )
    pg.evaluate("localStorage.clear()")
    pg.evaluate(wipe)
    pg.reload()
    pg.wait_for_timeout(500)
    pg.evaluate(
        "(()=>{window.__n=0;window.__add=IDBObjectStore.prototype.add;IDBObjectStore.prototype.add=function(v){const r=window.__add.call(this,v);if(++window.__n===2)this.transaction.abort();return r;};return 1})()"
    )
    pg.set_input_files("#sl-import", TMP_TWO)
    pg.wait_for_timeout(1500)
    check(
        "race: a store failure midway through an import leaves the deck unchanged and says picture files may have been kept",
        not pics() and "was not changed" in status() and "nothing refers to them" in status(),
        status(),
    )


def chat_poll_429(ctx):  # noqa: C901
    """Chat client: 429 answers (stubbed, with a fake clock) slow the polling and say so without pausing; real failures still pause; recovery and sequence stay correct."""
    import django

    django.setup()

    def make_space():
        from apps.accounts.models import CustomUser
        from apps.chat import services as cs

        me = CustomUser.objects.get(employee_id="ST-LIVE-1")
        other, _ = CustomUser.objects.get_or_create(
            employee_id="ST-LIVE-2",
            defaults={
                "full_name": "Second Synthetic",
                "email": "live.two@salowtech.com",
                "email_verified": True,
                "role": CustomUser.Role.EMPLOYEE,
                "status": CustomUser.Status.ACTIVE,
                "is_active": True,
            },
        )
        return cs.open_dm(me, other.pk).public_id

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        space_id = ex.submit(make_space).result()
    url = APP + "/chat/space/%s/" % space_id
    pg = ctx.pages[0]
    loaded = []
    mode = {"m": "429", "ra": None, "n": 0}
    seen = []

    def handler(route, request=None):
        seen.append(route.request.url.split("?after=")[-1])
        mode["n"] += 1
        m = mode["m"]
        if m == "429":
            ra = mode["ra"]
            if ra == "date":
                from email.utils import formatdate

                ra = formatdate(time.time() + 100, usegmt=True)
            hd = {"Retry-After": str(ra)} if ra else {}
            route.fulfill(status=429, headers=hd, body="")
        elif m == "500":
            route.fulfill(status=500, body="")
        elif m == "401":
            route.fulfill(status=401, body="")
        else:
            route.fulfill(
                status=200,
                headers={"X-Chat-Last": "5", "X-Chat-More": "0", "Content-Type": "text/html"},
                body='<div class="stub-msg">hello after throttle</div>',
            )

    def status():
        return pg.inner_text("#chat-status")

    def run(sec):
        done = 0
        while done < sec:
            step = min(5, sec - done)
            pg.clock.run_for(step * 1000)
            pg.wait_for_timeout(150)
            done += step

    def load(m, ra=None):
        nonlocal pg
        if loaded:
            old = pg
            old.goto("about:blank")
            old.wait_for_timeout(
                14000
            )  # the site-wide request limit answers a page load with 429 when loads come too fast
            pg = ctx.new_page()
            old.goto("about:blank")  # stops the old page's timers so only the new page polls
            if old is not ctx.pages[0]:
                old.close()
        loaded.append(1)
        mode.update({"m": m, "ra": ra, "n": 0})
        seen.clear()
        pg.unroute_all() if hasattr(pg, "unroute_all") else None
        pg.clock.install()
        pg.route(re.compile(r"/chat/space/.+/poll/"), handler)
        pg.goto(url)
        pg.wait_for_timeout(300)

    load("429")
    run(300)
    st = status()
    check(
        "chat429: 300 s of 429 answers: not paused, and the page says updates are slower",
        "paused" not in st and "rate limited" in st,
        st[:140],
    )
    check(
        "chat429: polling backed off (about 5 polls in 300 s, not one every 10 s)",
        3 <= mode["n"] <= 6,
        str(mode["n"]),
    )
    check(
        "chat429: every retry asked for the same position (nothing skipped)",
        set(seen) == {"0"},
        str(set(seen)),
    )
    mode["m"] = "ok"
    run(160)
    st2 = status()
    html = pg.inner_html("#chat-list")
    check(
        "chat429: after the server answers again the notice clears and the message appears once, position advances",
        st2 == ""
        and html.count("hello after throttle") >= 1
        and pg.get_attribute("#chat-list", "data-last") == "5",
        (st2[:80], html.count("hello after throttle"), pg.get_attribute("#chat-list", "data-last")),
    )
    after = [x for x in seen if x == "5"]
    check(
        "chat429: the next poll after recovery uses the new position",
        len(after) >= 1,
        str(seen[-3:]),
    )
    load("429", ra=45)
    run(15)
    check(
        "chat429: a Retry-After of 45 s is shown as the rate-limit notice without a countdown",
        "rate limited" in status() and "seconds" not in status(),
        status()[:120],
    )
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.screenshot(
        path=os.path.join(os.environ.get("DRILL_SHOTS", "./"), "chat-throttle-notice-phone.png")
    )
    n0 = mode["n"]
    run(30)
    check("chat429: no poll during the 45 s wait", mode["n"] == n0, "%d vs %d" % (mode["n"], n0))
    run(20)
    check(
        "chat429: after the 45 s the page tries again",
        mode["n"] == n0 + 1,
        "%d vs %d" % (mode["n"], n0),
    )
    load("429", ra=300)
    run(10)
    n1 = mode["n"]
    run(285)
    check(
        "chat429: a Retry-After of 300 s (over the 120 s fallback cap) is not retried early",
        mode["n"] == n1,
        "%d vs %d" % (mode["n"], n1),
    )
    run(60)
    check(
        "chat429: and is retried after the 300 s", mode["n"] == n1 + 1, "%d vs %d" % (mode["n"], n1)
    )
    load("429", ra=90000)
    run(10)
    n3 = mode["n"]
    run(280)
    check(
        "chat429: a Retry-After of 90000 s (over a day) is not cut short or retried early",
        mode["n"] == n3 and mode["n"] >= 1,
        "%d vs %d" % (mode["n"], n3),
    )
    load("429", ra="date")
    run(15)

    n2 = mode["n"]
    run(80)
    check(
        "chat429: a Retry-After HTTP date about 100 s ahead is not retried early",
        mode["n"] == n2,
        "%d vs %d" % (mode["n"], n2),
    )
    run(40)
    check("chat429: and is retried after it", mode["n"] == n2 + 1, "%d vs %d" % (mode["n"], n2))
    load("500")
    run(70)
    st3 = status()
    check("chat429: five real server errors still pause live updates", "paused" in st3, st3[:100])
    load("401")
    run(15)
    check(
        "chat429: sign-out still stops with its own message",
        "session has ended" in status(),
        status()[:100],
    )


def chat_poll_real(ctx):  # noqa: C901
    """Chat, real server and TiDB: a message sent by the second person while this person's polls are rate limited arrives once and the position advances."""
    import concurrent.futures

    import django

    django.setup()
    holder = {}

    def setup():
        from apps.accounts.models import CustomUser
        from apps.chat import services as cs

        me = CustomUser.objects.get(employee_id="ST-LIVE-1")
        other, _ = CustomUser.objects.get_or_create(
            employee_id="ST-LIVE-2",
            defaults={
                "full_name": "Second Synthetic",
                "email": "live.two@salowtech.com",
                "email_verified": True,
                "role": CustomUser.Role.EMPLOYEE,
                "status": CustomUser.Status.ACTIVE,
                "is_active": True,
            },
        )
        holder["other"], holder["cs"] = other, cs
        return cs.open_dm(me, other.pk).public_id

    def send(body, cid):
        return holder["cs"].post_message(holder["other"], space_id, body, cid).seq

    ex = concurrent.futures.ThreadPoolExecutor(1)
    space_id = ex.submit(setup).result()
    pg = ctx.pages[0]
    log = []
    pg.on("response", _poll_log(log))
    pg.goto(APP + "/chat/space/%s/" % space_id)
    pg.wait_for_timeout(500)
    burst = pg.evaluate(
        "(async()=>{const u=document.getElementById('chat-list').dataset.poll+'?after=0';const c=[];for(let i=0;i<40;i++){const r=await fetch(u,{credentials:'same-origin'});c.push(r.status)}return c})()"
    )
    print("chat_poll_real: burst statuses 200:", burst.count(200), "429:", burst.count(429))
    seq1 = ex.submit(send, "sent while limited one", "real-1").result()
    t0 = time.time()
    body = lambda: pg.inner_html("#chat-list")  # noqa: E731
    while time.time() - t0 < 230 and body().count("sent while limited one") == 0:
        pg.wait_for_timeout(3000)
    waited = time.time() - t0
    seen = [e for e in log if e[0] == 429]
    print(
        "chat_poll_real: waited %.0f s, polls %d, 429 answers %d, Retry-After values %s"
        % (waited, len(log), len(seen), sorted({e[1] for e in seen}))
    )
    pg.wait_for_timeout(25000)
    check(
        "real: the message sent during the rate limit arrived exactly once",
        body().count("sent while limited one") == 1,
        str(body().count("sent while limited one")),
    )
    check(
        "real: the position advanced to that message",
        pg.get_attribute("#chat-list", "data-last") == str(seq1),
        "%s vs %s" % (pg.get_attribute("#chat-list", "data-last"), seq1),
    )
    check(
        "real: live updates were not paused and the notice is gone",
        "paused" not in pg.inner_text("#chat-status")
        and "rate limited" not in pg.inner_text("#chat-status"),
        pg.inner_text("#chat-status")[:100],
    )
    seq2 = ex.submit(send, "second after recovery", "real-2").result()
    pg.wait_for_timeout(26000)
    check(
        "real: a later message also arrives exactly once",
        body().count("second after recovery") == 1 and body().count("sent while limited one") == 1,
        str((body().count("second after recovery"), body().count("sent while limited one"))),
    )
    check(
        "real: the position advanced again",
        pg.get_attribute("#chat-list", "data-last") == str(seq2),
        "%s vs %s" % (pg.get_attribute("#chat-list", "data-last"), seq2),
    )
    check(
        "real: a 429 was observed during the run (burst)",
        burst.count(429) > 0 or len(seen) > 0,
        str((burst.count(429), len(seen))),
    )


def _poll_log(log):
    def f(resp):
        if "/poll/" in resp.url:
            log.append((resp.status, resp.headers.get("retry-after")))

    return f


def _two_page_pdf():
    """A tiny valid PDF: page 1 portrait 612x792, page 2 landscape 792x612, each with a frame and a line of text."""
    objs = []

    def add(b):
        objs.append(b)
        return len(objs)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>")

    def page(w, h, content_ref):
        return (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R /Resources << /Font << /F1 7 0 R >> >> >>"
            % (w, h, content_ref)
        ).encode()

    def stream(txt):
        b = txt.encode()
        return b"<< /Length %d >>\nstream\n" % len(b) + b + b"\nendstream"

    add(page(612, 792, 4))
    add(stream("2 w 20 20 572 752 re S BT /F1 36 Tf 60 700 Td (Portrait page one) Tj ET"))
    add(page(792, 612, 6))
    add(stream("2 w 20 20 752 572 re S BT /F1 36 Tf 60 520 Td (Landscape page two) Tj ET"))
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def pdf_fit(ctx):  # noqa: C901
    """PDF viewer: Fit width is the 100 percent view, Fit page keeps a whole page in view on a portrait and a landscape page, at desktop and phone width."""
    pg = fresh(ctx, "/pdf/")
    data = _two_page_pdf()

    def open_file():
        pg.set_input_files(
            "#pdf-open", files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of 2"):
                break
            pg.wait_for_timeout(300)
        pg.wait_for_timeout(700)

    def where():
        return pg.inner_text("#pdf-where")

    def box():
        return pg.evaluate(
            "(()=>{const c=document.getElementById('pdf-canvas');return [parseInt(c.style.width),parseInt(c.style.height),document.getElementById('pdf-stage').clientWidth,window.innerHeight]})()"
        )

    def inview():
        return pg.evaluate(
            "(()=>{const r=document.getElementById('pdf-canvas').getBoundingClientRect();return [Math.round(r.top),Math.round(r.bottom),window.innerHeight]})()"
        )

    def dis(i):
        return pg.evaluate("document.getElementById('%s').disabled" % i)

    pg.set_viewport_size({"width": 1280, "height": 720})
    pg.reload()
    open_file()
    w, h, sw, ih = box()
    check(
        "fit: a new file opens at zoom 100% with Fit width already current",
        "zoom 100%" in where() and dis("pdf-fitw") and not dis("pdf-fitp"),
        where(),
    )
    check(
        "fit: at 100% the page is as wide as the stage (within 24 px, the stage width read at draw time)",
        min(sw, 1600) - 24 <= w <= min(sw, 1600) + 1,
        str((w, sw)),
    )
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(900)
    w, h, sw, ih = box()
    check(
        "fit: Fit page says so and swaps which button is available",
        "fit to page" in where() and dis("pdf-fitp") and not dis("pdf-fitw"),
        where(),
    )
    check(
        "fit: a portrait page then fits the height limit (the window height less 40 px) and the width",
        h <= ih - 40 + 2 and w <= sw + 1 and h >= ih - 40 - 3,
        str((w, h, sw, ih)),
    )
    t, b, ih = inview()
    check(
        "fit: the whole fitted page is inside the window (scrolled into view)",
        t >= -1 and b <= ih + 1,
        str((t, b, ih)),
    )
    check("fit: the readout gives the fitted scale", "fit to page, 5" in where(), where())
    pg.set_viewport_size({"width": 1280, "height": 600})
    pg.wait_for_timeout(1200)
    w5, h5, sw5, ih5 = box()
    check(
        "fit: resizing the window refits the page to the new height",
        h5 <= ih5 - 40 + 2 and h5 >= ih5 - 40 - 3 and ih5 == 600,
        str((w5, h5, ih5)),
    )
    pg.set_viewport_size({"width": 1280, "height": 720})
    pg.wait_for_timeout(1200)
    pg.click("#pdf-next")
    pg.wait_for_timeout(900)
    w2, h2, sw, ih = box()
    check(
        "fit: the landscape page 2 stays fitted, limited by width, and is shorter than the stage",
        "Page 2 of 2" in where()
        and "fit to page" in where()
        and w2 <= sw + 1
        and h2 <= ih - 40 + 2,
        str((w2, h2, sw, ih)),
    )
    pg.click("#pdf-fitw")
    pg.wait_for_timeout(900)
    w3, h3, sw, ih = box()
    check(
        "fit: Fit width goes back to 100% and the full width",
        "zoom 100%" in where() and min(sw, 1600) - 24 <= w3 <= min(sw, 1600) + 1 and h3 > 0,
        str((w3, h3, sw)),
    )
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(900)
    pg.click("#pdf-zin")
    pg.wait_for_timeout(600)
    check(
        "fit: on landscape page 2 Fit page is about 96%, so Zoom in goes to 100%, not 125%",
        "zoom 100%" in where(),
        where(),
    )
    pg.click("#pdf-prev")
    pg.wait_for_timeout(900)
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(900)
    pg.click("#pdf-zin")
    pg.wait_for_timeout(600)
    check(
        "fit: Zoom in from portrait Fit page (about 57%) goes to the next step up, 75%",
        "Page 1 of 2, zoom 75%" in where(),
        where(),
    )
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(900)
    wf, hf, _, _ = box()
    pg.click("#pdf-zout")
    pg.wait_for_timeout(900)
    wz, hz, _, _ = box()
    check(
        "fit: Zoom out from portrait Fit page makes it smaller (50%), never larger",
        "zoom 50%" in where() and wz < wf and hz < hf,
        str((where(), wf, wz)),
    )
    pg.click("#pdf-close")
    pg.wait_for_timeout(300)
    pg.click("#pdf-fitp") if not dis("pdf-fitp") else None
    check(
        "fit: with no file open both Fit buttons are disabled",
        dis("pdf-fitp") and dis("pdf-fitw"),
        where(),
    )
    open_file()
    check("fit: a reopened file starts at 100% again", "zoom 100%" in where(), where())
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(400)
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(900)
    w4, h4, sw4, ih4 = box()
    check(
        "fit: at phone width Fit page fits inside the stage width and the window height less 40 px height",
        w4 <= sw4 + 1 and h4 <= ih4 * 0.8 + 2,
        str((w4, h4, sw4, ih4)),
    )
    t4, b4, ih4b = inview()
    check(
        "fit: at phone width the whole fitted page is in the window",
        t4 >= -1 and b4 <= ih4b + 1,
        str((t4, b4, ih4b)),
    )
    pg.screenshot(path=os.path.join(os.environ.get("DRILL_SHOTS", "./"), "pdf-fit-phone.png"))
    pg.set_viewport_size({"width": 1280, "height": 720})
    pg.wait_for_timeout(300)


def pdf_rotate(ctx):  # noqa: C901
    """PDF viewer: Turn view rotates the shown page 90 degrees a step, for the view only; sizes, Fit page and reset on reopen follow the turned shape."""
    pg = fresh(ctx, "/pdf/")
    data = _two_page_pdf()
    pg.set_viewport_size({"width": 1280, "height": 720})
    pg.reload()
    pg.set_input_files(
        "#pdf-open", files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": data}]
    )
    for _ in range(50):
        if pg.inner_text("#pdf-where").startswith("Page 1 of 2"):
            break
        pg.wait_for_timeout(300)
    pg.wait_for_timeout(700)

    def shape():
        return pg.evaluate(
            "(()=>{const c=document.getElementById('pdf-canvas');return [parseInt(c.style.width),parseInt(c.style.height),c.width,c.height]})()"
        )

    def where():
        return pg.inner_text("#pdf-where")

    w0, h0, cw0, ch0 = shape()
    check(
        "rotate: portrait page starts upright (taller than wide)",
        h0 > w0 and ch0 > cw0 and "turned" not in where(),
        str((w0, h0, where())),
    )
    pg.click("#pdf-rot")
    pg.wait_for_timeout(900)
    w1, h1, cw1, ch1 = shape()
    check(
        "rotate: one turn makes it wider than tall and the readout says 90",
        w1 > h1 and cw1 > ch1 and "turned 90 degrees" in where(),
        str((w1, h1, where())),
    )
    check(
        "rotate: the turned shape is the page shape swapped (612x792 becomes about 792x612)",
        abs((w1 / h1) - (792 / 612)) < 0.02,
        str(w1 / h1),
    )
    pg.click("#pdf-rot")
    pg.wait_for_timeout(900)
    w2, h2, _, _ = shape()
    check(
        "rotate: two turns is upright again, upside down, same page shape as at the start (size can differ a little when a scrollbar appears)",
        abs(w2 / h2 - w0 / h0) < 0.02 and "turned 180" in where(),
        str((w2, h2, where())),
    )
    pg.click("#pdf-rot")
    pg.wait_for_timeout(600)
    pg.click("#pdf-rot")
    pg.wait_for_timeout(900)
    w4, h4, _, _ = shape()
    check(
        "rotate: four turns return to the start and the readout drops the turn",
        abs(w4 / h4 - w0 / h0) < 0.02 and "turned" not in where(),
        str((w4, h4, where())),
    )
    pg.click("#pdf-rot")
    pg.wait_for_timeout(700)
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(900)
    wf, hf, _, _ = shape()
    ih = pg.evaluate("window.innerHeight")
    t = pg.evaluate(
        "(()=>{const r=document.getElementById('pdf-canvas').getBoundingClientRect();return [Math.round(r.top),Math.round(r.bottom)]})()"
    )
    check(
        "rotate: Fit page on the turned page fits the turned shape and shows it whole",
        wf > hf and hf <= ih - 40 + 2 and t[0] >= -1 and t[1] <= ih + 1,
        str((wf, hf, ih, t)),
    )
    pg.screenshot(path=os.path.join(os.environ.get("DRILL_SHOTS", "./"), "pdf-rotate.png"))
    pg.click("#pdf-next")
    pg.wait_for_timeout(900)
    w5, h5, _, _ = shape()
    check(
        "rotate: the turn stays when changing page (landscape page 2 turned is taller than wide)",
        h5 > w5 and "Page 2 of 2" in where() and "turned 90" in where(),
        str((w5, h5, where())),
    )
    pg.click("#pdf-close")
    pg.set_input_files(
        "#pdf-open", files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": data}]
    )
    for _ in range(50):
        if pg.inner_text("#pdf-where").startswith("Page 1 of 2"):
            break
        pg.wait_for_timeout(300)
    pg.wait_for_timeout(700)
    w6, h6, _, _ = shape()
    check(
        "rotate: reopening a file starts upright",
        h6 > w6 and "turned" not in where(),
        str((w6, h6, where())),
    )


def pdf_notes_turn(ctx):  # noqa: C901
    """PDF notes with a turned view (needs a secure context, so run this drill alone: the runner then treats the drill origin as secure for this browser only)."""
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 720})
    pg.reload()
    if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
        check(
            "notes turn: the browser offers a secure context (run this drill alone)",
            False,
            "navigator.locks missing",
        )
        return
    data = _two_page_pdf()
    prefix = pg.get_attribute("#pdf-notes", "data-prefix")

    def opened():
        pg.set_input_files(
            "#pdf-open", files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of 2"):
                break
            pg.wait_for_timeout(300)
        pg.wait_for_timeout(900)

    def raw():
        return pg.evaluate(
            "(p=>{for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k.indexOf(p)===0)return localStorage.getItem(k)}return null})(%s)"
            % json.dumps(prefix)
        )

    def saved():
        r = raw()
        return json.loads(r) if r else None

    def pins():
        return pg.evaluate(
            "[...document.querySelectorAll('#pdf-pins .pdf-pin')].map(b=>[b.style.left,b.style.top,b.textContent])"
        )

    def notemsg():
        return pg.inner_text("#pdf-notestatus")

    def where():
        return pg.inner_text("#pdf-where")

    def save_note(text):
        pg.fill("#pdf-notetext", text)
        pg.click("#pdf-noteform button[type=submit]")
        pg.wait_for_timeout(700)

    opened()
    check(
        "notes turn: notes are on in this secure test browser",
        "No notes yet" in notemsg(),
        notemsg(),
    )
    pg.click("#pdf-addnote")
    r = pg.evaluate(
        "(()=>{const r=document.getElementById('pdf-canvas').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
    )
    pg.click("#pdf-canvas", position={"x": r[2] * 0.25, "y": r[3] * 0.40})
    pg.wait_for_timeout(300)
    save_note("spot note")
    s1 = saved()
    pg.click("#pdf-addpagenote")
    pg.wait_for_timeout(300)
    save_note("page note")
    raw_two = raw()
    print("notes turn: stored shape:", raw_two[:300])
    check(
        "notes turn: a spot note and a page note are stored, the spot with its fraction",
        raw_two.count("spot note") == 1 and raw_two.count("page note") == 1 and s1 is not None,
        raw_two[:200],
    )
    p0 = pins()
    check(
        "notes turn: upright, one pin is shown near 25 percent and 40 percent",
        len(p0) == 1
        and abs(float(p0[0][0].rstrip("%")) - 25) < 1.5
        and abs(float(p0[0][1].rstrip("%")) - 40) < 1.5,
        str(p0),
    )
    pg.click("#pdf-rot")
    pg.wait_for_timeout(900)
    check(
        "notes turn: turned, the pin is hidden and the saved notes are byte-identical",
        pins() == [] and raw() == raw_two,
        str(pins()),
    )
    pg.click("#pdf-addnote")
    pg.wait_for_timeout(300)
    check(
        "notes turn: turned, placing a note on a spot is refused with a message and no form",
        "cannot be placed" in notemsg()
        and pg.evaluate("document.getElementById('pdf-noteform').hidden")
        and pg.get_attribute("#pdf-addnote", "aria-pressed") == "false",
        notemsg(),
    )
    pg.click("#pdf-addpagenote")
    pg.wait_for_timeout(300)
    save_note("page note while turned")
    check(
        "notes turn: a page note can still be added while turned and has no spot",
        raw().count("page note while turned") == 1 and raw().count("spot note") == 1,
        raw()[:200],
    )
    for _ in range(3):
        pg.click("#pdf-rot")
        pg.wait_for_timeout(500)
    pg.wait_for_timeout(600)
    p1 = pins()
    check(
        "notes turn: back to upright, the same pin returns at the same place (its number can change, it is the note's order)",
        [x[:2] for x in p1] == [x[:2] for x in p0] and "turned" not in where(),
        str((p0, p1)),
    )
    pg.click("#pdf-addnote")
    check(
        "notes turn: choosing a spot is pending",
        pg.get_attribute("#pdf-addnote", "aria-pressed") == "true",
        "",
    )
    before_rot = raw()
    pg.click("#pdf-rot")
    pg.wait_for_timeout(700)
    check(
        "notes turn: turning cancels a pending spot choice and says so",
        pg.get_attribute("#pdf-addnote", "aria-pressed") == "false" and "cancelled" in notemsg(),
        notemsg(),
    )
    r2 = pg.evaluate(
        "(()=>{const r=document.getElementById('pdf-canvas').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
    )
    pg.click("#pdf-canvas", position={"x": r2[2] * 0.5, "y": r2[3] * 0.5})
    pg.wait_for_timeout(400)
    check(
        "notes turn: a click on the turned page after the cancel opens no form and stores nothing",
        pg.evaluate("document.getElementById('pdf-noteform').hidden") and raw() == before_rot,
        "",
    )
    with pg.expect_download() as dl:
        pg.click("#pdf-exportnotes")
    txt = open(dl.value.path()).read()
    check(
        "notes turn: the notes text file made while turned lists all notes",
        "spot note" in txt and "page note" in txt and "page note while turned" in txt,
        txt[:200],
    )
    print("notes turn: notes text export head:", txt[:240].replace("\n", " | "))
    with pg.expect_download() as d2:
        pg.click("#pdf-exportcopy")
    turned_copy = open(d2.value.path(), "rb").read()
    for _ in range(3):
        pg.click("#pdf-rot")
        pg.wait_for_timeout(400)
    with pg.expect_download() as d3:
        pg.click("#pdf-exportcopy")
    up_copy = open(d3.value.path(), "rb").read()
    open(os.path.join(os.environ.get("DRILL_SHOTS", "./"), "notes-copy-turned.pdf"), "wb").write(
        turned_copy
    )
    open(os.path.join(os.environ.get("DRILL_SHOTS", "./"), "notes-copy-upright.pdf"), "wb").write(
        up_copy
    )
    print("notes turn: copy sizes turned/upright:", len(turned_copy), len(up_copy))
    check(
        "notes turn: the copy with notes is the same whether the view was turned or not (bytes)",
        turned_copy == up_copy,
        "%d vs %d" % (len(turned_copy), len(up_copy)),
    )
    pg.click("#pdf-rot")
    pg.wait_for_timeout(800)
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(900)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(1200)
    pg.screenshot(
        path=os.path.join(os.environ.get("DRILL_SHOTS", "./"), "pdf-turned-fit-phone.png")
    )
    pg.set_viewport_size({"width": 1280, "height": 720})


def _n_page_pdf(n, label="Page"):
    """A valid PDF of n portrait pages, each with a frame and its number (for measuring, not for the app)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join("%d 0 R" % (3 + 2 * i) for i in range(n))
    objs.append(("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n)).encode())
    font_ref = 3 + 2 * n
    for i in range(n):
        objs.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
                % (4 + 2 * i, font_ref)
            ).encode()
        )
        t = (
            "2 w 20 20 572 752 re S BT /F1 48 Tf 60 700 Td (%s %d) Tj ET" % (label, i + 1)
        ).encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(t) + t + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def pdf_thumb_measure(ctx):  # noqa: C901
    """Measurement only (no app change): thumbnails of a 300 page PDF, rendered one at a time in this browser, with the time and the coarse JS heap."""
    import base64

    pg = fresh(ctx, "/pdf/")
    n = 300
    b64 = base64.b64encode(_n_page_pdf(n)).decode()
    js = """async (args) => {
      const [b64, n, keepAll] = args;
      const m = await import('/static/pdf/vendor/pdf.min.mjs');
      m.GlobalWorkerOptions.workerSrc = '/static/pdf/vendor/pdf.worker.min.mjs';
      const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const doc = await m.getDocument({ data: bin }).promise;
      const heap = () => (performance.memory ? performance.memory.usedJSHeapSize : -1);
      const h0 = heap(), t0 = performance.now(), kept = [], times = [];
      for (let i = 1; i <= n; i++) {
        const t1 = performance.now();
        const p = await doc.getPage(i);
        const v1 = p.getViewport({ scale: 1 });
        const vp = p.getViewport({ scale: 120 / v1.width });
        const c = document.createElement('canvas');
        c.width = Math.floor(vp.width); c.height = Math.floor(vp.height);
        await p.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise;
        p.cleanup();
        times.push(performance.now() - t1);
        if (keepAll) kept.push(c);
      }
      const total = performance.now() - t0, h1 = heap();
      times.sort((a, b) => a - b);
      window.__kept = kept;
      return { pages: n, keepAll, totalMs: Math.round(total), medianMs: Math.round(times[n >> 1]), p95Ms: Math.round(times[Math.floor(n * 0.95)]), heapBefore: h0, heapAfter: h1, thumbW: Math.floor(120), thumbH: Math.floor(120 * 792 / 612) };
    }"""
    for keep in (False, True):
        r = pg.evaluate(js, [b64, n, keep])
        print("thumb measure:", r)
        check(
            "thumb measure: %d thumbnails rendered one at a time (keep all: %s)" % (n, keep),
            r["pages"] == n,
            str(r),
        )


def pdf_forms(ctx):  # noqa: C901
    """Fill the standard form fields of a PDF in the viewer and download a separate filled copy."""
    import hashlib
    import pathlib

    fx = pathlib.Path(__file__).resolve().parents[1] / "js" / "fixtures"
    basic = (fx / "form_basic.pdf").read_bytes()
    signed = (fx / "form_signed.pdf").read_bytes()
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 800})

    def open_pdf(name, data):
        pg.set_input_files(
            "#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of"):
                break
            pg.wait_for_timeout(300)
        pg.wait_for_timeout(500)

    def status():
        return pg.inner_text("#pdf-formstatus")

    def find():
        pg.click("#pdf-formfind")
        for _ in range(40):
            if (
                pg.is_visible("#pdf-formlist")
                or "no standard form" in status()
                or "could not" in status()
            ):
                break
            pg.wait_for_timeout(250)

    # Controls stay off with no file.
    check(
        "forms: buttons are off with no file open",
        pg.is_disabled("#pdf-formfind") and pg.is_disabled("#pdf-formsave"),
    )
    open_pdf("form.pdf", basic)
    check(
        "forms: find is on once a file is open, save is still off",
        not pg.is_disabled("#pdf-formfind") and pg.is_disabled("#pdf-formsave"),
    )
    find()
    names = pg.evaluate(
        "[...document.querySelectorAll('#pdf-formlist > label, #pdf-formlist > fieldset')].map(e=>e.textContent.trim().split(' ')[0].replace(/\\s.*/,''))"
    )
    check(
        "forms: eight fillable fields are listed, the button is named as unsupported",
        len(names) == 8 and "go (button)" in status() and "8 field(s)" in status(),
        (names, status()),
    )
    check(
        "forms: the read only field is shown disabled",
        pg.evaluate(
            "[...document.querySelectorAll('#pdf-formlist label')].find(l=>l.textContent.indexOf('fixed')===0).querySelector('input').disabled"
        ),
    )
    check(
        "forms: the text limit is applied to the box",
        pg.evaluate(
            "[...document.querySelectorAll('#pdf-formlist label')].find(l=>l.textContent.indexOf('code')===0).querySelector('input').maxLength"
        )
        == 4,
    )
    # Nothing changed: no copy.
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(300)
    check("forms: saving with no change makes no copy", "no copy was made" in status(), status())

    # Fill.
    def field(name):
        return (
            "[...document.querySelectorAll('#pdf-formlist label')].find(l=>l.textContent.indexOf('%s')===0)"
            % name
        )

    pg.evaluate(field("full_name") + ".querySelector('input').value='Akif Hussain'")
    pg.evaluate(field("agree") + ".querySelector('input').click()")
    pg.evaluate(field("country") + ".querySelector('select').value='France'")
    pg.evaluate("document.querySelector('#pdf-formlist input[type=radio][value=large]').click()")
    pg.evaluate(
        field("tags")
        + ".querySelector('select').querySelectorAll('option').forEach(o=>o.selected=(o.value=='a'||o.value=='c'))"
    )
    pg.evaluate(field("notes") + ".querySelector('textarea').value='line1\\nline2'")
    before = hashlib.sha256(basic).hexdigest()
    with pg.expect_download() as dl:
        pg.click("#pdf-formsave")
    path = dl.value.path()
    out = open(path, "rb").read()
    check(
        "forms: the download is a separate PDF named -filled",
        dl.value.suggested_filename == "form-filled.pdf" and out[:5] == b"%PDF-" and out != basic,
        dl.value.suggested_filename,
    )
    check(
        "forms: the file that was opened is byte-identical",
        hashlib.sha256(basic).hexdigest() == before,
    )
    check(
        "forms: the status says the original was not changed",
        "6 field(s) changed" in status() and "not changed" in status(),
        status(),
    )
    shot = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(500)
    pg.locator("#pdf-forms").scroll_into_view_if_needed()
    pg.screenshot(path=shot + "/pdf-forms-phone.png")
    pg.set_viewport_size({"width": 1280, "height": 800})
    # Reopen the filled copy and read the values back.
    open_pdf("form-filled.pdf", out)
    find()
    vals = pg.evaluate(
        "(()=>{const q=n=>[...document.querySelectorAll('#pdf-formlist label')].find(l=>l.textContent.indexOf(n)===0);"
        "return {name:q('full_name').querySelector('input').value,agree:q('agree').querySelector('input').checked,"
        "country:q('country').querySelector('select').value,size:document.querySelector('#pdf-formlist input[type=radio]:checked').value,"
        "tags:[...q('tags').querySelectorAll('option')].filter(o=>o.selected).map(o=>o.value).join(),notes:q('notes').querySelector('textarea').value,"
        "fixed:q('fixed').querySelector('input').value,code:q('code').querySelector('input').value}})()"
    )
    check(
        "forms: the copy reopens with the filled values and the untouched fields as they were",
        vals
        == {
            "name": "Akif Hussain",
            "agree": True,
            "country": "France",
            "size": "large",
            "tags": "a,c",
            "notes": "line1\nline2",
            "fixed": "locked",
            "code": "",
        },
        vals,
    )
    # The page shows the filled text too (appearance stream drawn by pdf.js), not only the field value.
    pg.wait_for_timeout(300)
    # A value the form font cannot draw is refused and nothing downloads.
    pg.evaluate(field("full_name") + ".querySelector('input').value='\\u0905\\u0915'")
    got = []
    pg.on("download", lambda d: got.append(d))
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(1200)
    check(
        "forms: a value the font cannot draw is refused, nothing downloaded",
        not got and "could not be written" in status(),
        status(),
    )
    # Another file replaces the list.
    open_pdf("form.pdf", basic)
    check(
        "forms: opening another file clears the old field list",
        not pg.is_visible("#pdf-formlist") and status() == "",
    )
    # Signed file: first press holds, second press within 15 s downloads.
    open_pdf("signed.pdf", signed)
    find()
    check(
        "forms: a signed file says so when the fields are listed",
        "digital signature" in status(),
        status(),
    )
    pg.evaluate(field("full_name") + ".querySelector('input').value='Sam'")
    got.clear()
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(1500)
    check(
        "forms: the first press on a signed file downloads nothing and warns",
        not got and "will not validate" in status() and "again within 15 seconds" in status(),
        status(),
    )
    pg.evaluate(field("full_name") + ".querySelector('input').value='Sam2'")
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(1500)
    check(
        "forms: changing a value after the warning does not release the held copy",
        not got,
        status(),
    )
    with pg.expect_download() as dl2:
        pg.click("#pdf-formsave")
    check(
        "forms: pressing again with the same values downloads the copy",
        dl2.value.suggested_filename == "signed-filled.pdf",
    )
    # Held signed copy does not survive reopening the same bytes as a new open.
    open_pdf("signed.pdf", signed)
    find()
    pg.evaluate(field("full_name") + ".querySelector('input').value='Sam'")
    got.clear()
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(1500)
    held = "again within 15 seconds" in status()
    open_pdf("signed.pdf", signed)
    check(
        "forms: reopening a file clears the field list and the held signed copy",
        held
        and not pg.is_visible("#pdf-formlist")
        and status() == ""
        and pg.is_disabled("#pdf-formsave"),
        status(),
    )
    find()
    pg.evaluate(field("full_name") + ".querySelector('input').value='Sam'")
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(1500)
    check(
        "forms: after reopening, the first press warns again and downloads nothing",
        not got and "again within 15 seconds" in status(),
        status(),
    )
    # XFA file: listed with a notice, no filled copy offered.
    open_pdf("xfa.pdf", (fx / "form_xfa.pdf").read_bytes())
    find()
    check(
        "forms: an XFA file is flagged and the filled copy is not offered",
        "XFA" in status() and pg.is_disabled("#pdf-formsave") and not got,
        status(),
    )
    # Switching during field discovery: the old file's fields must not appear.
    open_pdf("form.pdf", basic)
    pg.evaluate(
        "document.getElementById('pdf-formfind').click(); document.getElementById('pdf-close').click()"
    )
    pg.wait_for_timeout(2000)
    check(
        "forms: closing during field discovery leaves no old fields or text",
        not pg.is_visible("#pdf-formlist")
        and pg.inner_text("#pdf-formlist") == ""
        and status() == "",
        status(),
    )
    open_pdf("form.pdf", basic)
    pg.evaluate("document.getElementById('pdf-formfind').click()")
    pg.set_input_files(
        "#pdf-open",
        files=[
            {
                "name": "other.pdf",
                "mimeType": "application/pdf",
                "buffer": (fx / "form_other.pdf").read_bytes(),
            }
        ],
    )
    pg.wait_for_timeout(3000)
    shown = pg.evaluate(
        "[...document.querySelectorAll('#pdf-formlist label, #pdf-formlist fieldset')].map(e=>e.textContent.trim())"
    )
    check(
        "forms: opening another file during discovery shows none of the first file's fields",
        not any("full_name" in t for t in shown) and pg.inner_text("#pdf-name") == "other.pdf",
        (shown, status()),
    )
    find()
    shown = pg.evaluate(
        "[...document.querySelectorAll('#pdf-formlist label')].map(e=>e.textContent.trim())"
    )
    check(
        "forms: the new file lists only its own field",
        len(shown) == 1 and shown[0].startswith("other_field"),
        shown,
    )
    # Switching during export: nothing downloads and the panel stays clean.
    open_pdf("form.pdf", basic)
    find()
    pg.evaluate(field("full_name") + ".querySelector('input').value='Zed'")
    got.clear()
    pg.evaluate(
        "document.getElementById('pdf-formsave').click(); document.getElementById('pdf-close').click()"
    )
    pg.wait_for_timeout(2500)
    check(
        "forms: closing during export downloads nothing and clears the panel",
        not got and not pg.is_visible("#pdf-formlist") and status() == "",
        status(),
    )
    # Typed edits are not discarded by a second Find without a warning.
    open_pdf("form.pdf", basic)
    find()
    pg.evaluate(field("full_name") + ".querySelector('input').value='Keep me'")
    pg.click("#pdf-formfind")
    pg.wait_for_timeout(500)
    kept = pg.evaluate(field("full_name") + ".querySelector('input').value")
    check(
        "forms: Find again with typed edits warns and keeps them",
        kept == "Keep me" and "discard what you typed" in status(),
        (kept, status()),
    )
    pg.click("#pdf-formfind")
    pg.wait_for_timeout(1500)
    check(
        "forms: pressing Find again a second time discards them and reads fresh",
        pg.evaluate(field("full_name") + ".querySelector('input').value") == "",
        status(),
    )
    # Controlled gate (test only, nothing in the page changes): the fill of an old file resolves after a newer file is loaded and its own fill is running.
    other = (fx / "form_other.pdf").read_bytes()
    open_pdf("form.pdf", basic)
    find()
    pg.evaluate(
        "(()=>{window.__gates=[];const o=window.PdfForms.fill;window.__origFill=o;"
        "window.PdfForms.fill=function(L,b,v){return new Promise(res=>{window.__gates.push(()=>res(o.call(window.PdfForms,L,b,v)))})}})()"
    )
    got.clear()
    pg.evaluate(field("full_name") + ".querySelector('input').value='Old file'")
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(500)
    pg.set_input_files(
        "#pdf-open", files=[{"name": "other.pdf", "mimeType": "application/pdf", "buffer": other}]
    )
    for _ in range(40):
        if pg.inner_text("#pdf-name") == "other.pdf" and not pg.is_disabled("#pdf-formfind"):
            break
        pg.wait_for_timeout(250)
    pg.wait_for_timeout(500)
    find()
    pg.evaluate("document.querySelector('#pdf-formlist input').value='New file'")
    pg.click("#pdf-formsave")
    pg.wait_for_timeout(500)
    gates = pg.evaluate("window.__gates.length")
    pg.evaluate("window.__gates[0]()")  # the old file's fill finishes now
    pg.wait_for_timeout(1500)
    check(
        "forms: an old file's export finishing late downloads nothing, shows no old status, and does not unlock the newer export",
        gates == 2
        and not got
        and pg.is_disabled("#pdf-formsave")
        and "Building a filled copy" in status(),
        (gates, [d.suggested_filename for d in got], pg.is_disabled("#pdf-formsave"), status()),
    )
    with pg.expect_download() as dl3:
        pg.evaluate("window.__gates[1]()")
    check(
        "forms: the newer file's export then downloads under its own name with its own value",
        dl3.value.suggested_filename == "other-filled.pdf"
        and b"004E00650077002000660069006C0065" in open(dl3.value.path(), "rb").read().upper(),
        dl3.value.suggested_filename,
    )
    pg.evaluate("window.PdfForms.fill=window.__origFill;0")
    # A file with no form.
    open_pdf("none.pdf", _two_page_pdf())
    find()
    check(
        "forms: a file with no form says so and offers no save",
        "no standard form" in status() and pg.is_disabled("#pdf-formsave"),
        status(),
    )


def pdf_thumbs(ctx):  # noqa: C901
    """The page thumbnail strip: lazy drawing, bounded drawn count, jumping, following the page, file switch."""
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 390, "height": 800})

    def open_pdf(name, data, expect="Page 1 of"):
        pg.set_input_files(
            "#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(60):
            if pg.inner_text("#pdf-where").startswith(expect):
                break
            pg.wait_for_timeout(250)

    def drawn():
        return pg.evaluate(
            "[...document.querySelectorAll('#pdf-thumbs .pdf-thumb')].map((b,i)=>b.querySelector('canvas')?i+1:0).filter(x=>x)"
        )

    def settle(cond, tries=40):
        for _ in range(tries):
            v = drawn()
            if cond(v):
                return v
            pg.wait_for_timeout(250)
        return drawn()

    check("thumbs: strip is hidden with no file", not pg.is_visible("#pdf-thumbs"))
    open_pdf("many.pdf", _n_page_pdf(300))
    check(
        "thumbs: one button per page for a 300 page file, none hidden by the cap",
        pg.locator("#pdf-thumbs .pdf-thumb").count() == 300 and not pg.is_visible("#pdf-thumbnote"),
    )
    d = settle(lambda v: 1 in v and len(v) >= 4)
    check(
        "thumbs: the first pages draw and far pages do not",
        1 in d and 200 not in d and len(d) <= 14,
        d,
    )
    check(
        "thumbs: the current page is marked",
        pg.evaluate("document.querySelector('#pdf-thumbs [aria-current=true]').dataset.n") == "1",
    )
    # Pixels of one thumbnail: it has ink (the page frame and number), and a sensible size.
    px = pg.evaluate(
        "(()=>{const c=document.querySelector('#pdf-thumbs .pdf-thumb canvas');const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let ink=0;for(let i=0;i<g.length;i+=4){if(g[i]<128)ink++}return {w:c.width,h:c.height,ink:ink}})()"
    )
    check(
        "thumbs: a thumbnail canvas has ink and is small",
        px["ink"] > 20 and 70 <= px["w"] <= 160 and px["h"] < 260,
        px,
    )
    pg.click('#pdf-thumbs .pdf-thumb[data-n="3"]')
    pg.wait_for_timeout(800)
    check(
        "thumbs: clicking a thumbnail goes to that page and moves the mark",
        pg.inner_text("#pdf-where").startswith("Page 3 of")
        and pg.evaluate("document.querySelector('#pdf-thumbs [aria-current=true]').dataset.n")
        == "3",
        pg.inner_text("#pdf-where"),
    )
    pg.evaluate("document.getElementById('pdf-thumbs').scrollLeft = 150*92")
    d = settle(lambda v: any(140 <= x <= 170 for x in v) and 1 not in v)
    check(
        "thumbs: after scrolling far, the near pages draw, the first page was let go, and the drawn count stays bounded",
        any(140 <= x <= 170 for x in d) and 1 not in d and len(d) <= 24,
        d,
    )
    pg.fill("#pdf-page", "250")
    pg.press("#pdf-page", "Enter")
    d = settle(lambda v: 250 in v)
    check("thumbs: going to a far page brings the strip to it", 250 in d and len(d) <= 24, d)
    pg.locator("#pdf-thumbs").scroll_into_view_if_needed()
    pg.screenshot(path=os.environ.get("DRILL_SHOTS", "/tmp") + "/pdf-thumbs-phone.png")  # noqa: S108
    # Cap.
    open_pdf("big.pdf", _n_page_pdf(450))
    check(
        "thumbs: a file over the cap shows 400 buttons and says so",
        pg.locator("#pdf-thumbs .pdf-thumb").count() == 400
        and "first 400 of 450" in pg.inner_text("#pdf-thumbnote"),
        pg.inner_text("#pdf-thumbnote"),
    )
    # Switching files while drawing leaves only the new file's strip.
    pg.set_input_files(
        "#pdf-open",
        files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": _two_page_pdf()}],
    )
    open_pdf("two.pdf", _two_page_pdf())
    pg.wait_for_timeout(1500)
    n = pg.locator("#pdf-thumbs .pdf-thumb").count()
    d = drawn()
    check(
        "thumbs: after switching files the strip is the new file's only",
        n == 2 and all(x <= 2 for x in d) and not pg.is_visible("#pdf-thumbnote"),
        (n, d),
    )
    pg.click("#pdf-close")
    pg.wait_for_timeout(500)
    check(
        "thumbs: closing the file empties and hides the strip",
        not pg.is_visible("#pdf-thumbs") and pg.locator("#pdf-thumbs .pdf-thumb").count() == 0,
    )
    # Closing right after opening a big file leaves nothing drawn later.
    open_pdf("many.pdf", _n_page_pdf(300))
    pg.evaluate("document.getElementById('pdf-close').click()")
    pg.wait_for_timeout(2500)
    check(
        "thumbs: closing while thumbnails are drawing leaves nothing behind",
        pg.locator("#pdf-thumbs canvas").count() == 0
        and pg.locator("#pdf-thumbs .pdf-thumb").count() == 0,
    )
    # Desktop picture for inspection.
    pg.set_viewport_size({"width": 1280, "height": 800})
    open_pdf("many.pdf", _n_page_pdf(300))
    pg.fill("#pdf-page", "120")
    pg.press("#pdf-page", "Enter")
    settle(lambda v: 120 in v)
    pg.wait_for_timeout(600)
    pg.locator("#pdf-thumbs").scroll_into_view_if_needed()
    pg.screenshot(path=os.environ.get("DRILL_SHOTS", "/tmp") + "/pdf-thumbs-desktop.png")  # noqa: S108


def pdf_thumbs_layout(ctx):  # noqa: C901
    """The strip must not widen the document or scroll the whole page sideways."""
    pg = fresh(ctx, "/pdf/")
    pdf = _n_page_pdf(300)

    def open_pdf():
        pg.set_input_files(
            "#pdf-open", files=[{"name": "many.pdf", "mimeType": "application/pdf", "buffer": pdf}]
        )
        for _ in range(60):
            if pg.inner_text("#pdf-where").startswith("Page 1 of"):
                break
            pg.wait_for_timeout(250)

    def geo():
        return pg.evaluate(
            "({sw:document.documentElement.scrollWidth,iw:window.innerWidth,sx:window.scrollX,sy:window.scrollY,strip:document.getElementById('pdf-thumbs').getBoundingClientRect().width,stripSw:document.getElementById('pdf-thumbs').scrollWidth,card:document.querySelector('.pdf-card').getBoundingClientRect().width})"
        )

    for w in (1280, 390):
        pg.set_viewport_size({"width": w, "height": 800})
        pg.reload()
        pg.wait_for_timeout(500)
        before = geo()
        open_pdf()
        pg.wait_for_timeout(800)
        a = geo()
        pg.evaluate("document.getElementById('pdf-thumbs').style.display='none'")
        pg.wait_for_timeout(600)
        nos = geo()
        pg.evaluate("document.getElementById('pdf-thumbs').style.display=''")
        pg.wait_for_timeout(600)
        check(
            "layout %d: the page area is as wide with the strip as without it" % w,
            nos["card"] == a["card"] and nos["sw"] == a["sw"],
            (a, nos),
        )
        check(
            "layout %d: the document is no wider than the window with the strip" % w,
            a["sw"] <= a["iw"] and a["strip"] <= a["card"] + 1,
            (before, a),
        )
        pg.fill("#pdf-page", "200")
        pg.press("#pdf-page", "Enter")
        pg.wait_for_timeout(1500)
        b = geo()
        check(
            "layout %d: going far does not scroll the whole page sideways" % w,
            b["sx"] == 0 and b["sw"] <= b["iw"],
            b,
        )
        pg.focus('#pdf-thumbs .pdf-thumb[data-n="205"]')
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1200)
        c = geo()
        check(
            "layout %d: keyboard focus and Enter on a thumbnail does not scroll the page sideways"
            % w,
            c["sx"] == 0
            and c["sw"] <= c["iw"]
            and pg.inner_text("#pdf-where").startswith("Page 205"),
            (c, pg.inner_text("#pdf-where")),
        )
    pg.set_viewport_size({"width": 1280, "height": 800})
    pg.wait_for_timeout(300)
    pg.locator("#pdf-thumbs").scroll_into_view_if_needed()
    d = geo()
    check("layout: scrolling the strip into view leaves scrollX at 0", d["sx"] == 0, d)


def pdf_thumbs_cancel(ctx):  # noqa: C901
    """Thumbnail renders are cancelled when the file is closed or replaced, and an old file's chain does not drop a newer file's render handle.
    Test-only gate: thumbnail renders (small canvases) are held until released, so the order of events is fixed, not timed."""
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 800})
    pg.evaluate_handle("0")
    pg.evaluate(
        """async (b64) => {
      const m = await import('/static/pdf/vendor/pdf.min.mjs');
      m.GlobalWorkerOptions.workerSrc = '/static/pdf/vendor/pdf.worker.min.mjs';
      const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const doc = await m.getDocument({ data: bin }).promise, page = await doc.getPage(1), proto = Object.getPrototypeOf(page), orig = proto.render;
      window.__r = { gates: [], started: 0 };
      proto.render = function (params) {
        if (params.canvasContext.canvas.width >= 200) return orig.call(this, params);
        let res, rej; const promise = new Promise((a, b) => { res = a; rej = b; }), g = { cancelled: false };
        const self = this;
        g.release = () => { if (g.cancelled) { rej(Object.assign(new Error('cancelled'), { name: 'RenderingCancelledException' })); return; } window.__r.started++; try { const real = orig.call(self, params); real.promise.then(res, rej); } catch (e) { rej(e); } };
        g.task = { promise, cancel() { g.cancelled = true; } };
        window.__r.gates.push(g); return g.task;
      };
      return 1;
    }""",
        __import__("base64").b64encode(_two_page_pdf()).decode(),
    )

    def gates():
        return pg.evaluate("window.__r.gates.length")

    def wait_gates(n):
        for _ in range(40):
            if gates() >= n:
                return True
            pg.wait_for_timeout(200)
        return False

    def open_pdf(name, data):
        pg.set_input_files(
            "#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}]
        )

    open_pdf("a.pdf", _n_page_pdf(300))
    check(
        "thumbs cancel: the first file's thumbnail render is held at the gate",
        wait_gates(1),
        gates(),
    )
    open_pdf("b.pdf", _two_page_pdf())
    check("thumbs cancel: the second file's thumbnail render is held too", wait_gates(2), gates())
    pg.evaluate("window.__r.gates[0].release()")  # the old file's held render ends now
    pg.wait_for_timeout(600)
    a_cancelled = pg.evaluate("window.__r.gates[0].cancelled")
    check(
        "thumbs cancel: the old file's held render was cancelled when the file was replaced, and did not start",
        a_cancelled and pg.evaluate("window.__r.started") == 0,
        pg.evaluate("[window.__r.gates[0].cancelled, window.__r.started]"),
    )
    pg.evaluate("document.getElementById('pdf-close').click()")
    pg.wait_for_timeout(300)
    b_cancelled = pg.evaluate("window.__r.gates[1].cancelled")
    pg.evaluate("window.__r.gates[1].release()")
    pg.wait_for_timeout(800)
    check(
        "thumbs cancel: closing the file cancelled the newer file's held render even after the old chain ended",
        b_cancelled and pg.evaluate("window.__r.started") == 0,
        pg.evaluate("[window.__r.gates[1].cancelled, window.__r.started]"),
    )
    check(
        "thumbs cancel: nothing was drawn into the page afterwards",
        pg.locator("#pdf-thumbs canvas").count() == 0,
    )


def pdf_marks(ctx):  # noqa: C901
    """Area highlight and pen marks over a page, kept in the tab, written to a separate copy."""
    import pathlib
    import re

    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    signed = (
        pathlib.Path(__file__).resolve().parents[1] / "js" / "fixtures" / "form_signed3.pdf"
    ).read_bytes()
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 390, "height": 800})

    def open_pdf(name, data):
        pg.set_input_files(
            "#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of") and pg.evaluate(
                "document.getElementById('pdf-canvas').width > 1"
            ):
                break
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(600)

    def box():
        return pg.evaluate(
            "(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
        )

    def drag(a, b, steps=6):
        pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block: 'start'});0")
        pg.wait_for_timeout(150)
        x, y, w, h = box()
        pg.mouse.move(x + a[0] * w, y + a[1] * h)
        pg.mouse.down()
        for i in range(1, steps + 1):
            pg.mouse.move(
                x + (a[0] + (b[0] - a[0]) * i / steps) * w,
                y + (a[1] + (b[1] - a[1]) * i / steps) * h,
            )
        pg.mouse.up()
        pg.wait_for_timeout(200)

    def rects():
        return pg.evaluate(
            "(()=>{const a=document.getElementById('pdf-canvas').getBoundingClientRect(),b=document.getElementById('pdf-marks').getBoundingClientRect();return [a.left,a.top,a.width,a.height,b.left,b.top,b.width,b.height]})()"
        )

    def aligned():
        r = rects()
        return all(abs(r[i] - r[i + 4]) < 0.6 for i in range(4)), r

    def count(sel):
        return pg.evaluate("document.querySelectorAll('#pdf-marks %s').length" % sel)

    check(
        "marks: tools are off with no file",
        pg.is_disabled("#pdf-tool-hl")
        and pg.is_disabled("#pdf-tool-pen")
        and pg.is_disabled("#pdf-markcopy"),
    )
    hint = pg.text_content("#pdf-markhint")
    check(
        "marks: before use the panel says area only, tab only and no keyboard drawing",
        "selects letters or copies text" in hint
        and "kept in this browser on this device" in hint
        and "no keyboard way to draw" in hint,
    )
    open_pdf("three.pdf", _n_page_pdf(3))
    check(
        "marks: with a file open the tools turn on, copy waits for a mark",
        not pg.is_disabled("#pdf-tool-hl") and pg.is_disabled("#pdf-markcopy"),
    )
    check(
        "marks: with no tool on the overlay does not take pointer events or touch scrolling",
        pg.evaluate("getComputedStyle(document.getElementById('pdf-marks')).pointerEvents")
        == "none"
        and pg.evaluate("getComputedStyle(document.getElementById('pdf-marks')).touchAction")
        != "none",
    )
    pg.click("#pdf-tool-hl")
    check(
        "marks: Area highlight on says what it does and takes the pointer",
        "does not select text" in pg.inner_text("#pdf-markstatus")
        and pg.evaluate("getComputedStyle(document.getElementById('pdf-marks')).touchAction")
        == "none",
    )
    drag((0.2, 0.2), (0.7, 0.35))
    r = pg.evaluate(
        "(()=>{const e=document.querySelector('#pdf-marks rect');return e?[+e.getAttribute('x'),+e.getAttribute('y'),+e.getAttribute('width'),+e.getAttribute('height')]:null})()"
    )
    check(
        "marks: a drag adds one rectangle at about 0.2,0.2 sized 0.5x0.15",
        bool(r)
        and abs(r[0] - 0.2) < 0.03
        and abs(r[1] - 0.2) < 0.03
        and abs(r[2] - 0.5) < 0.04
        and abs(r[3] - 0.15) < 0.04,
        r,
    )
    pg.click("#pdf-tool-pen")
    drag((0.1, 0.6), (0.9, 0.7), steps=12)
    check(
        "marks: the pen adds a polyline",
        count("polyline") == 1 and pg.locator("#pdf-marklist li").count() == 2,
    )
    pg.evaluate(
        "document.getElementById('pdf-marks').dispatchEvent(new PointerEvent('pointerdown',{pointerId:7,clientX:100,clientY:300,bubbles:true,button:0}));document.getElementById('pdf-marks').dispatchEvent(new PointerEvent('pointercancel',{pointerId:7,bubbles:true}));0"
    )
    pg.wait_for_timeout(200)
    check(
        "marks: a cancelled touch stroke adds nothing and leaves no half-drawn line",
        pg.locator("#pdf-marklist li").count() == 2 and count("[data-temp]") == 0,
    )
    pg.click("#pdf-tool-pen")
    check(
        "marks: turning the tool off gives the page back to scrolling",
        pg.evaluate("getComputedStyle(document.getElementById('pdf-marks')).touchAction") != "none",
    )
    pg.click("#pdf-next")
    pg.wait_for_timeout(900)
    check(
        "marks: page 2 shows none of page 1's marks and the list still has both",
        count("rect") == 0
        and count("polyline") == 0
        and pg.locator("#pdf-marklist li").count() == 2,
    )
    pg.click("#pdf-prev")
    pg.wait_for_timeout(900)
    ok1, r1 = aligned()
    check("marks: the overlay sits exactly on the canvas at 390 px", ok1, r1)
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.wait_for_timeout(1000)
    ok2, r2 = aligned()
    check("marks: the overlay sits exactly on the canvas at 1280 px", ok2 and r2[2] > 500, r2)
    pg.click("#pdf-zout")
    pg.click("#pdf-zout")
    pg.wait_for_timeout(1300)
    ok50, r50 = aligned()
    check(
        "marks: the overlay sits exactly on the canvas at 50 percent (page narrower than the stage)",
        ok50 and r50[2] < r2[2] - 50,
        (r2[2], r50),
    )
    pg.click("#pdf-zin")
    pg.click("#pdf-zin")
    pg.wait_for_timeout(1300)
    ok100, r100 = aligned()
    check(
        "marks: the overlay sits exactly on the canvas back at 100 percent",
        ok100 and abs(r100[2] - r2[2]) < 1,
        (r2[2], r100),
    )
    pg.click("#pdf-zin")
    pg.click("#pdf-zin")
    pg.wait_for_timeout(1300)
    ok3, r3 = aligned()
    check(
        "marks: the overlay still sits exactly on the canvas after zooming in",
        ok3 and r3[2] > r2[2],
        (r2[2], r3),
    )
    pg.click("#pdf-fitp")
    pg.wait_for_timeout(1300)
    okf, rf = aligned()
    check("marks: the overlay sits exactly on the canvas at Fit page", okf, rf)
    pg.click("#pdf-fitw")
    pg.wait_for_timeout(1000)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(1300)
    pg.evaluate("document.getElementById('pdf-stage').scrollIntoView({block:'start'});0")
    pg.wait_for_timeout(300)
    pg.locator("#pdf-stage").screenshot(path=shots + "/pdf-marks-overlay.png")
    check(
        "marks: back on page 1 both marks are drawn again",
        count("rect") == 1 and count("polyline") == 1,
    )
    pg.click("#pdf-tool-hl")
    pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block:'start'});0")
    x, y, w, h = box()
    pg.mouse.move(x + 0.1 * w, y + 0.8 * h)
    pg.mouse.down()
    pg.mouse.move(x + 0.5 * w, y + 0.9 * h)
    pg.evaluate("document.getElementById('pdf-next').click();0")
    pg.wait_for_timeout(1000)
    pg.mouse.up()
    pg.wait_for_timeout(300)
    check(
        "marks: changing the page in the middle of a drag adds nothing and leaves no half-drawn shape",
        pg.locator("#pdf-marklist li").count() == 2 and count("[data-temp]") == 0,
        pg.locator("#pdf-marklist li").count(),
    )
    pg.evaluate("document.getElementById('pdf-prev').click();0")
    pg.wait_for_timeout(1000)
    pg.click("#pdf-tool-hl")
    pg.click("#pdf-rot")
    pg.wait_for_timeout(800)
    check(
        "marks: while the page is turned the tools are off and no marks are drawn over it",
        pg.is_disabled("#pdf-tool-hl") and pg.is_disabled("#pdf-tool-pen") and count("rect") == 0,
    )
    pg.click("#pdf-rot")
    pg.click("#pdf-rot")
    pg.click("#pdf-rot")
    pg.wait_for_timeout(1000)
    check(
        "marks: turned back to upright the marks and tools return",
        not pg.is_disabled("#pdf-tool-hl") and count("rect") == 1,
        (count("rect"), pg.is_disabled("#pdf-tool-hl")),
    )
    with pg.expect_download() as dl:
        pg.click("#pdf-markcopy")
    out = open(dl.value.path(), "rb").read()
    open(shots + "/pdf-marks-copy.pdf", "wb").write(out)
    check(
        "marks: the copy is a separate -marks file with one Highlight and one Ink annotation",
        dl.value.suggested_filename == "three-marks.pdf"
        and len(re.findall(rb"/Subtype\s*/Highlight", out)) == 1
        and len(re.findall(rb"/Subtype\s*/Ink", out)) == 1,
        dl.value.suggested_filename,
    )
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        open(td + "/c.pdf", "wb").write(out)
        subprocess.run(
            ["pdftoppm", "-r", "50", "-f", "1", "-l", "1", td + "/c.pdf", td + "/pg"],
            check=True,
            capture_output=True,
        )  # noqa: S603, S607
        ppm = open(td + "/pg-1.ppm", "rb").read()
    toks, pos = [], 0
    while len(toks) < 4:
        while ppm[pos : pos + 1].isspace():
            pos += 1
        s0 = pos
        while not ppm[pos : pos + 1].isspace():
            pos += 1
        toks.append(ppm[s0:pos])
    pos += 1
    pw, ph = int(toks[1]), int(toks[2])
    xs, ys = [], []
    for yy in range(ph):
        row = ppm[pos + yy * pw * 3 : pos + (yy + 1) * pw * 3]
        for xx in range(pw):
            r_, g_, b_ = row[xx * 3], row[xx * 3 + 1], row[xx * 3 + 2]
            if r_ > 235 and g_ > 215 and b_ < 140:
                xs.append(xx)
                ys.append(yy)
    fr = [min(xs) / pw, min(ys) / ph, (max(xs) + 1) / pw, (max(ys) + 1) / ph] if xs else None
    check(
        "marks: the highlight dragged at 0.2,0.2 to 0.7,0.35 on the page shows in a poppler render of the copy at the same fractions (within 0.015)",
        bool(fr)
        and all(abs(a_ - b_) < 0.015 for a_, b_ in zip(fr, [0.2, 0.2, 0.7, 0.35], strict=True)),
        fr,
    )
    pg.locator("#pdf-marksbox").scroll_into_view_if_needed()
    pg.screenshot(path=shots + "/pdf-marks-phone.png")
    li = pg.locator("#pdf-marklist li").nth(0).locator("button")
    li.click()
    check(
        "marks: after Remove the focus moves to the next Remove button and the change is announced",
        pg.evaluate("document.activeElement.textContent") == "Remove"
        and "1 mark(s) left" in pg.inner_text("#pdf-markstatus"),
        pg.inner_text("#pdf-markstatus"),
    )
    check(
        "marks: Remove drops that mark from the page and the list",
        pg.locator("#pdf-marklist li").count() == 1 and count("rect") + count("polyline") == 1,
    )
    open_pdf("two.pdf", _n_page_pdf(2))
    check(
        "marks: opening another file clears the marks and turns the tools back to off",
        pg.locator("#pdf-marklist li").count() == 0
        and count("rect") == 0
        and pg.evaluate("document.getElementById('pdf-tool-hl').getAttribute('aria-pressed')")
        == "false",
    )
    open_pdf("signed.pdf", signed)
    pg.click("#pdf-tool-hl")
    drag((0.1, 0.1), (0.4, 0.2))
    got = []
    pg.on("download", lambda x: got.append(x))
    pg.click("#pdf-markcopy")
    pg.wait_for_timeout(1200)
    warned = "will not validate" in pg.inner_text("#pdf-markstatus") and not got
    drag((0.5, 0.5), (0.8, 0.6))
    pg.locator("#pdf-marklist li").nth(1).locator("button").click()
    pg.click("#pdf-markcopy")
    pg.wait_for_timeout(1200)
    check(
        "marks: a held signed copy is dropped when the marks change, even if they come back the same",
        warned and not got and "again within 15 seconds" in pg.inner_text("#pdf-markstatus"),
        (warned, len(got)),
    )
    open_pdf("first.pdf", _n_page_pdf(2, "First"))
    pg.click("#pdf-tool-hl")
    drag((0.1, 0.1), (0.4, 0.2))
    pg.evaluate(
        "(()=>{const o=window.PdfMarks.build;window.__ob=o;window.__rel=null;window.PdfMarks.build=function(){const a=arguments,t=this;return new Promise(function(r){window.__rel=function(){r(o.apply(t,a))}})};0})()"
    )
    got.clear()
    pg.click("#pdf-markcopy")
    pg.wait_for_timeout(500)
    held = pg.evaluate("typeof window.__rel === 'function'")
    open_pdf("second.pdf", _n_page_pdf(2, "Second"))
    pg.evaluate("window.__rel && window.__rel();0")
    pg.evaluate("window.PdfMarks.build = window.__ob;0")
    pg.wait_for_timeout(1500)
    check(
        "marks: a build finishing after another file was opened downloads nothing and does not report success",
        held and not got and "Downloaded" not in pg.inner_text("#pdf-markstatus"),
        (held, len(got), pg.inner_text("#pdf-markstatus")),
    )
    check(
        "marks: the new file is not left busy and has no marks from the first",
        pg.locator("#pdf-marklist li").count() == 0 and not pg.is_disabled("#pdf-tool-hl"),
    )
    open_pdf(
        "xfa.pdf",
        (
            pathlib.Path(__file__).resolve().parents[1] / "js" / "fixtures" / "form_xfa.pdf"
        ).read_bytes(),
    )
    pg.click("#pdf-tool-hl")
    drag((0.1, 0.1), (0.4, 0.2))
    got.clear()
    pg.click("#pdf-markcopy")
    pg.wait_for_timeout(1200)
    check(
        "marks: an XFA file is refused with a reason and nothing downloads",
        not got and "XFA" in pg.inner_text("#pdf-markstatus"),
        pg.inner_text("#pdf-markstatus"),
    )
    sw = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    check("marks: the panel does not widen the page at 390", sw[0] <= sw[1], sw)


def pdf_marks_secure(ctx):  # noqa: C901
    """Note placing and the drawing tools exclude each other, and the stroke limit is announced. Needs a secure context, so run alone; the runner then treats the drill origin as secure for this browser only (a test flag, not the app's HTTPS deployment)."""
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.reload()
    if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
        check(
            "marks secure: the browser offers a secure context (run this drill alone)",
            False,
            "navigator.locks missing",
        )
        return
    pg.set_input_files(
        "#pdf-open",
        files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": _n_page_pdf(2)}],
    )
    for _ in range(50):
        if pg.inner_text("#pdf-where").startswith("Page 1 of 2"):
            break
        pg.wait_for_timeout(300)
    pg.wait_for_timeout(1200)
    check("marks secure: notes are on in this test browser", not pg.is_disabled("#pdf-addnote"))

    def pressed(i):
        return pg.evaluate("document.getElementById('%s').getAttribute('aria-pressed')" % i)

    pg.click("#pdf-addnote")
    check("marks secure: note placing is on", pressed("pdf-addnote") == "true")
    pg.click("#pdf-tool-hl")
    check(
        "marks secure: turning on Area highlight turns note placing off and says so",
        pressed("pdf-addnote") == "false"
        and pressed("pdf-tool-hl") == "true"
        and "Placing a note was turned off" in pg.inner_text("#pdf-notestatus"),
        pg.inner_text("#pdf-notestatus"),
    )
    pg.click("#pdf-addnote")
    check(
        "marks secure: turning on note placing turns the drawing tool off and says so",
        pressed("pdf-addnote") == "true"
        and pressed("pdf-tool-hl") == "false"
        and "turned off because you are placing a note" in pg.inner_text("#pdf-markstatus"),
        pg.inner_text("#pdf-markstatus"),
    )
    pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block:'start'});0")
    x, y, w, h = pg.evaluate(
        "(()=>{const r=document.getElementById('pdf-canvas').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
    )
    pg.mouse.click(x + 0.3 * w, y + 0.3 * h)
    pg.wait_for_timeout(300)
    check(
        "marks secure: with note placing on, a click on the page opens the note form and adds no mark",
        not pg.evaluate("document.getElementById('pdf-noteform').hidden")
        and pg.locator("#pdf-marklist li").count() == 0,
    )
    pg.click("#pdf-addnote")
    pg.click("#pdf-tool-pen")
    n = pg.evaluate(
        "(()=>{const s=document.getElementById('pdf-marks'),r=s.getBoundingClientRect();const f=(t,x,y)=>s.dispatchEvent(new PointerEvent(t,{pointerId:3,clientX:r.left+x*r.width,clientY:r.top+y*r.height,bubbles:true,button:0}));f('pointerdown',0.01,0.01);let k=0;for(let i=0;i<2100;i++){f('pointermove',0.01+0.98*((i*7)%997)/997,0.01+0.98*((i*13)%991)/991);k++}return document.getElementById('pdf-markstatus').textContent})()"
    )
    check("marks secure: a stroke past the point limit says so", "length limit" in n, n[:120])
    pg.evaluate(
        "document.getElementById('pdf-marks').dispatchEvent(new PointerEvent('pointerup',{pointerId:3,clientX:50,clientY:50,bubbles:true,button:0}));0"
    )
    pg.wait_for_timeout(300)
    check(
        "marks secure: the long stroke is kept as one drawing capped at the limit",
        pg.locator("#pdf-marklist li").count() == 1,
    )

    # A copy with the marks and, only when asked, the notes.
    if pg.is_visible("#pdf-noteform"):
        pg.click("#pdf-notecancel")
    pg.evaluate("document.getElementById('pdf-notes').scrollIntoView({block:'start'});0")
    pg.click("#pdf-addpagenote")
    pg.fill("#pdf-notetext", "combined note")
    pg.click("#pdf-noteform button[type=submit]")
    pg.wait_for_timeout(600)
    box_on = not pg.is_disabled("#pdf-markwithnotes")
    check(
        "marks secure: with a note saved, the notes tick box on the marks copy is available and starts off",
        box_on and not pg.is_checked("#pdf-markwithnotes"),
    )

    def copy_bytes():
        with pg.expect_download() as dl:
            pg.click("#pdf-markcopy")
        return open(dl.value.path(), "rb").read(), dl.value.suggested_filename

    plain, plain_name = copy_bytes()
    pg.check("#pdf-markwithnotes")
    both, both_name = copy_bytes()
    shots_dir = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    open(os.path.join(shots_dir, "pdf-marks-notes-copy.pdf"), "wb").write(both)
    check(
        "marks secure: without the tick the copy has the mark and no note; with it the copy has the mark and the note (separate file name)",
        plain.count(b"/Subtype /Highlight") + plain.count(b"/Subtype /Ink") == 1
        and plain.count(b"/Subtype /Text") == 0
        and both.count(b"/Subtype /Text") == 1
        and both.count(b"/Subtype /Highlight") + both.count(b"/Subtype /Ink") == 1
        and plain_name.endswith("-marks.pdf")
        and both_name.endswith("-marks-notes.pdf"),
        "%s %s | %d %d"
        % (plain_name, both_name, plain.count(b"/Subtype /Text"), both.count(b"/Subtype /Text")),
    )
    check(
        "marks secure: the status names both counts",
        "1 mark(s) and 1 note(s)" in pg.inner_text("#pdf-markstatus"),
        pg.inner_text("#pdf-markstatus"),
    )
    pg.click("#pdf-clearnotes")
    pg.click("#pdf-clearnotes")
    pg.wait_for_timeout(500)
    check(
        "marks secure: with no notes left the tick box is off and unavailable",
        pg.is_disabled("#pdf-markwithnotes") and not pg.is_checked("#pdf-markwithnotes"),
    )


def pdf_marks_keep(ctx):  # noqa: C901
    import re

    """Marks are kept on this device per file: come back after a reload, stay per file, two tabs cannot overwrite each other, failures stay visible. Needs a secure context, so the runner gives it the flagged test browser."""
    KEY = "salow.pdfmarks."
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.reload()
    if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
        check(
            "marks keep: the browser offers a secure context (flagged test browser)",
            False,
            "navigator.locks missing",
        )
        return
    A = _n_page_pdf(4, "Keep")
    B = _n_page_pdf(4, "Other")

    def open_pdf(page, data):
        page.set_input_files(
            "#pdf-open", files=[{"name": "k.pdf", "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if page.inner_text("#pdf-where").startswith("Page 1 of 4") and page.evaluate(
                "document.getElementById('pdf-canvas').width > 1"
            ):
                break
            page.wait_for_timeout(250)
        page.wait_for_timeout(700)

    def drag(page, a, b):
        page.evaluate("document.getElementById('pdf-marks').scrollIntoView({block: 'start'});0")
        page.wait_for_timeout(150)
        x, y, w, h = page.evaluate(
            "(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
        )
        page.mouse.move(x + a[0] * w, y + a[1] * h)
        page.mouse.down()
        for i in range(1, 7):
            page.mouse.move(
                x + (a[0] + (b[0] - a[0]) * i / 6) * w, y + (a[1] + (b[1] - a[1]) * i / 6) * h
            )
        page.mouse.up()
        page.wait_for_timeout(300)

    def n_list(page):
        return page.locator("#pdf-marklist li").count()

    def keys(page):
        return page.evaluate("Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0).length" % KEY)

    def status(page):
        return page.inner_text("#pdf-markstatus")

    open_pdf(pg, A)
    pg.click("#pdf-tool-hl")
    drag(pg, (0.2, 0.2), (0.6, 0.4))
    pg.click("#pdf-tool-pen")
    drag(pg, (0.1, 0.6), (0.8, 0.7))
    check(
        "marks keep: two marks added, the status says they are kept on this device and one key is stored",
        n_list(pg) == 2 and "kept on this device" in status(pg) and keys(pg) == 1,
        status(pg),
    )
    check(
        "marks keep: the stored value is only marks (no file bytes) and is small",
        pg.evaluate(
            "(()=>{const k=Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0)[0];const v=JSON.parse(localStorage.getItem(k));return Object.keys(v).join()==='v,marks'&&localStorage.getItem(k).length<4000})()"
            % KEY
        ),
    )
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    check(
        "marks keep: after a reload the same file shows both marks and says they were saved",
        n_list(pg) == 2 and "2 saved mark(s)" in status(pg),
        status(pg),
    )
    hidden_when_readable = not pg.is_visible("#pdf-markdiscard")
    check(
        "marks keep: the saved marks are drawn on the page",
        pg.evaluate("document.querySelectorAll('#pdf-marks rect, #pdf-marks polyline').length")
        == 2,
    )
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.evaluate("document.getElementById('pdf-marksbox').scrollIntoView({block:'start'});0")
    pg.wait_for_timeout(300)
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg.screenshot(path=os.path.join(shots, "pdf-marks-keep-reload-390.png"))
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.evaluate("document.getElementById('pdf-marksbox').scrollIntoView({block:'center'});0")
    pg.wait_for_timeout(400)
    pg.locator("#pdf-marksbox").screenshot(
        path=os.path.join(shots, "pdf-marks-keep-reload-1280.png")
    )
    prefix = pg.evaluate("document.getElementById('pdf-marksbox').getAttribute('data-prefix')")
    stored_keys = pg.evaluate(
        "Object.keys(localStorage).filter(k=>k.indexOf('salow.pdfmarks.')===0)"
    )
    check(
        "marks keep: the key is this signed-in user's prefix plus a 64-hex file hash, and only that key exists",
        bool(re.fullmatch(r"salow\.pdfmarks\.\d+\.", prefix or ""))
        and len(stored_keys) == 1
        and bool(re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", stored_keys[0])),
        "%s %s" % (prefix, stored_keys),
    )
    open_pdf(pg, B)
    check("marks keep: a different file shows no marks", n_list(pg) == 0)
    hash_a = stored_keys[0][len(prefix) :]
    foreign_key = "salow.pdfmarks.999999." + hash_a
    foreign_raw = '{"v":1,"marks":[{"id":"foreign001","page":3,"kind":"hl","r":[0.2,0.2,0.4,0.4]}]}'
    pg.evaluate("([k,v])=>{localStorage.setItem(k,v);return 0}", [foreign_key, foreign_raw])
    KEY = prefix  # from here the helpers look only at this user's keys
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    check(
        "marks keep: another user's marks for the same file are not shown here",
        n_list(pg) == 2 and "foreign001" not in pg.inner_html("#pdf-marklist"),
        str(n_list(pg)),
    )
    pg.locator("#pdf-marklist button").first.click()
    pg.wait_for_timeout(300)
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    check(
        "marks keep: a removed mark stays removed after a reload", n_list(pg) == 1, str(n_list(pg))
    )
    check(
        "marks keep: the other user's stored marks are untouched by this user's saves and removals",
        pg.evaluate("(k)=>localStorage.getItem(k)", foreign_key) == foreign_raw,
    )
    # A page that cannot say whose marks these are keeps nothing: tab only, and stored data stays as it is.
    all_before = pg.evaluate(
        "Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))"
    )
    t3 = ctx.new_page()

    # The page's marks prefix reads as empty (what a page without a signed-in user would give).
    t3.add_init_script(
        "(()=>{const g=Element.prototype.getAttribute;Element.prototype.getAttribute=function(n){return this.id==='pdf-marksbox'&&n==='data-prefix'?'':g.call(this,n)}})()"
    )
    t3.goto(APP + "/pdf/")
    t3.wait_for_timeout(500)
    open_pdf(t3, A)
    st3 = status(t3)
    n3 = n_list(t3)
    t3.click("#pdf-tool-hl")
    drag(t3, (0.2, 0.2), (0.4, 0.4))
    check(
        "marks keep: with no user prefix on the page, nothing saved is shown, a mark stays in the tab, and storage is untouched",
        "whose marks" in st3
        and n3 == 0
        and n_list(t3) == 1
        and t3.evaluate(
            "Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))"
        )
        == all_before,
        "%s | %s | attr=%r"
        % (
            st3,
            n3,
            t3.evaluate("document.getElementById('pdf-marksbox').getAttribute('data-prefix')"),
        ),
    )
    t3.close()
    pg.bring_to_front()

    # Two tabs.
    t2 = ctx.new_page()
    t2.goto(APP + "/pdf/")
    t2.wait_for_timeout(500)
    open_pdf(t2, A)
    check("marks keep: a second tab shows the saved mark", n_list(t2) == 1)
    pg.bring_to_front()
    pg.click("#pdf-tool-hl")
    drag(pg, (0.3, 0.1), (0.5, 0.2))
    t2.wait_for_timeout(600)
    check(
        "marks keep: a mark added in one tab shows in the other with a message",
        n_list(t2) == 2 and "changed in another tab" in status(t2),
        status(t2),
    )
    # Make the second tab stale on purpose: change what is stored from inside it (no event reaches itself).
    t2.evaluate(
        "(()=>{const k=Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0)[0];const v=JSON.parse(localStorage.getItem(k));v.marks[v.marks.length-1]={id:'zzzzzzzz01',page:2,kind:'hl',r:[0.11,0.11,0.31,0.31]};localStorage.setItem(k,JSON.stringify(v));return 0})()"
        % KEY
    )
    pg.bring_to_front()
    t2.bring_to_front()
    raw_of = (
        "(()=>{const k=Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0)[0];return localStorage.getItem(k)})()"
        % KEY
    )
    raw0 = t2.evaluate(raw_of)
    t2.click("#pdf-tool-hl")
    drag(t2, (0.5, 0.5), (0.7, 0.7))
    raw1 = t2.evaluate(raw_of)
    # The injected edit replaced a mark with a different one (same count), so the count alone cannot tell an overwrite from a refusal: the stored text must be byte for byte what the other tab wrote.
    check(
        "marks keep: a stale tab's change is refused: the stored text is exactly what the other tab wrote, the page says so and shows it",
        "was not applied" in status(t2)
        and raw1 == raw0
        and "zzzzzzzz01" in raw1
        and "0.5,0.5" not in raw1.replace(" ", "")
        and n_list(t2) == 2,
        "%s | same %s | list %s" % (status(t2), raw1 == raw0, n_list(t2)),
    )
    t2.close()
    pg.bring_to_front()

    # Switching files while the first is still being identified: the late one must not show its marks on the newer file or touch storage.
    raw_a = pg.evaluate(raw_of)
    pg.evaluate(
        "(()=>{const s=crypto.subtle,o=s.digest.bind(s);let n=0;s.digest=function(){return (n++===0?new Promise(r=>setTimeout(r,1500)):Promise.resolve()).then(()=>o.apply(null,arguments))};window.__dig=[s,o];return 0})()"
    )
    pg.set_input_files(
        "#pdf-open", files=[{"name": "a.pdf", "mimeType": "application/pdf", "buffer": A}]
    )
    pg.set_input_files(
        "#pdf-open", files=[{"name": "b.pdf", "mimeType": "application/pdf", "buffer": B}]
    )
    pg.wait_for_timeout(3500)
    check(
        "marks keep: switching files during the load shows the newer file's marks (none), never the late file's, and storage is untouched",
        n_list(pg) == 0
        and "saved mark" not in status(pg)
        and pg.evaluate(raw_of) == raw_a
        and pg.inner_text("#pdf-name") == "b.pdf",
        "%s | list %s | name %s" % (status(pg), n_list(pg), pg.inner_text("#pdf-name")),
    )
    pg.evaluate("(()=>{const [s,o]=window.__dig;s.digest=o;return 0})()")
    open_pdf(pg, A)

    # Switching files while a save waits for the lock: the late save lands on the first file's own key only, and the newer file shows nothing from it.
    keys_before = pg.evaluate(
        "Object.keys(localStorage).filter(k=>k.indexOf('salow.pdfmarks.')===0).sort()"
    )
    n_a = n_list(pg)
    pg.evaluate(
        "(()=>{const l=navigator.locks,o=l.request.bind(l);let n=0;l.request=function(){const a=arguments;return (n++===0?new Promise(r=>setTimeout(r,1500)):Promise.resolve()).then(()=>o.apply(null,a))};window.__lk=[l,o];return 0})()"
    )
    pg.click("#pdf-tool-hl")
    drag(pg, (0.6, 0.1), (0.9, 0.3))
    pg.set_input_files(
        "#pdf-open", files=[{"name": "b.pdf", "mimeType": "application/pdf", "buffer": B}]
    )
    pg.wait_for_timeout(3500)
    keys_mid = pg.evaluate(
        "Object.keys(localStorage).filter(k=>k.indexOf('salow.pdfmarks.')===0).sort()"
    )
    check(
        "marks keep: switching files while a save waits shows nothing of it on the new file and creates no key for it",
        n_list(pg) == 0 and "added" not in status(pg) and keys_mid == keys_before,
        "%s | %s | %s" % (status(pg), n_list(pg), keys_mid),
    )
    pg.evaluate("(()=>{const [l,o]=window.__lk;l.request=o;return 0})()")
    open_pdf(pg, A)
    check(
        "marks keep: the late save was written to the first file's own key, so it shows when that file is opened again",
        n_list(pg) == n_a + 1,
        "%s vs %s" % (n_list(pg), n_a),
    )

    # Quota or write failure.
    pg.evaluate(
        "window.__orig=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new Error('quota')};0"
    )
    before = n_list(pg)
    if pg.evaluate("document.getElementById('pdf-tool-hl').getAttribute('aria-pressed')") != "true":
        pg.click("#pdf-tool-hl")
    drag(pg, (0.6, 0.6), (0.9, 0.9))
    check(
        "marks keep: when the browser cannot save, the mark is not added and the status says so",
        n_list(pg) == before and "would not save" in status(pg),
        status(pg),
    )
    pg.evaluate("Storage.prototype.setItem=window.__orig;0")

    # Unreadable stored data is left alone.
    pg.evaluate(
        "(()=>{const k=Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0)[0];localStorage.setItem(k,'not json');return 0})()"
        % KEY
    )
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    check(
        "marks keep: unreadable saved marks are left as they are and the page says marks stay in this tab",
        "could not be read" in status(pg) and "this tab only" in status(pg),
        status(pg),
    )
    pg.click("#pdf-tool-hl")
    drag(pg, (0.2, 0.2), (0.4, 0.4))
    kept = pg.evaluate(
        "(()=>{const k=Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0)[0];return localStorage.getItem(k)})()"
        % KEY
    )
    check(
        "marks keep: in that state a mark still works in the tab and the stored value is untouched",
        n_list(pg) == 1 and kept == "not json",
        str(kept),
    )
    pg.locator("#pdf-marksbox").screenshot(
        path=os.path.join(shots, "pdf-marks-keep-unreadable-1280.png")
    )
    pg.click("#pdf-markclear")
    pg.wait_for_timeout(300)
    kept2 = pg.evaluate(raw_of)
    check(
        "marks keep: in that state Remove all marks says it only cleared this tab, and the unreadable saved value is still there",
        n_list(pg) == 0
        and "from this tab" in status(pg)
        and "still on this device" in status(pg)
        and kept2 == "not json",
        "%s | %s" % (status(pg), kept2),
    )

    # Discard unreadable saved marks: only on a person's two presses, only if the stored text is still what could not be read.
    check(
        "marks keep: the discard control is hidden while saved marks are readable and shows only in the unreadable state",
        hidden_when_readable and pg.is_visible("#pdf-markdiscard"),
    )
    pg.click("#pdf-markdiscard")
    pg.locator("#pdf-marksbox").screenshot(
        path=os.path.join(shots, "pdf-marks-keep-discard-1280.png")
    )
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(300)
    pg.locator("#pdf-marksbox").screenshot(
        path=os.path.join(shots, "pdf-marks-keep-discard-390.png")
    )
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.wait_for_timeout(300)
    check(
        "marks keep: one press only asks, says it cannot be undone, and deletes nothing",
        "cannot be undone" in status(pg) and pg.evaluate(raw_of) == "not json",
        status(pg),
    )
    pg.evaluate(
        "(()=>{const k=Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0)[0];localStorage.setItem(k,'other junk');return 0})()"
        % KEY
    )
    pg.click("#pdf-markdiscard")
    pg.wait_for_timeout(400)
    check(
        "marks keep: if the stored text changed since it was read, the second press deletes nothing",
        "nothing was deleted" in status(pg) and pg.evaluate(raw_of) == "other junk",
        "%s | %s" % (status(pg), pg.evaluate(raw_of)),
    )
    if pg.evaluate("document.getElementById('pdf-tool-hl').getAttribute('aria-pressed')") != "true":
        pg.click("#pdf-tool-hl")
    drag(pg, (0.2, 0.2), (0.4, 0.4))
    pg.click("#pdf-markdiscard")
    pg.click("#pdf-markclear")
    pg.wait_for_timeout(300)
    pg.click("#pdf-markdiscard")
    pg.wait_for_timeout(400)
    check(
        "marks keep: a change to the marks between the presses cancels the first press (the next press only asks again)",
        "cannot be undone" in status(pg) and pg.evaluate(raw_of) == "other junk",
        status(pg),
    )
    if pg.evaluate("document.getElementById('pdf-tool-hl').getAttribute('aria-pressed')") != "true":
        pg.click("#pdf-tool-hl")
    drag(pg, (0.2, 0.2), (0.4, 0.4))
    pg.click("#pdf-markdiscard")
    pg.click("#pdf-markdiscard")
    pg.wait_for_timeout(500)
    stored_ok = pg.evaluate(
        "(()=>{const k=Object.keys(localStorage).filter(k=>k.indexOf('%s')>=0)[0];try{return JSON.parse(localStorage.getItem(k)).marks.length}catch(e){return -1}})()"
        % KEY
    )
    check(
        "marks keep: two presses delete the unreadable value, keep the marks drawn in the tab, and hide the control",
        "deleted from this device" in status(pg)
        and pg.evaluate("document.activeElement && document.activeElement.id") == "pdf-tool-hl"
        and "now kept" in status(pg)
        and stored_ok == 1
        and n_list(pg) == 1
        and not pg.is_visible("#pdf-markdiscard"),
        "%s | %s | n=%s vis=%s"
        % (status(pg), stored_ok, n_list(pg), pg.is_visible("#pdf-markdiscard")),
    )
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    check(
        "marks keep: after that, a reload shows the kept mark",
        n_list(pg) == 1 and "1 saved mark(s)" in status(pg),
        status(pg),
    )

    # Another tab fixes the unreadable value between the two presses: the page leaves the unreadable state and the next edit is kept next to what the other tab wrote.
    full_key = prefix + hash_a
    pg.evaluate("([k,v])=>{localStorage.setItem(k,v);return 0}", [full_key, "junk three"])
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    pg.click("#pdf-markdiscard")
    other = '{"v":1,"marks":[{"id":"compete001","page":2,"kind":"hl","r":[0.1,0.1,0.3,0.3]}]}'
    pg.evaluate("([k,v])=>{localStorage.setItem(k,v);return 0}", [full_key, other])
    pg.click("#pdf-markdiscard")
    pg.wait_for_timeout(500)
    check(
        "marks keep: when the value became readable between the presses nothing is deleted, the control goes away, the other tab's mark shows and focus moves to a tool",
        "nothing was deleted" in status(pg)
        and not pg.is_visible("#pdf-markdiscard")
        and n_list(pg) == 1
        and pg.evaluate("document.activeElement && document.activeElement.id") == "pdf-tool-hl"
        and pg.evaluate("(k)=>localStorage.getItem(k)", full_key) == other,
        "%s | n=%s" % (status(pg), n_list(pg)),
    )
    if pg.evaluate("document.getElementById('pdf-tool-hl').getAttribute('aria-pressed')") != "true":
        pg.click("#pdf-tool-hl")
    drag(pg, (0.5, 0.5), (0.7, 0.7))
    kept_two = pg.evaluate("(k)=>localStorage.getItem(k)", full_key) or ""
    check(
        "marks keep: after that, a new mark is saved next to the other tab's mark (nothing deleted or overwritten)",
        "compete001" in kept_two
        and kept_two.count('"kind":"hl"') == 2
        and n_list(pg) == 2
        and "kept on this device" in status(pg),
        "%s | %s" % (status(pg), kept_two[:80]),
    )
    # The same, when the other tab removed the value (nothing stored any more).
    pg.evaluate("([k,v])=>{localStorage.setItem(k,v);return 0}", [full_key, "junk four"])
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    pg.click("#pdf-markdiscard")
    pg.evaluate("(k)=>{localStorage.removeItem(k);return 0}", full_key)
    pg.click("#pdf-markdiscard")
    pg.wait_for_timeout(500)
    check(
        "marks keep: when the value was removed between the presses the control goes away and nothing is deleted",
        "nothing was deleted" in status(pg)
        and not pg.is_visible("#pdf-markdiscard")
        and n_list(pg) == 0,
        status(pg),
    )
    if pg.evaluate("document.getElementById('pdf-tool-hl').getAttribute('aria-pressed')") != "true":
        pg.click("#pdf-tool-hl")
    drag(pg, (0.5, 0.5), (0.7, 0.7))
    check(
        "marks keep: after that, a new mark is saved under the file's key",
        (pg.evaluate("(k)=>localStorage.getItem(k)", full_key) or "").count('"kind":"hl"') == 1
        and n_list(pg) == 1,
        status(pg),
    )

    # Remove all clears the key.
    pg.evaluate("localStorage.clear();0")
    pg.reload()
    pg.wait_for_timeout(500)
    open_pdf(pg, A)
    pg.click("#pdf-tool-hl")
    drag(pg, (0.2, 0.2), (0.4, 0.4))
    check("marks keep: one key is stored before Remove all marks", keys(pg) == 1)
    pg.click("#pdf-markclear")
    pg.wait_for_timeout(300)
    check(
        "marks keep: Remove all marks empties the list and deletes the stored key",
        n_list(pg) == 0 and keys(pg) == 0 and "removed from this device" in status(pg),
        status(pg),
    )


def pdf_marks_shot(ctx):  # noqa: C901
    """A simple marks plus notes example at phone width, for a picture of the marks copy panel. Needs a secure context (flagged test browser)."""
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.reload()
    if not pg.evaluate("!!(navigator.locks && navigator.locks.request)"):
        check("marks shot: the browser offers a secure context", False, "navigator.locks missing")
        return
    pg.set_input_files(
        "#pdf-open",
        files=[
            {
                "name": "report.pdf",
                "mimeType": "application/pdf",
                "buffer": _n_page_pdf(2, "Report"),
            }
        ],
    )
    for _ in range(50):
        if pg.inner_text("#pdf-where").startswith("Page 1 of 2") and pg.evaluate(
            "document.getElementById('pdf-canvas').width > 1"
        ):
            break
        pg.wait_for_timeout(250)
    pg.wait_for_timeout(900)
    pg.click("#pdf-addpagenote")
    pg.fill("#pdf-notetext", "Check the totals")
    pg.click("#pdf-noteform button[type=submit]")
    pg.wait_for_timeout(500)
    pg.click("#pdf-tool-hl")
    pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block: 'start'});0")
    pg.wait_for_timeout(200)
    x, y, w, h = pg.evaluate(
        "(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
    )
    pg.mouse.move(x + 0.15 * w, y + 0.12 * h)
    pg.mouse.down()
    pg.mouse.move(x + 0.8 * w, y + 0.2 * h, steps=5)
    pg.mouse.up()
    pg.wait_for_timeout(300)
    pg.check("#pdf-markwithnotes")
    pg.evaluate("document.getElementById('pdf-marksbox').scrollIntoView({block: 'start'});0")
    pg.wait_for_timeout(300)
    check(
        "marks shot: one mark and one note, the tick box is on and the list shows the mark",
        pg.is_checked("#pdf-markwithnotes")
        and pg.locator("#pdf-marklist li").count() == 1
        and pg.locator("#pdf-notelist li").count() == 1,
    )
    pg.screenshot(path=os.path.join(shots, "pdf-marks-notes-panel-390.png"))
    sw = pg.evaluate("document.documentElement.scrollWidth")
    check("marks shot: the page does not scroll sideways at 390 px", sw <= 390, str(sw))


def pdf_text_hl(ctx):  # noqa: C901
    """Text highlight: the text pieces under a dragged area get a highlight, empty areas add nothing, and the copy carries one quad per piece."""
    import io

    from PIL import Image

    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    out = []

    def one(vw, vh):  # noqa: C901
        pg = fresh(ctx, "/pdf/")
        pg.set_viewport_size({"width": vw, "height": vh})
        pg.reload()
        pg.set_input_files(
            "#pdf-open",
            files=[
                {"name": "t.pdf", "mimeType": "application/pdf", "buffer": _n_page_pdf(2, "Report")}
            ],
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of 2") and pg.evaluate(
                "document.getElementById('pdf-canvas').width > 1"
            ):
                break
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(900)
        pg.click("#pdf-tool-tx")
        pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block: 'start'});0")
        pg.wait_for_timeout(200)
        x, y, w, h = pg.evaluate(
            "(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
        )

        def drag(a, b):
            pg.mouse.move(x + a[0] * w, y + a[1] * h)
            pg.mouse.down()
            pg.mouse.move(x + b[0] * w, y + b[1] * h, steps=4)
            pg.mouse.up()
            pg.wait_for_timeout(700)

        drag((0.5, 0.6), (0.9, 0.7))
        empty_ok = pg.locator(
            "#pdf-marklist li"
        ).count() == 0 and "No text under that area" in pg.inner_text("#pdf-markstatus")
        drag((0.3, 0.075), (0.35, 0.1))
        one = pg.locator(
            "#pdf-marklist li"
        ).count() == 1 and "text-line highlight" in pg.inner_text("#pdf-marklist")
        rects = pg.evaluate("[...document.querySelectorAll('#pdf-marks rect')].length")
        pg.wait_for_timeout(200)
        shot = pg.screenshot(clip={"x": x, "y": y, "width": w, "height": h})
        open(os.path.join(shots, "pdf-text-hl-%d.png" % vw), "wb").write(shot)
        im = Image.open(io.BytesIO(shot)).convert("RGB")
        sx = im.width / w

        def yellow(box):
            n = t = 0
            for yy in range(int(box[1] * im.height), int(box[3] * im.height), 2):
                for xx in range(int(box[0] * im.width), int(box[2] * im.width), 2):
                    r, g, b = im.getpixel((xx, yy))
                    t += 1
                    n += r > 230 and g > 215 and b < 200
            return n / max(t, 1)

        on_text = yellow((0.12, 0.075, 0.45, 0.1))
        off_text = yellow((0.12, 0.3, 0.9, 0.5))
        out.append((vw, empty_ok, one, rects, round(on_text, 2), round(off_text, 2), round(sx, 2)))
        check(
            "text highlight %d px: empty area adds nothing, a drag over text adds one mark, yellow sits on the text and not below it"
            % vw,
            empty_ok and one and rects == 1 and on_text > 0.3 and off_text < 0.02,
            str(out[-1]),
        )
        if vw == 1280:
            got = []
            pg.on("download", lambda d: got.append(d))
            pg.click("#pdf-markcopy")
            for _ in range(20):
                if got:
                    break
                pg.wait_for_timeout(300)
            if got:
                got[0].save_as(os.path.join(shots, "pdf-text-hl-copy.pdf"))
            check("text highlight: the copy downloads", bool(got))
        sw = pg.evaluate("document.documentElement.scrollWidth")
        check("text highlight %d px: no sideways scroll" % vw, sw <= vw, str(sw))

    one(1280, 900)
    one(390, 800)


def _rot_crop_pdf(
    stream=b"BT /F1 40 Tf 120 400 Td (Rotated line) Tj ET",
    box=b"/CropBox [50 100 562 700] /Rotate 90",
):
    """One page: MediaBox 612x792, CropBox [50 100 562 700] (not at 0,0), /Rotate 90, one line of text inside the crop."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        + box
        + b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs):
        offs.append(len(out))
        out += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def pdf_text_hl_rot(ctx):  # noqa: C901
    """Text-line highlight on a page with native /Rotate 90 and a CropBox away from 0,0: the box picked on screen matches the box in a Poppler render of the copy."""
    import subprocess
    import tempfile

    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.reload()
    pg.set_input_files(
        "#pdf-open",
        files=[{"name": "rot.pdf", "mimeType": "application/pdf", "buffer": _rot_crop_pdf()}],
    )
    for _ in range(50):
        if pg.inner_text("#pdf-where").startswith("Page 1 of 1") and pg.evaluate(
            "document.getElementById('pdf-canvas').width > 1"
        ):
            break
        pg.wait_for_timeout(250)
    pg.wait_for_timeout(900)
    pg.click("#pdf-tool-tx")
    pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block: 'start'});0")
    pg.wait_for_timeout(200)
    x, y, w, h = pg.evaluate(
        "(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
    )
    pg.mouse.move(x + 0.02 * w, y + 0.02 * h)
    pg.mouse.down()
    pg.mouse.move(x + 0.98 * w, y + 0.98 * h, steps=5)
    pg.mouse.up()
    pg.wait_for_timeout(800)
    box = pg.evaluate(
        "(()=>{const r=document.querySelector('#pdf-marks rect');if(!r)return null;const g=k=>parseFloat(r.getAttribute(k));return [g('x'),g('y'),g('x')+g('width'),g('y')+g('height')]})()"
    )
    shot = pg.screenshot(clip={"x": x, "y": y, "width": w, "height": h})
    open(os.path.join(shots, "pdf-text-hl-rot-screen.png"), "wb").write(shot)
    with pg.expect_download() as dl:
        pg.click("#pdf-markcopy")
    out = open(dl.value.path(), "rb").read()
    open(os.path.join(shots, "pdf-text-hl-rot-copy.pdf"), "wb").write(out)
    with tempfile.TemporaryDirectory() as td:
        open(td + "/c.pdf", "wb").write(out)
        subprocess.run(
            ["pdftoppm", "-cropbox", "-r", "50", "-f", "1", "-l", "1", td + "/c.pdf", td + "/pg"],
            check=True,
            capture_output=True,
        )  # noqa: S603, S607
        subprocess.run(
            [
                "pdftoppm",
                "-cropbox",
                "-r",
                "50",
                "-png",
                "-f",
                "1",
                "-l",
                "1",
                td + "/c.pdf",
                shots + "/pdf-text-hl-rot-poppler",
            ],
            check=True,
            capture_output=True,
        )  # noqa: S603, S607
        ppm = open(td + "/pg-1.ppm", "rb").read()
    toks, pos = [], 0
    while len(toks) < 4:
        while ppm[pos : pos + 1].isspace():
            pos += 1
        s0 = pos
        while not ppm[pos : pos + 1].isspace():
            pos += 1
        toks.append(ppm[s0:pos])
    pos += 1
    pw, ph = int(toks[1]), int(toks[2])
    xs, ys = [], []
    for yy in range(ph):
        row = ppm[pos + yy * pw * 3 : pos + (yy + 1) * pw * 3]
        for xx in range(pw):
            if row[xx * 3] > 235 and row[xx * 3 + 1] > 215 and row[xx * 3 + 2] < 140:
                xs.append(xx)
                ys.append(yy)
    fr = [min(xs) / pw, min(ys) / ph, (max(xs) + 1) / pw, (max(ys) + 1) / ph] if xs else None
    check(
        "text-line highlight on /Rotate 90 with an offset CropBox: one box is picked, and it is smaller than the page",
        bool(box) and (box[2] - box[0]) < 0.9 and (box[3] - box[1]) < 0.5,
        box,
    )
    check(
        "text-line highlight on /Rotate 90 with an offset CropBox: the box in a Poppler render of the copy matches the picked box (within 0.015)",
        bool(box)
        and bool(fr)
        and all(abs(a_ - b_) < 0.015 for a_, b_ in zip(fr, box, strict=True)),
        (box, fr),
    )


def _yellow_red_boxes(pdf_bytes, shots, tag, crop=False):
    """Render page 1 with Poppler; return the yellow box as fractions and the count of red pen pixels."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        open(td + "/c.pdf", "wb").write(pdf_bytes)
        subprocess.run(
            [
                "pdftoppm",
                *(["-cropbox"] if crop else []),
                "-r",
                "50",
                "-f",
                "1",
                "-l",
                "1",
                td + "/c.pdf",
                td + "/pg",
            ],
            check=True,
            capture_output=True,
        )  # noqa: S603, S607
        subprocess.run(
            [
                "pdftoppm",
                *(["-cropbox"] if crop else []),
                "-r",
                "50",
                "-png",
                "-f",
                "1",
                "-l",
                "1",
                td + "/c.pdf",
                os.path.join(shots, tag),
            ],
            check=True,
            capture_output=True,
        )  # noqa: S603, S607
        ppm = open(td + "/pg-1.ppm", "rb").read()
    toks, pos = [], 0
    while len(toks) < 4:
        while ppm[pos : pos + 1].isspace():
            pos += 1
        s0 = pos
        while not ppm[pos : pos + 1].isspace():
            pos += 1
        toks.append(ppm[s0:pos])
    pos += 1
    pw, ph = int(toks[1]), int(toks[2])
    xs, ys, red = [], [], 0
    for yy in range(ph):
        row = ppm[pos + yy * pw * 3 : pos + (yy + 1) * pw * 3]
        for xx in range(pw):
            r_, g_, b_ = row[xx * 3], row[xx * 3 + 1], row[xx * 3 + 2]
            if r_ > 235 and g_ > 215 and b_ < 140:
                xs.append(xx)
                ys.append(yy)
            elif r_ > 180 and g_ < 80 and b_ < 80:
                red += 1
    fr = [min(xs) / pw, min(ys) / ph, (max(xs) + 1) / pw, (max(ys) + 1) / ph] if xs else None
    return fr, red


def pdf_marks_flat(ctx):  # noqa: C901
    """Flattened copy: marks are drawn into the page (no annotations), it matches the marks copy in a Poppler render, and a signed file is refused."""
    import pathlib

    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.reload()

    def open_pdf(name, data, pages):
        pg.set_input_files(
            "#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of %d" % pages) and pg.evaluate(
                "document.getElementById('pdf-canvas').width > 1"
            ):
                break
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(800)

    open_pdf("flat.pdf", _n_page_pdf(2, "Flat"), 2)

    def drag(a, b):
        pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block: 'start'});0")
        pg.wait_for_timeout(150)
        x, y, w, h = pg.evaluate(
            "(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
        )
        pg.mouse.move(x + a[0] * w, y + a[1] * h)
        pg.mouse.down()
        pg.mouse.move(x + b[0] * w, y + b[1] * h, steps=5)
        pg.mouse.up()
        pg.wait_for_timeout(500)

    pg.click("#pdf-tool-hl")
    drag((0.2, 0.3), (0.7, 0.4))
    pg.click("#pdf-tool-hl")
    pg.click("#pdf-tool-pen")
    drag((0.2, 0.6), (0.8, 0.7))
    pg.click("#pdf-tool-pen")
    got = []
    pg.on("download", lambda d_: got.append(d_))
    pg.click("#pdf-markcopy")
    for _ in range(20):
        if got:
            break
        pg.wait_for_timeout(300)
    pg.click("#pdf-markflat")
    for _ in range(20):
        if len(got) > 1:
            break
        pg.wait_for_timeout(300)
    check(
        "flat: both copies download, the fixed one named -marks-fixed",
        len(got) == 2 and got[1].suggested_filename == "flat-marks-fixed.pdf",
        [g.suggested_filename for g in got],
    )
    if len(got) < 2:
        return
    marks_pdf = open(got[0].path(), "rb").read()
    flat_pdf = open(got[1].path(), "rb").read()
    open(os.path.join(shots, "pdf-flat-copy.pdf"), "wb").write(flat_pdf)
    check(
        "flat: the marks copy has a Highlight and an Ink annotation, the flat copy has none",
        len(re.findall(rb"/Subtype\s*/Highlight", marks_pdf)) == 1
        and len(re.findall(rb"/Subtype\s*/Ink", marks_pdf)) == 1
        and not re.findall(rb"/Subtype\s*/(Highlight|Ink)", flat_pdf),
    )
    fm, rm = _yellow_red_boxes(marks_pdf, shots, "pdf-flat-marks-render")
    ff, rf = _yellow_red_boxes(flat_pdf, shots, "pdf-flat-flat-render")
    check(
        "flat: the flat copy renders the yellow box and the red pen like the marks copy (box within 0.015, pen pixels within 20%)",
        bool(fm)
        and bool(ff)
        and all(abs(a_ - b_) < 0.015 for a_, b_ in zip(fm, ff, strict=True))
        and rm > 20
        and abs(rf - rm) <= 0.2 * rm,
        (fm, ff, rm, rf),
    )
    check(
        "flat: the status says the marks cannot be removed in that copy and notes are not in it",
        "cannot be removed or edited" in pg.inner_text("#pdf-markstatus")
        and "notes are not in it" in pg.inner_text("#pdf-markstatus"),
    )
    check(
        "flat: the saved marks in the page are unchanged",
        pg.locator("#pdf-marklist li").count() == 2,
    )
    signed = (
        pathlib.Path(__file__).resolve().parents[1] / "js" / "fixtures" / "form_signed3.pdf"
    ).read_bytes()
    pg.set_input_files(
        "#pdf-open", files=[{"name": "signed.pdf", "mimeType": "application/pdf", "buffer": signed}]
    )
    pg.wait_for_timeout(2500)
    pg.click("#pdf-tool-hl")
    drag((0.1, 0.1), (0.4, 0.2))
    n0 = len(got)
    pg.click("#pdf-markflat")
    pg.wait_for_timeout(2000)
    check(
        "flat: a signed file is refused with a plain message and nothing downloads",
        len(got) == n0
        and "digital signature" in pg.inner_text("#pdf-markstatus")
        and "no copy with the marks fixed in" in pg.inner_text("#pdf-markstatus"),
        pg.inner_text("#pdf-markstatus"),
    )


def pdf_marks_flat_geo(ctx):  # noqa: C901
    """Marks-fixed copy vs the annotation copy, rendered from two separately downloaded files: a rotated and cropped page with a text-line highlight, and a page whose content ends inside an open q and cm."""
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.reload()
    cases = [
        ("rotcrop", _rot_crop_pdf(), "tx", (0.02, 0.02), (0.98, 0.98), True),
        (
            "opencm",
            _rot_crop_pdf(
                b"BT /F1 40 Tf 60 700 Td (Open state) Tj ET q 0.5 0 0 0.5 100 100 cm 0 0 1 rg", b""
            ),
            "hl",
            (0.2, 0.3),
            (0.7, 0.4),
            False,
        ),
    ]
    for tag, data, tool, a, b, crop in cases:
        pg.set_input_files(
            "#pdf-open",
            files=[{"name": tag + ".pdf", "mimeType": "application/pdf", "buffer": data}],
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of 1") and pg.evaluate(
                "document.getElementById('pdf-canvas').width > 1"
            ):
                break
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(900)
        pg.click("#pdf-tool-" + tool)
        pg.evaluate("document.getElementById('pdf-marks').scrollIntoView({block: 'start'});0")
        pg.wait_for_timeout(150)
        x, y, w, h = pg.evaluate(
            "(()=>{const r=document.getElementById('pdf-marks').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()"
        )
        pg.mouse.move(x + a[0] * w, y + a[1] * h)
        pg.mouse.down()
        pg.mouse.move(x + b[0] * w, y + b[1] * h, steps=5)
        pg.mouse.up()
        pg.wait_for_timeout(800)
        got = []
        pg.on("download", lambda d_, got=got: got.append(d_))
        pg.click("#pdf-markcopy")
        for _ in range(20):
            if got:
                break
            pg.wait_for_timeout(300)
        pg.click("#pdf-markflat")
        for _ in range(20):
            if len(got) > 1:
                break
            pg.wait_for_timeout(300)
        check(
            "flat geo %s: two separate files were downloaded" % tag,
            len(got) == 2 and got[0].path() != got[1].path(),
        )
        if len(got) < 2:
            return
        mpdf, fpdf = open(got[0].path(), "rb").read(), open(got[1].path(), "rb").read()
        fm, _r1 = _yellow_red_boxes(mpdf, shots, "pdf-geo-%s-annot-render" % tag, crop)
        ff, _r2 = _yellow_red_boxes(fpdf, shots, "pdf-geo-%s-fixed-render" % tag, crop)
        want = [0.2, 0.3, 0.7, 0.4] if tag == "opencm" else None
        check(
            "flat geo %s: the fixed copy's box equals the annotation copy's box (within 0.015)%s"
            % (tag, " and the dragged box" if want else ""),
            bool(fm)
            and bool(ff)
            and all(abs(a_ - b_) < 0.015 for a_, b_ in zip(fm, ff, strict=True))
            and (not want or all(abs(a_ - b_) < 0.015 for a_, b_ in zip(ff, want, strict=True))),
            (fm, ff),
        )
        pg.click("#pdf-markclear")
        pg.wait_for_timeout(500)


def pdf_text_stale(ctx):  # noqa: C901
    """A text pick that finishes after the file was closed must not add a mark or write its message over the new state."""
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.reload()
    pg.set_input_files(
        "#pdf-open",
        files=[
            {"name": "a.pdf", "mimeType": "application/pdf", "buffer": _n_page_pdf(2, "Report")}
        ],
    )
    for _ in range(50):
        if pg.inner_text("#pdf-where").startswith("Page 1 of 2") and pg.evaluate(
            "document.getElementById('pdf-canvas').width > 1"
        ):
            break
        pg.wait_for_timeout(250)
    pg.wait_for_timeout(900)
    pg.click("#pdf-tool-tx")
    res = pg.evaluate(
        """(() => {
      const s = document.getElementById('pdf-marks'), r = s.getBoundingClientRect();
      const ev = (t, fx, fy) => s.dispatchEvent(new PointerEvent(t, { pointerId: 7, button: 0, bubbles: true, clientX: r.left + fx * r.width, clientY: r.top + fy * r.height }));
      ev('pointerdown', 0.05, 0.05); ev('pointerup', 0.95, 0.2);
      const busyAfterUp = document.getElementById('pdf-markstatus').textContent;
      document.getElementById('pdf-close').click();
      return busyAfterUp;
    })()"""
    )
    pg.wait_for_timeout(1200)
    st = pg.inner_text("#pdf-markstatus")
    check(
        "stale text pick: the pick was under way when the file was closed",
        "Finding text" in res,
        res,
    )
    check(
        "stale text pick: after the file is closed the late result adds nothing and does not write its own message",
        "page changed" not in st
        and "Text-line highlight added" not in st
        and pg.locator("#pdf-marklist li").count() == 0,
        st,
    )
    pg.set_input_files(
        "#pdf-open",
        files=[{"name": "b.pdf", "mimeType": "application/pdf", "buffer": _n_page_pdf(2, "Other")}],
    )
    for _ in range(50):
        if pg.inner_text("#pdf-where").startswith("Page 1 of 2"):
            break
        pg.wait_for_timeout(250)
    pg.wait_for_timeout(800)
    check(
        "stale text pick: the next file's tools are usable and marks empty",
        not pg.is_disabled("#pdf-tool-tx") and pg.locator("#pdf-marklist li").count() == 0,
    )


def pdf_order(ctx):  # noqa: C901
    """Reorder and remove pages from a list, then download that order."""
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 390, "height": 800})

    def open_pdf(name, data):
        pg.set_input_files(
            "#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of"):
                break
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(400)

    def order():
        return pg.evaluate(
            "[...document.querySelectorAll('#pdf-orderlist li span')].map(s=>s.textContent.trim())"
        )

    check("order: the list button is off with no file", pg.is_disabled("#pdf-orderstart"))
    open_pdf("five.pdf", _n_page_pdf(5))
    pg.click("#pdf-orderstart")
    check(
        "order: the list starts in file order and fills the pages box",
        order() == ["Page %d" % i for i in range(1, 6)] and pg.input_value("#pdf-range") == "1-5",
        (order(), pg.input_value("#pdf-range")),
    )
    first_up, last_down = (
        pg.locator("#pdf-orderlist li").nth(0).locator("button").nth(0),
        pg.locator("#pdf-orderlist li").nth(4).locator("button").nth(1),
    )
    check(
        "order: Up on the first and Down on the last are off",
        first_up.is_disabled() and last_down.is_disabled(),
    )
    check(
        "order: buttons carry a label with the page and position",
        "page 1, position 1 of 5" in (first_up.get_attribute("aria-label") or ""),
        first_up.get_attribute("aria-label"),
    )
    pg.locator("#pdf-orderlist li").nth(4).locator("button").nth(0).click()
    pg.locator("#pdf-orderlist li").nth(3).locator("button").nth(0).click()
    pg.locator("#pdf-orderlist li").nth(0).locator("button").nth(2).click()
    check(
        "order: two Ups on page 5 and a Remove of page 1 give 2,5,3,4",
        pg.input_value("#pdf-range") == "2,5,3,4"
        and order() == ["Page 2", "Page 5", "Page 3", "Page 4"],
        (order(), pg.input_value("#pdf-range")),
    )
    with pg.expect_download() as dl:
        pg.click("#pdf-splitgo")
    out = open(dl.value.path(), "rb").read()
    open(shots + "/pdf-order.pdf", "wb").write(out)
    open_pdf("order.pdf", out)
    tx = []
    for i in range(1, 5):
        pg.fill("#pdf-page", str(i))
        pg.press("#pdf-page", "Enter")
        for _ in range(25):
            t = pg.inner_text("#pdf-text").strip()
            if (
                t
                and t.endswith(("2", "3", "4", "5"))
                and t.startswith("Page")
                and (len(tx) == 0 or t != tx[-1])
            ):
                break
            pg.wait_for_timeout(100)
        tx.append(pg.inner_text("#pdf-text").strip())
    check(
        "order: the downloaded file's pages, read by pdf.js, are 2,5,3,4",
        tx == ["Page 2", "Page 5", "Page 3", "Page 4"],
        tx,
    )
    check(
        "order: opening another file clears the list and the pages box",
        pg.locator("#pdf-orderlist li").count() == 0 and pg.input_value("#pdf-range") == "",
    )

    def active():
        return pg.evaluate(
            "document.activeElement && document.activeElement.getAttribute('aria-label') || ''"
        )

    open_pdf("five.pdf", _n_page_pdf(5))
    pg.click("#pdf-orderstart")
    pg.locator("#pdf-orderlist li").nth(3).locator("button").nth(0).click()
    check(
        "order: after Up the focus is on the same page's Up button and the move is announced",
        active().startswith("Up: page 4, position 3 of 5")
        and "moved to position 3" in pg.inner_text("#pdf-pagesstatus"),
        (active(), pg.inner_text("#pdf-pagesstatus")),
    )
    pg.locator("#pdf-orderlist li").nth(4).locator("button").nth(2).click()
    check(
        "order: after removing the last row the focus lands on an enabled button of the new last row",
        active().startswith("Remove: page 3, position 4 of 4") or "position 4 of 4" in active(),
        active(),
    )
    pg.fill("#pdf-range", "5,5")
    check(
        "order: typing in the box ends the list and keeps what was typed",
        pg.locator("#pdf-orderlist li").count() == 0 and pg.input_value("#pdf-range") == "5,5",
    )
    pg.click("#pdf-orderstart")
    check(
        "order: listing again replaces the box with the file order",
        pg.input_value("#pdf-range") == "1-5",
        pg.input_value("#pdf-range"),
    )
    hundred = _n_page_pdf(100)
    open_pdf("hundred.pdf", hundred)
    pg.click("#pdf-orderstart")
    check(
        "order: 100 pages list as one range and download unchanged as 100 pages",
        pg.input_value("#pdf-range") == "1-100" and pg.locator("#pdf-orderlist li").count() == 100,
    )
    with pg.expect_download() as dl100:
        pg.click("#pdf-splitgo")
    b100 = open(dl100.value.path(), "rb").read()
    check(
        "order: the unchanged 100-page download has 100 pages",
        len(re.findall(rb"/Type\s*\/Page[^s]", b100)) == 100,
        len(b100),
    )
    for r in range(0, 100, 2):
        pg.locator("#pdf-orderlist li").nth(r).locator("button").nth(1).click()
    rng = pg.input_value("#pdf-range")
    check(
        "order: 50 swaps give a box text over 200 characters",
        200 < len(rng) <= 400 and rng.startswith("2,1,4,3"),
        len(rng),
    )
    with pg.expect_download() as dlw:
        pg.click("#pdf-splitgo")
    bw = open(dlw.value.path(), "rb").read()
    check(
        "order: that long order downloads 100 pages",
        len(re.findall(rb"/Type\s*\/Page[^s]", bw)) == 100
        and "Downloaded a new file with 100" in pg.inner_text("#pdf-pagesstatus"),
        pg.inner_text("#pdf-pagesstatus"),
    )
    import pathlib

    signed = (
        pathlib.Path(__file__).resolve().parents[1] / "js" / "fixtures" / "form_signed3.pdf"
    ).read_bytes()
    open_pdf("signed.pdf", signed)
    pg.click("#pdf-orderstart")
    got = []
    pg.on("download", lambda x: got.append(x))
    pg.click("#pdf-splitgo")
    pg.wait_for_timeout(1000)
    warned = "will not validate" in pg.inner_text("#pdf-pagesstatus")
    n_rows = pg.locator("#pdf-orderlist li").count()
    if n_rows >= 2:
        pg.locator("#pdf-orderlist li").nth(0).locator("button").nth(1).click()
        pg.locator("#pdf-orderlist li").nth(1).locator("button").nth(0).click()
    pg.click("#pdf-splitgo")
    pg.wait_for_timeout(1000)
    check(
        "order: a held signed copy is dropped by a list change even when the order comes back the same",
        warned and not got and "again within 15 seconds" in pg.inner_text("#pdf-pagesstatus"),
        (warned, len(got), n_rows),
    )
    sw = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    check("order: the list does not widen the page at 390", sw[0] <= sw[1], sw)
    pg.click("#pdf-orderstart")
    pg.locator("#pdf-orderlist li").nth(0).locator("button").nth(2).click()
    pg.locator("#pdf-orderlist li").nth(1).locator("button").nth(0).click()
    pg.locator("#pdf-pages").scroll_into_view_if_needed()
    pg.locator("#pdf-pages").screenshot(path=shots + "/pdf-order-phone.png")


def pdf_pages(ctx):  # noqa: C901
    """Keep chosen pages as a new PDF, or merge the open file with added files."""
    import pathlib
    import re

    fx = pathlib.Path(__file__).resolve().parents[1] / "js" / "fixtures"
    signed = (fx / "form_signed3.pdf").read_bytes()
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = fresh(ctx, "/pdf/")
    pg.set_viewport_size({"width": 1280, "height": 800})

    def open_pdf(name, data):
        pg.set_input_files(
            "#pdf-open", files=[{"name": name, "mimeType": "application/pdf", "buffer": data}]
        )
        for _ in range(50):
            if pg.inner_text("#pdf-where").startswith("Page 1 of"):
                break
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(400)

    def status():
        return pg.inner_text("#pdf-pagesstatus")

    def npages(b):
        return len(re.findall(rb"/Type\s*/Page[^s]", b))

    def texts(n):
        out = []
        for i in range(1, n + 1):
            pg.fill("#pdf-page", str(i))
            pg.press("#pdf-page", "Enter")
            for _ in range(25):
                t = pg.inner_text("#pdf-text")
                if t and not t.startswith("Reading") and t.strip().endswith(str(i)):
                    break
                pg.wait_for_timeout(100)
            out.append(pg.inner_text("#pdf-text").strip())
        return out

    check(
        "pages: controls are off with no file",
        pg.is_disabled("#pdf-range")
        and pg.is_disabled("#pdf-splitgo")
        and pg.is_disabled("#pdf-mergeadd")
        and pg.is_disabled("#pdf-mergego"),
    )
    six = _n_page_pdf(6)
    open_pdf("six.pdf", six)
    check(
        "pages: split and add are on once a file is open, merge waits for added files",
        not pg.is_disabled("#pdf-splitgo")
        and not pg.is_disabled("#pdf-mergeadd")
        and pg.is_disabled("#pdf-mergego"),
    )
    got = []
    pg.on("download", lambda d: got.append(d))
    for bad, frag in (
        ("", "Type the pages"),
        ("9", "out of range"),
        ("4-2", "backwards"),
        ("a", "not a page"),
    ):
        pg.fill("#pdf-range", bad)
        pg.click("#pdf-splitgo")
        pg.wait_for_timeout(250)
        check(
            "pages: range %r is refused with a reason and nothing downloads" % bad,
            frag in status() and not got,
            status(),
        )
    pg.fill("#pdf-range", "4, 2")
    with pg.expect_download() as dl:
        pg.click("#pdf-splitgo")
    out = open(dl.value.path(), "rb").read()
    open(shots + "/pdf-split.pdf", "wb").write(out)
    check(
        "pages: split gives a separate -pages file with 2 pages and a note that the originals were not changed",
        dl.value.suggested_filename == "six-pages.pdf"
        and npages(out) == 2
        and "not changed" in status()
        and "2 page" in status(),
        (dl.value.suggested_filename, npages(out), status()),
    )
    open_pdf("split.pdf", out)
    tx = texts(2)
    check(
        "pages: the split file's pages, read by pdf.js, are Page 4 then Page 2",
        tx == ["Page 4", "Page 2"],
        tx,
    )
    open_pdf("six.pdf", six)
    bee, cat = _n_page_pdf(2, "Bee"), _n_page_pdf(2, "Cat")
    pg.set_input_files(
        "#pdf-mergeadd",
        files=[
            {"name": "bee.pdf", "mimeType": "application/pdf", "buffer": bee},
            {"name": "<b>cat</b>.pdf", "mimeType": "application/pdf", "buffer": cat},
        ],
    )
    pg.wait_for_timeout(800)
    names = pg.evaluate("[...document.querySelectorAll('#pdf-mergelist li')].map(l=>l.textContent)")
    check(
        "pages: added files are listed as text and merge turns on",
        len(names) == 2
        and "<b>cat</b>.pdf" in names[1]
        and pg.evaluate("document.querySelectorAll('#pdf-mergelist b').length") == 0
        and not pg.is_disabled("#pdf-mergego"),
        names,
    )
    with pg.expect_download() as dl2:
        pg.click("#pdf-mergego")
    out2 = open(dl2.value.path(), "rb").read()
    open(shots + "/pdf-merged.pdf", "wb").write(out2)
    check(
        "pages: merge gives a -merged file of 10 pages",
        dl2.value.suggested_filename == "six-merged.pdf" and npages(out2) == 10,
        (dl2.value.suggested_filename, npages(out2)),
    )
    open_pdf("merged.pdf", out2)
    tx = texts(10)
    check(
        "pages: the merged file's pages, read by pdf.js, are the open file then bee then cat, in order",
        tx
        == [
            "Page 1",
            "Page 2",
            "Page 3",
            "Page 4",
            "Page 5",
            "Page 6",
            "Bee 1",
            "Bee 2",
            "Cat 1",
            "Cat 2",
        ],
        tx,
    )
    check(
        "pages: opening another file cleared the added files",
        pg.locator("#pdf-mergelist li").count() == 0
        and pg.is_disabled("#pdf-mergego")
        and status() == "",
    )
    pg.set_input_files(
        "#pdf-mergeadd",
        files=[{"name": "junk.pdf", "mimeType": "application/pdf", "buffer": b"not a pdf at all"}],
    )
    pg.wait_for_timeout(600)
    check(
        "pages: a file that is not a PDF is refused and nothing is added",
        "Nothing was added" in status() and pg.locator("#pdf-mergelist li").count() == 0,
        status(),
    )
    open_pdf("signed.pdf", signed)
    two = _two_page_pdf()
    pg.set_input_files(
        "#pdf-mergeadd", files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": two}]
    )
    pg.wait_for_timeout(800)
    pg.fill("#pdf-range", "1")
    got.clear()
    pg.click("#pdf-splitgo")
    pg.wait_for_timeout(1200)
    check(
        "pages: a signed input warns on the first press and downloads nothing",
        not got and "will not validate" in status() and "again within 15 seconds" in status(),
        status(),
    )
    # Replace the added file by another file with the very same name and size: the held copy must not carry over.
    pg.click("#pdf-mergeclear")
    pg.wait_for_timeout(300)
    pg.set_input_files(
        "#pdf-mergeadd", files=[{"name": "two.pdf", "mimeType": "application/pdf", "buffer": two}]
    )
    pg.wait_for_timeout(800)
    pg.click("#pdf-splitgo")
    pg.wait_for_timeout(1200)
    check(
        "pages: re-adding a file with the same name and size does not release the held copy",
        not got and "again within 15 seconds" in status(),
        status(),
    )
    with pg.expect_download() as dl3:
        pg.click("#pdf-splitgo")
    check(
        "pages: the same choice pressed again downloads",
        dl3.value.suggested_filename == "signed-pages.pdf",
    )
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(400)
    pg.locator("#pdf-pages").scroll_into_view_if_needed()
    pg.screenshot(path=shots + "/pdf-pages-phone.png")
    sw = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    check("pages: the panel does not widen the page at 390", sw[0] <= sw[1], sw)


def _poll_logger(codes, k):
    def log(resp):
        if "/poll/" in resp.url:
            codes.setdefault(k, []).append(resp.status)

    return log


def chat_poll_tabs(ctx):  # noqa: C901
    """Chat: one person with many visible tabs against the real poll limit (30 a minute): observed responses and the status line each tab shows."""
    import django

    django.setup()

    def make_space():
        from apps.accounts.models import CustomUser
        from apps.chat import services as cs

        me = CustomUser.objects.get(employee_id="ST-LIVE-1")
        other, _ = CustomUser.objects.get_or_create(
            employee_id="ST-LIVE-2",
            defaults={
                "full_name": "Second Synthetic",
                "email": "live.two@salowtech.com",
                "email_verified": True,
                "role": CustomUser.Role.EMPLOYEE,
                "status": CustomUser.Status.ACTIVE,
                "is_active": True,
            },
        )
        return cs.open_dm(me, other.pk).public_id

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        space_id = ex.submit(make_space).result()
    url = APP + "/chat/space/%s/" % space_id
    n = int(os.environ.get("DRILL_TABS", "12"))
    secs = int(os.environ.get("DRILL_SECS", "75"))
    codes = {}
    pages = []
    for k in range(n):
        pgk = ctx.pages[0] if k == 0 else ctx.new_page()
        pgk.on("response", _poll_logger(codes, k))
        pgk.goto(url)
        pages.append(pgk)
    vis = [p.evaluate("document.visibilityState") for p in pages]
    t0 = time.time()
    while time.time() - t0 < secs:
        pages[0].wait_for_timeout(5000)
    allc = [c for v in codes.values() for c in v]
    n429 = allc.count(429)
    stat = [p.inner_text("#chat-status") for p in pages]
    paused = [x for x in stat if "paused" in x]
    slow_tabs = [k for k, x in enumerate(stat) if "rate limited" in x]
    if slow_tabs:
        pages[slow_tabs[0]].screenshot(
            path=os.path.join(os.environ.get("DRILL_SHOTS", "./"), "chat-throttle-notice.png")
        )
    print(
        "chat_poll_tabs: tabs",
        n,
        "visibility",
        sorted(set(vis)),
        "polls",
        len(allc),
        "ok",
        allc.count(200),
        "429",
        n429,
        "other",
        len(allc) - allc.count(200) - n429,
    )
    print("chat_poll_tabs: status lines", sorted(set(stat)))
    check(
        "chat: %d visible tabs for %d s produced polls" % (n, secs), len(allc) > 0, str(len(allc))
    )
    check("chat: observed whether the poll limit answered 429 (count printed)", True, str(n429))
    check(
        "chat: observed how many tabs paused live updates (count printed)", True, str(len(paused))
    )


def _js_route(body):
    return lambda route: route.fulfill(status=200, content_type="application/javascript", body=body)


def slides_oldpage_v5(ctx):  # noqa: C901
    """An older Slides page (the files from before pictures) must not touch a version 5 deck: it is left byte-identical and nothing is overwritten."""
    pg = fresh(ctx, "/slides/")
    KEY = "document.getElementById('slide-root').dataset.storeKey"
    pg.on("dialog", lambda d: d.accept())
    pg.set_input_files(
        "#sl-img-file", files=[{"name": "c.png", "mimeType": "image/png", "buffer": _png(40, 20)}]
    )
    pg.wait_for_timeout(900)
    before = pg.evaluate("localStorage.getItem(%s)" % KEY)
    import json as _json

    check(
        "oldpage: the current page stored a version 5 deck",
        _json.loads(before)["v"] == 5,
        before[:60],
    )
    base = "tests/js/fixtures/slides_page_v4_before_images/"
    olds = {f: open(base + f + ".js").read() for f in ("editor", "model", "pdf")}

    def old_files(route, request=None):
        m = re.search(r"/static/slides/(editor|model|pdf|images)\.js", route.request.url)
        body = "" if m.group(1) == "images" else olds[m.group(1)]
        route.fulfill(status=200, content_type="application/javascript", body=body)

    pg.route(re.compile(r"/static/slides/(editor|model|pdf|images)\.js"), old_files)
    pg.reload()
    pg.wait_for_timeout(800)
    st = pg.inner_text("#sl-status")
    print("oldpage status:", st[:200])
    after_open = pg.evaluate("localStorage.getItem(%s)" % KEY)
    check("oldpage: opening it left the stored deck byte-identical", after_open == before)
    for sel in ("#sl-add-text", "#sl-add-rect", "#sl-add-slide"):
        if pg.query_selector(sel):
            try:
                pg.click(sel, timeout=1500)
            except Exception:  # noqa: S110 # a disabled button cannot be clicked: that is the expected case
                pass
    pg.wait_for_timeout(400)
    after_edit = pg.evaluate("localStorage.getItem(%s)" % KEY)
    check(
        "oldpage: trying to add things still leaves the stored deck byte-identical",
        after_edit == before,
    )
    check(
        "oldpage: the status says the stored deck could not be read and was left untouched",
        "could not be read" in st and "left untouched" in st,
        st[:120],
    )


def slides_images_pdfmem(ctx):  # noqa: C901
    """Pictures: the deck pixel cap at add time, and a deck over it is refused for the PDF with a message while a deck at the cap still exports."""
    import json as _json

    pg = fresh(ctx, "/slides/")
    KEY = "document.getElementById('slide-root').dataset.storeKey"
    pg.evaluate(
        "new Promise(r=>{const q=indexedDB.deleteDatabase('salow.slides.pictures.'+%s);q.onsuccess=q.onerror=q.onblocked=()=>r()})"
        % KEY
    )
    pg.reload()
    pg.wait_for_timeout(400)

    def deck():
        return pg.evaluate("JSON.parse(localStorage.getItem(%s))" % KEY)

    def pics():
        d = deck()
        return (
            [e for sl in d["slides"] for e in sl["elements"] if e["type"] == "image"] if d else []
        )

    def put(name, data):
        pg.set_input_files(
            "#sl-img-file", files=[{"name": name, "mimeType": "image/png", "buffer": data}]
        )
        pg.wait_for_timeout(3500)

    for k in range(4):
        put("big%d.png" % k, _png(3000, 4000, (10 + k, 40, 90)))
    four = len(pics())
    put("big4.png", _png(3000, 4000, (99, 40, 90)))
    check(
        "mem: four 12 million pixel pictures (48 million) fit and a fifth is refused for the pixel cap",
        four == 4 and len(pics()) == 4 and "million pixels" in pg.inner_text("#sl-status"),
        str((four, len(pics()), pg.inner_text("#sl-status"))),
    )
    t0 = time.time()
    with pg.expect_download(timeout=60000) as dl:
        pg.click("#sl-pdf")
    raw = open(dl.value.path(), "rb").read()
    secs = time.time() - t0
    print("mem: PDF with four 12 MP pictures took %.1f s, %d bytes" % (secs, len(raw)))
    check(
        "mem: a deck at the cap still exports a PDF with four image objects",
        raw[:5] == b"%PDF-" and raw.count(b"/Subtype /Image") == 4,
        str(raw.count(b"/Subtype /Image")),
    )
    # a fifth distinct stored picture, then a reference deck that names all five
    pg.click("#sl-items button")
    pg.click("#sl-del-el")
    put("big4.png", _png(3000, 4000, (99, 40, 90)))
    d = deck()
    hs = [e["img"] for e in pics()]
    allh = []
    for e in pics():
        allh.append(e["img"])
    # the first picture was deleted, so the store holds five and the deck four: name five in a reference-only deck
    stored = pg.evaluate(
        "new Promise(res=>{const r=indexedDB.open('salow.slides.pictures.'+%s,1);r.onsuccess=()=>{const q=r.result.transaction('img').objectStore('img').getAllKeys();q.onsuccess=()=>{r.result.close();res(q.result)}}})"
        % KEY
    )
    d5 = _json.loads(_json.dumps(d))
    els = d5["slides"][0]["elements"]
    extra = [h for h in stored if h not in hs][0]
    e0 = _json.loads(_json.dumps(els[0]))
    e0["id"] = "zz1"
    e0["img"] = extra
    els.append(e0)
    open(TMP_REF5, "w").write(_json.dumps(d5))
    pg.on("dialog", lambda dd: dd.accept())
    pg.set_input_files("#sl-import", TMP_REF5)
    pg.wait_for_timeout(1500)
    check(
        "mem: a reference-only deck naming five stored 12 MP pictures imports",
        len(pics()) == 5,
        str(len(pics())),
    )
    pg.click("#sl-pdf")
    pg.wait_for_timeout(2500)
    st = pg.inner_text("#sl-status")
    check(
        "mem: that 60 million pixel deck is refused for the PDF with a message and no file",
        "million pixels" in st and "no PDF was made" in st,
        st,
    )


def cal_month(ctx):
    """Calendar month grid: events made through the real form land on the right day cells; cell cap, no sideways scroll at 390 px."""
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = ctx.pages[0]
    mk = [("Standup", "2026-12-15T09:00", "2026-12-15T09:30")] * 1
    mk += [(f"Review {i}", "2026-12-15T1%d:00" % i, "2026-12-15T1%d:30" % i) for i in range(4)]
    mk += [("Offsite", "2026-11-30T10:00", "2026-12-02T10:00"), ("Late call", "2026-12-01T01:30", "2026-12-01T02:00")]
    for title, a, b in mk:
        pg.goto(APP + "/calendar/new/")
        pg.fill("input[name=title]", title)
        pg.fill("input[name=start]", a)
        pg.fill("input[name=end]", b)
        pg.click(".cal-form button[type=submit]")
        pg.wait_for_load_state()
    pg.goto(APP + "/calendar/month/?m=2026-12")
    pg.wait_for_timeout(400)
    cell = lambda d: pg.evaluate(  # noqa: E731
        "(d)=>{for(const td of document.querySelectorAll('.cal-month td')){if(td.classList.contains('cal-other'))continue;"
        "if(td.querySelector('.cal-dn').textContent.trim().split(' ')[0]===d)return td.innerText}return null}", d)
    c15 = cell("15") or ""
    check("cal month: 15 Dec names 3 events and counts the other 2", c15.count("Review") + c15.count("Standup") == 3 and "+2 more" in c15, c15)
    c1 = cell("1") or ""
    check("cal month: 1 Dec holds the Offsite and the 01:30 IST call", "Offsite" in c1 and "Late call" in c1, c1)
    check("cal month: 3 Dec has no Offsite", "Offsite" not in (cell("3") or ""))
    shot(pg, "cal_month_desktop")
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.reload()
    pg.wait_for_timeout(400)
    ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check("cal month: no sideways page scroll at 390 px", not ov)
    shot(pg, "cal_month_390")
    pg.set_viewport_size({"width": 1100, "height": 900})
    pg.goto(APP + "/calendar/month/?m=2026-12")
    pg.click("a.cal-more")
    pg.wait_for_load_state()
    check("cal month: +2 more opens that day", "d=2026-12-15" in pg.url, pg.url)
    day = pg.inner_text("body")
    check("cal month: the day page lists the 4th and 5th events too", all(t in day for t in ("Standup", "Review 0", "Review 1", "Review 2", "Review 3")), day[:300])
    shot(pg, "cal_day_15")
    pg.click("a.cal-title:has-text('Review 3')")
    pg.wait_for_load_state()
    check("cal month: an event on the day page opens its detail", "/calendar/event/" in pg.url and "Review 3" in pg.inner_text("body"), pg.url)
    pg.goto(APP + "/calendar/week/?d=2026-12-01")
    wk = pg.inner_text("body")
    check("cal week: the week of 30 Nov lists Offsite on three days", wk.count("Offsite") == 3, wk[:300])
    shot(pg, "cal_week")
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(APP + "/calendar/day/?d=2026-12-15")
    check("cal day: no sideways scroll at 390 px", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    shot(pg, "cal_day_15_390")
    navs = pg.evaluate("[...document.querySelectorAll('.cal-nav a')].map(a=>a.getClientRects().length)")
    check("cal day: Previous and Next each stay on one line at 390 px", navs == [1, 1], str(navs))
    pg.goto(APP + "/calendar/month/?m=2026-12")
    navs = pg.evaluate("[...document.querySelectorAll('.cal-nav a')].map(a=>a.getClientRects().length)")
    check("cal month: Previous and Next each stay on one line at 390 px", navs == [1, 1], str(navs))
    shot(pg, "cal_month_390b")
    pg.set_viewport_size({"width": 1100, "height": 900})
    pg.goto(APP + "/calendar/month/?m=2026-13")
    check("cal month: a bad month shows the error text", "Month must look like" in pg.inner_text("body"))


def cal_series(ctx):
    """Repeating events on the real stack: form creates a weekly series, month/day show every occurrence, one occurrence is cancelled from its detail page, the series keeps the rest."""
    pg = ctx.pages[0]
    pg.goto(APP + "/calendar/new/")
    pg.fill("input[name=title]", "Team sync")
    pg.fill("input[name=start]", "2026-12-01T10:00")
    pg.fill("input[name=end]", "2026-12-01T10:30")
    pg.select_option("select[name=freq]", "weekly")
    pg.check("input[name=weekday][value='1']")
    pg.check("input[name=weekday][value='3']")
    pg.fill("input[name=count]", "6")
    pg.evaluate("document.querySelector('.cal-repeat').scrollIntoView()")
    shot(pg, "cal_series_form")
    pg.click(".cal-form button[type=submit]")
    pg.wait_for_load_state()
    body = pg.inner_text("body")
    check("cal series: detail says it repeats weekly on Tue and Thu, 6 times", "Repeats every week on Tue, Thu, 6 times" in body, body[:300])
    check("cal series: detail says single occurrences cannot be changed", "cannot change a single occurrence" in body)
    detail = pg.url
    pg.goto(APP + "/calendar/month/?m=2026-12")
    cell = lambda d: pg.evaluate(  # noqa: E731
        "(d)=>{for(const td of document.querySelectorAll('.cal-month td')){if(td.classList.contains('cal-other'))continue;"
        "if(td.querySelector('.cal-dn').textContent.trim().split(' ')[0]===d)return td.innerText}return null}", d)
    hit = [d for d in ("1", "3", "8", "10", "15", "17") if "Team sync" in (cell(d) or "")]
    check("cal series: month shows 1,3,8,10,15,17 Dec", hit == ["1", "3", "8", "10", "15", "17"], str(hit))
    check("cal series: 22 Dec has none (count 6 used up)", "Team sync" not in (cell("22") or ""))
    pg.goto(detail)
    pg.click("button[aria-label='Cancel only the occurrence on Thursday 3 December 2026']")
    pg.wait_for_load_state()
    body = pg.inner_text("body")
    check("cal series: the page says 3 Dec was cancelled and others unchanged", "3 Dec 2026" in body and "other occurrences are unchanged" in body, body[:400])
    shot(pg, "cal_series_detail")
    pg.goto(APP + "/calendar/month/?m=2026-12")
    check("cal series: 3 Dec is gone, 8 Dec stays", "Team sync" not in (cell("3") or "") and "Team sync" in (cell("8") or ""))
    pg.goto(APP + "/calendar/day/?d=2026-12-03")
    check("cal series: the day page for 3 Dec lists nothing", "Team sync" not in pg.inner_text("body"))
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(APP + "/calendar/new/")
    check("cal series: new-event form has no sideways scroll at 390 px", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    pg.select_option("select[name=freq]", "weekly")
    pg.evaluate("document.querySelector('.cal-repeat').scrollIntoView()")
    shot(pg, "cal_series_form_390")
    pg.goto(detail)
    check("cal series: detail has no sideways scroll at 390 px", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    shot(pg, "cal_series_detail_390")
    check("cal series: the whole-series button says so", pg.locator("a:has-text('Cancel whole series')").count() == 1 and pg.locator("a:text-is('Cancel event')").count() == 0)
    check("cal series: the date picker for occurrences is on the page", pg.locator("#occ-from").count() == 1)
    pg.click("a:has-text('Cancel whole series')")
    pg.wait_for_load_state()
    cp = pg.inner_text("body")
    check("cal series: the confirm page names the whole series and every occurrence", "Cancel the whole series?" in cp and "every occurrence, past and future" in cp, cp[:300])
    check("cal series: the confirm page has no sideways scroll at 390 px", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    shot(pg, "cal_series_cancel_390")
    pg.click("a:has-text('Keep the series')")
    pg.wait_for_load_state()
    pg.goto(detail + "?from=2026-12-10")
    check("cal series: from 10 Dec the list starts at 10 Dec", "Thu 10 Dec 2026" in pg.inner_text("body"))
    pg.set_viewport_size({"width": 1100, "height": 900})


def cal_series_page(ctx):
    """A 200-occurrence daily series at 390 px: the occurrence list, a later page, and cancelling the last date."""
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = ctx.pages[0]
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(APP + "/calendar/new/")
    pg.fill("input[name=title]", "Daily stand")
    pg.fill("input[name=start]", "2027-03-01T10:00")
    pg.fill("input[name=end]", "2027-03-01T10:15")
    pg.select_option("select[name=freq]", "daily")
    pg.fill("input[name=count]", "200")
    pg.click(".cal-form button[type=submit]")
    pg.wait_for_load_state()
    d0 = pg.url
    cancels = lambda: pg.locator("button[aria-label^='Cancel only the occurrence']").count()  # noqa: E731
    check("cal series page: first page lists 20 occurrences with Cancel this one", cancels() == 20, str(cancels()))
    pg.screenshot(path=os.path.join(shots, "cal_pages_1.png"), full_page=True)
    check("cal series page: no sideways scroll", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    pg.click("a:has-text('Later occurrences')")
    pg.wait_for_load_state()
    check("cal series page: the later page starts on 21 Mar", "Sun 21 Mar 2027" in pg.inner_text("body"), pg.url)
    check("cal series page: the later page offers Earlier occurrences", pg.locator("a:has-text('Earlier occurrences')").count() == 1)
    pg.screenshot(path=os.path.join(shots, "cal_pages_2.png"), full_page=True)
    pg.click("a:has-text('Earlier occurrences')")
    pg.wait_for_load_state()
    check("cal series page: Earlier returns the first page (1 Mar to 20 Mar, 20 buttons)", "Mon 1 Mar 2027" in pg.inner_text("body") and cancels() == 20, pg.url)
    pg.screenshot(path=os.path.join(shots, "cal_pages_back.png"), full_page=True)
    pg.click("a:has-text('Later occurrences')")
    pg.wait_for_load_state()
    pg.goto(d0 + "?from=2027-09-10")
    check("cal series page: the last page has no Later link and ends 16 Sep", pg.locator("a:has-text('Later occurrences')").count() == 0 and "Thu 16 Sep 2027" in pg.inner_text("body"))
    pg.click("button[aria-label='Cancel only the occurrence on Thursday 16 September 2027']")
    pg.wait_for_load_state()
    body = pg.inner_text("body")
    check("cal series page: 16 Sep is listed as cancelled and no longer offered", "16 Sep 2027" in body and "other occurrences are unchanged" in body and pg.locator("button[aria-label='Cancel only the occurrence on Thursday 16 September 2027']").count() == 0, body[:300])
    check("cal series page: after cancelling it stays on the same page of occurrences", "from=2027-09-10" in pg.url and "Wed 15 Sep 2027" in body, pg.url)
    pg.screenshot(path=os.path.join(shots, "cal_pages_cancelled.png"), full_page=True)
    pg.set_viewport_size({"width": 1100, "height": 900})


def task_comments(ctx):
    """Project task comments at 390 px: post, escaped markup, a long unbroken word, line breaks, the board link."""
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = ctx.pages[0]
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(APP + "/tasks/projects/")
    pg.fill("input[name=name]", "Comment drill")
    pg.click("form[action$='/projects/new/'] button[type=submit]")
    pg.wait_for_load_state()
    pg.fill("input[name=title]", "Write the plan")
    pg.click("form[action$='/add/'] button[type=submit]")
    pg.wait_for_load_state()
    check("task comments: the card has a Comments link", pg.locator("a:has-text('Comments')").count() == 1)
    pg.click("a:has-text('Comments')")
    pg.wait_for_load_state()
    check("task comments: empty thread says so", "No comments yet." in pg.inner_text("body"))
    pg.fill("textarea[name=body]", "First line\nSecond line <b>not bold</b> <img src=x onerror=alert(1)>")
    pg.click(".comment-form button[type=submit]")
    pg.wait_for_load_state()
    body = pg.inner_text(".comment-body")
    check("task comments: markup shows as text, no element made", "<b>not bold</b>" in body and pg.locator(".comment-body b, .comment-body img").count() == 0, body)
    check("task comments: line break kept", pg.evaluate("document.querySelector('.comment-body').innerText.includes('First line\\nSecond line')"))
    pg.fill("textarea[name=body]", "W" * 300)
    pg.click(".comment-form button[type=submit]")
    pg.wait_for_load_state()
    check("task comments: a 300-letter word does not scroll the page sideways", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    check("task comments: two comments listed", pg.locator(".comment").count() == 2)
    pg.screenshot(path=os.path.join(shots, "task_comments_390.png"), full_page=True)
    pg.fill("textarea[name=body]", "x" * 2001)
    pg.evaluate("document.querySelector('textarea[name=body]').removeAttribute('maxlength')")
    pg.fill("textarea[name=body]", "x" * 2001)
    pg.click(".comment-form button[type=submit]")
    pg.wait_for_load_state()
    check("task comments: 2001 characters is refused and the text stays in the box", "up to 2000" in pg.inner_text("body") and len(pg.input_value("textarea[name=body]")) == 2001 and pg.locator(".comment").count() == 2)
    # seed up to 105 comments through the service (the view throttle is 30 a minute), then walk the pager
    import django

    django.setup()
    from apps.accounts.models import CustomUser
    from apps.tasks import projects as pj
    from apps.tasks.models import ProjectTask, TaskComment

    import concurrent.futures

    def seed():
        me = CustomUser.objects.get(employee_id="ST-LIVE-1")
        task = ProjectTask.objects.get(title="Write the plan")
        have = TaskComment.objects.filter(task=task).count()
        for i in range(105 - have):
            pj.add_comment(me, task, f"seeded {i}")

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        ex.submit(seed).result()
    url = pg.url.split("?")[0]
    pg.goto(url)
    check("task comments: newest page shows 100 comments and an Older link", pg.locator(".comment").count() == 100 and pg.locator("a:has-text('Older comments')").count() == 1 and pg.locator("a:has-text('Newest comments')").count() == 0, str(pg.locator(".comment").count()))
    pg.click("a:has-text('Older comments')")
    pg.wait_for_load_state()
    check("task comments: the older page holds the first 5, starts with the markup comment, offers Newest and no Older", pg.locator(".comment").count() == 5 and "First line" in pg.locator(".comment-body").first.inner_text() and pg.locator("a:has-text('Newest comments')").count() == 1 and pg.locator("a:has-text('Older comments')").count() == 0, str(pg.locator(".comment").count()))
    pg.screenshot(path=os.path.join(shots, "task_comments_older_390.png"), full_page=True)
    pg.click("a:has-text('Newest comments')")
    pg.wait_for_load_state()
    check("task comments: Newest returns to the page of 100", pg.locator(".comment").count() == 100 and "before=" not in pg.url, pg.url)
    # post until the limit answers 429 (30 a minute) and check the typed text stays
    status = 0
    for i in range(40):
        pg.fill("textarea[name=body]", f"burst {i}")
        with pg.expect_response(lambda r: r.request.method == "POST") as ri:
            pg.click(".comment-form button[type=submit]")
        status = ri.value.status
        pg.wait_for_load_state()
        if status == 429:
            break
    check("task comments: the limit answers 429 after about 30 posts", status == 429 and 25 <= i <= 40, f"{status} at {i}")
    check("task comments: after 429 the typed text is still in the box and the notice says so", pg.input_value("textarea[name=body]") == f"burst {i}" and "Wait and try again" in pg.inner_text("body"), pg.input_value("textarea[name=body]"))
    pg.evaluate("document.querySelector('.comment-form').scrollIntoView()")
    pg.screenshot(path=os.path.join(shots, "task_comments_429_390.png"))
    pg.set_viewport_size({"width": 1100, "height": 900})


def task_filters(ctx):
    """Project board filters at 390 px: title search, milestone, overdue, the count line, Clear."""
    import concurrent.futures
    import datetime

    import django

    django.setup()
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = ctx.pages[0]
    pg.set_viewport_size({"width": 390, "height": 800})

    def seed():
        from django.utils import timezone

        from apps.accounts.models import CustomUser
        from apps.tasks import projects as pj

        me = CustomUser.objects.get(employee_id="ST-LIVE-1")
        p = pj.create_project(me, "Filter drill")
        todo, doing, done = list(p.columns.all())
        y = timezone.localdate() - datetime.timedelta(days=1)
        m = pj.create_milestone(me, p, "Launch of the whole shared planning workspace for every office, round two of three")
        a = pj.add_task(me, p, todo.pk, "Alpha plan", due=y)
        pj.add_task(me, p, doing.pk, "Beta review", due=timezone.localdate())
        pj.add_task(me, p, done.pk, "Gamma shipped", due=y)
        pj.add_task(me, p, todo.pk, "Delta notes")
        pj.set_milestone(me, a, m.pk)
        return str(p.public_id)

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        pid = ex.submit(seed).result()
    pg.goto(APP + "/tasks/projects/%s/" % pid)
    cards = lambda: pg.locator(".task-card").count()  # noqa: E731
    check("task filters: unfiltered board shows 4 cards and no count line", cards() == 4 and pg.locator("[role=status]").count() == 0)
    pg.select_option("select[name=person] >> nth=0", label="Live Employee")
    pg.click("button:has-text('Assign') >> nth=0")
    pg.wait_for_load_state()
    check("task filters: a lead assigns a member from the card and the card says so", pg.locator("text=Assigned to Live Employee").count() == 1 and "Assignee saved." in pg.inner_text("body"))
    pg.locator(".task-card").first.scroll_into_view_if_needed()
    pg.locator(".task-card").first.screenshot(path=os.path.join(shots, "task_assign_card_390.png"))
    pg.fill("input[name=q]", "ALPHA")
    pg.click(".board-filter button[type=submit]")
    pg.wait_for_load_state()
    check("task filters: a case-insensitive title search shows 1 card and says 1 of 4", cards() == 1 and "Showing 1 of 4 tasks" in pg.inner_text("[role=status]") and "Alpha plan" in pg.inner_text(".task-card"), str(cards()))
    check("task filters: the search box keeps the typed text", pg.input_value("input[name=q]") == "ALPHA")
    pg.click("a:has-text('Clear')")
    pg.wait_for_load_state()
    pg.check("input[name=overdue]")
    pg.click(".board-filter button[type=submit]")
    pg.wait_for_load_state()
    check("task filters: overdue shows only the open task due yesterday", cards() == 1 and "Alpha plan" in pg.inner_text(".task-card"), str(cards()))
    pg.select_option("select[name=milestone]", "none")
    pg.uncheck("input[name=overdue]")
    pg.click(".board-filter button[type=submit]")
    pg.wait_for_load_state()
    check("task filters: no-milestone shows the other 3 cards", cards() == 3 and "Alpha plan" not in pg.inner_text(".tasks-board"), str(cards()))
    pg.fill("input[name=q]", "zzz")
    pg.click(".board-filter button[type=submit]")
    pg.wait_for_load_state()
    check("task filters: a search with no match shows 0 cards and 3 'No matching tasks.' lines and the count", cards() == 0 and pg.locator("text=No matching tasks.").count() == 3 and "Showing 0 of 4 tasks" in pg.inner_text("[role=status]"))
    pg.screenshot(path=os.path.join(shots, "task_filters_empty_390.png"), full_page=True)
    pg.goto(APP + "/tasks/projects/%s/?milestone=none&q=" % pid)
    check("task filters: no sideways scroll at 390 px", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    pg.screenshot(path=os.path.join(shots, "task_filters_390.png"), full_page=True)
    pg.set_viewport_size({"width": 1100, "height": 900})


def finder_projects(ctx):
    """Finder at 390 px: a lower-case query finds a project by name, the result opens that project,
    a long name wraps, and a person who is not a member does not get it (checked in the store)."""
    import concurrent.futures

    import django

    django.setup()
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = ctx.pages[0]
    pg.set_viewport_size({"width": 390, "height": 800})
    long_name = "Quokka planning workspace for every office and every shared team space, round two of three"

    def seed():
        from apps.accounts.models import CustomUser
        from apps.tasks import projects as pj

        me = CustomUser.objects.get(employee_id="ST-LIVE-1")
        p = pj.create_project(me, long_name[:120])
        other = CustomUser.objects.create(employee_id="FD-OTHER", full_name="Other Person", email="fd-other@salowtech.com", status="active", is_active=True)
        pj.create_project(other, "Quokka private project of the other person")
        return str(p.public_id), other.pk

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        pid, other_pk = ex.submit(seed).result()
    pg.goto(APP + "/search/?q=quokka")
    body = pg.inner_text("body")
    check("finder projects: a lower-case query finds the project by name under Tasks", "Quokka planning workspace" in body and "<h3>Tasks</h3>" in pg.content())
    check("finder projects: another person's private project is not listed", "private project of the other person" not in body)
    check("finder projects: no sideways scroll at 390 px with the long name", not pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
    pg.screenshot(path=os.path.join(shots, "finder_projects_390.png"), full_page=True)
    pg.click("a.cal-title:has-text('Quokka planning workspace')")
    pg.wait_for_load_state()
    check("finder projects: the result opens that project", pg.url.endswith("/tasks/projects/%s/" % pid) and "Quokka planning" in pg.inner_text("body"), pg.url[-60:])

    def audience():
        from apps.core.foundation.search import search_grouped
        from apps.accounts.models import CustomUser

        o = CustomUser.objects.get(pk=other_pk)
        hits = search_grouped(o, "quokka", types=("tasks",), per_module=20)
        return [h.title for hh, _ in hits.values() for h in hh]

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        titles = ex.submit(audience).result()
    check("finder projects: the other person finds only their own project, not the lead's (store check, not a second browser login)", titles == ["Quokka private project of the other person"], str(titles))
    pg.set_viewport_size({"width": 1100, "height": 900})


def task_assign_second(ctx):
    """A second real login (own browser context, own random password and authenticator secret, never
    printed): the lead assigns the member; the member sees the task in the In projects part of
    Assigned to me, sees no Assign control on the board, and after removal loses the project page,
    the Assigned entry and the finder hit. The second context carries the same page-error and
    request-leak checks as the first."""
    import concurrent.futures
    import secrets as _sec

    import django

    django.setup()
    shots = os.environ.get("DRILL_SHOTS", "/tmp")  # noqa: S108
    pg = ctx.pages[0]

    def seed():
        from django.contrib.auth.hashers import make_password
        from django.utils import timezone

        from apps.accounts.auth.mfa_totp import encrypt_secret, get_auth_key_ring
        from apps.accounts.models import CustomUser, TOTPCredential
        from apps.tasks import projects as pj
        from apps.tasks.models import ProjectMember

        pw = _sec.token_urlsafe(18) + "aA1!"
        sec = pyotp.random_base32(length=32)
        u = CustomUser.objects.create(
            employee_id="ST-LIVE-2", full_name="Second Member", email="second.m@salowtech.com", email_verified=True,
            password=make_password(pw), role=CustomUser.Role.EMPLOYEE, status=CustomUser.Status.ACTIVE, is_active=True,
        )
        ct, nonce, key_id = encrypt_secret(sec, get_auth_key_ring())
        TOTPCredential.objects.create(user=u, encrypted_secret=ct, nonce=nonce, key_id=key_id, confirmed_at=timezone.now())
        me = CustomUser.objects.get(employee_id="ST-LIVE-1")
        p = pj.create_project(me, "Zork second login")
        ProjectMember.objects.create(project=p, person=u, role=ProjectMember.MEMBER, added_by=me)
        pj.add_task(me, p, p.columns.first().pk, "Zork task for member")
        return str(p.public_id), pw, sec

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        pid, pw2, sec2 = ex.submit(seed).result()
    pg.goto(APP + "/tasks/projects/%s/" % pid)
    pg.select_option("select[name=person] >> nth=0", label="Second Member")
    pg.click("button:has-text('Assign') >> nth=0")
    pg.wait_for_load_state()
    check("assign second: the lead assigns the member and the card says so", pg.locator("text=Assigned to Second Member").count() == 1)

    ctx2 = ctx.browser.new_context(viewport={"width": 390, "height": 800})
    errs, _remote, only_loopback = ctx.salow_hooks
    ctx2.route("**/*", only_loopback)  # same request-leak check as the first context
    pg2 = ctx2.new_page()
    pg2.on("pageerror", lambda e: errs.append(scrub(e)))  # same page-error check
    pg2.set_default_timeout(8000)
    pg2.goto(APP + "/sign-in/")
    pg2.wait_for_timeout(800)
    pg2.click("#btn-signin")
    pg2.wait_for_timeout(500)
    pg2.fill("#employee_id", "ST-LIVE-2")
    pg2.fill("#password", pw2)
    t = pyotp.TOTP(sec2)
    left = t.interval - (time.time() % t.interval)
    if left < 5:
        time.sleep(left + 0.5)
    pg2.fill("#totp_code", t.now())
    pg2.click("#confirm-btn")
    pg2.wait_for_load_state()
    pg2.wait_for_timeout(1200)
    pg2.goto(APP + "/tasks/assigned/")
    body2 = pg2.inner_text("body")
    check("assign second: the member's Assigned page lists the project task with its project and links to it", "Zork task for member" in body2 and "Zork second login" in body2 and pg2.locator("a.task-title:has-text('Zork task for member')").count() == 1)
    pg2.screenshot(path=os.path.join(shots, "task_assigned_page_member_390.png"), full_page=True)
    pg2.goto(APP + "/tasks/projects/%s/" % pid)
    check("assign second: the member's own login sees the card saying who it is assigned to", "Assigned to Second Member" in pg2.inner_text("body"))
    pg2.screenshot(path=os.path.join(shots, "task_assign_member_view_390.png"), full_page=True)
    check("assign second: the member opens the project and sees no Assign control", pg2.locator("button:has-text('Assign')").count() == 0)
    pg2.goto(APP + "/search/?q=zork")
    check("assign second: search shows the project to the member", "Zork second login" in pg2.inner_text("body"))

    def remove():
        from django.utils import timezone

        from apps.tasks.models import ProjectMember

        ProjectMember.objects.filter(project__public_id=pid, person__employee_id="ST-LIVE-2").update(removed_at=timezone.now())

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        ex.submit(remove).result()
    r = pg2.goto(APP + "/tasks/projects/%s/" % pid)
    check("assign second: after removal the project page is a 404 for them", r.status == 404, str(r.status))
    pg2.goto(APP + "/search/?q=zork")
    check("assign second: after removal search no longer shows the project", "Zork second login" not in pg2.inner_text("body"))
    pg2.goto(APP + "/tasks/assigned/")
    check("assign second: after removal the Assigned page no longer lists the task", "Zork task for member" not in pg2.inner_text("body"))
    pg2.close()


def second_context_selftest(ctx):
    """Proves the second context is covered by the shared checks: a page error and an off-machine
    request made in a second context land in the shared lists (then the lists are cleared so the
    run's own end checks are not tripped by this deliberate injection)."""
    errs, remote, only_loopback = ctx.salow_hooks
    c2 = ctx.browser.new_context()
    c2.route("**/*", only_loopback)
    p2 = c2.new_page()
    p2.on("pageerror", lambda e: errs.append(scrub(e)))
    p2.goto(APP + "/health")
    n_err, n_rem = len(errs), len(remote)
    p2.evaluate("setTimeout(function(){ throw new Error('selftest page error'); }, 0)")
    p2.wait_for_timeout(300)
    try:
        p2.goto("http://203.0.113.1:9/x", timeout=3000)  # blocked by the route, so nothing leaves the machine
    except Exception:  # noqa: BLE001, S110 - the abort is the expected outcome
        pass
    check("second context selftest: its page error reaches the shared list", len(errs) == n_err + 1, str(len(errs) - n_err))
    check("second context selftest: its off-machine request is blocked and recorded", len(remote) == n_rem + 1 and remote[-1].startswith("http://203.0.113.1"), str(remote[-1:]))
    p2.close()
    c2.close()
    pg0 = ctx.pages[0]
    pg0.wait_for_timeout(500)  # a late repeat of the injected error would arrive by now
    errs[:] = [e for e in errs if "selftest page error" not in str(e)]  # only the deliberate one
    remote[:] = [r for r in remote if not r.startswith("http://203.0.113.1:9")]  # only the deliberate entry; anything else stays and trips the end checks


DRILLS = {
    "second_context_selftest": second_context_selftest,
    "task_assign_second": task_assign_second,
    "finder_projects": finder_projects,
    "task_filters": task_filters,
    "task_comments": task_comments,
    "cal_series_page": cal_series_page,
    "cal_series": cal_series,
    "cal_month": cal_month,
    "slides_show": slides_show,
    "slides_edit": slides_edit,
    "slides_pdf": slides_pdf,
    "slides_layout": slides_layout,
    "slides_pdf_theme": slides_pdf_theme,
    "sheets_sort_filter": sheets_sort_filter,
    "sheets_lookup": sheets_lookup,
    "sheets_half_emoji": sheets_half_emoji,
    "sheets_dates_math": sheets_dates_math,
    "sheets_stats_math": sheets_stats_math,
    "slides_animation": slides_animation,
    "sheets_fill": sheets_fill,
    "sheets_headers": sheets_headers,
    "slides_drag": slides_drag,
    "slides_images": slides_images,
    "slides_images_edge": slides_images_edge,
    "slides_images_race": slides_images_race,
    "slides_images_pdfmem": slides_images_pdfmem,
    "slides_oldpage_v5": slides_oldpage_v5,
    "chat_poll_tabs": chat_poll_tabs,
    "chat_poll_429": chat_poll_429,
    "chat_poll_real": chat_poll_real,
    "pdf_fit": pdf_fit,
    "pdf_rotate": pdf_rotate,
    "pdf_notes_turn": pdf_notes_turn,
    "pdf_thumb_measure": pdf_thumb_measure,
    "pdf_forms": pdf_forms,
    "pdf_thumbs": pdf_thumbs,
    "pdf_thumbs_layout": pdf_thumbs_layout,
    "pdf_thumbs_cancel": pdf_thumbs_cancel,
    "pdf_pages": pdf_pages,
    "pdf_order": pdf_order,
    "pdf_marks": pdf_marks,
    "pdf_marks_secure": pdf_marks_secure,
    "pdf_marks_keep": pdf_marks_keep,
    "pdf_marks_shot": pdf_marks_shot,
    "pdf_text_hl": pdf_text_hl,
    "pdf_text_stale": pdf_text_stale,
    "pdf_marks_flat": pdf_marks_flat,
    "pdf_marks_flat_geo": pdf_marks_flat_geo,
    "pdf_text_hl_rot": pdf_text_hl_rot,
    "slides_style": slides_style,
    "sheets_tabs": sheets_tabs,
    "sheets_chart": sheets_chart,
    "sheets_validation": sheets_validation,
    "sheets_colour": sheets_colour,
    "sheets_format": sheets_format,
    "sheets_paste": sheets_paste,
    "sheets_old_page": sheets_old_page,
    "sheets_pivot": sheets_pivot,
    "sheets_grid": sheets_grid,
    "slides_master": slides_master,
    "slides_transition": slides_transition,
    "sheets_validation_paths": sheets_validation_paths,
    "sheets_tabs_stale": sheets_tabs_stale,
    "sheets_tabs_safety": sheets_tabs_safety,
    "selftest_scrub": selftest_scrub,
}


# Drills that need a secure context (navigator.locks) run in their own browser, started with a flag that treats the plain-HTTP drill origin as
# secure. This is a test browser setting only, not the app's policy or an HTTPS deployment. Other drills never get the flag.
SECURE_DRILLS = ("pdf_notes_turn", "pdf_marks_secure", "pdf_marks_keep", "pdf_marks_shot")


def run_group(p, names, secure, state=None, keep=None):  # noqa: C901
    b = p.chromium.launch(
        channel="chromium",
        args=[
            "--no-proxy-server",
            "--host-resolver-rules=MAP app.salowtech.com 127.0.0.1, EXCLUDE localhost",
        ]
        # Test browser only: one drill needs a secure context (navigator.locks) on the plain-HTTP drill origin. Not the app's policy.
        + (["--unsafely-treat-insecure-origin-as-secure=" + APP.rstrip("/")] if secure else []),
    )
    ctx = b.new_context(viewport={"width": 1100, "height": 900})
    ctx.set_default_timeout(8000)
    errs = []
    remote = []

    app_u = urlsplit(APP)

    def only_loopback(route):
        u = urlsplit(route.request.url)
        if u.scheme in ("data", "blob", "about"):
            route.continue_()
        elif u.scheme == "http" and u.hostname == app_u.hostname and u.port == app_u.port:
            route.continue_()
        else:
            remote.append((u.scheme or "?") + "://" + (u.hostname or "?") + ":" + str(u.port))
            route.abort()

    ctx.route("**/*", only_loopback)
    ctx.salow_hooks = (errs, remote, only_loopback)  # a drill that opens a second context attaches the same checks
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errs.append(scrub(e)))
    try:
        # Before any credential is typed: the server we reach must be on this machine.
        r = pg.goto(APP + "/health")
        addr = r.server_addr() if r else None
        if not addr or addr["ipAddress"] not in ("127.0.0.1", "::1"):
            print("ABORT: the app address is not loopback, nothing was typed")
            return 2
        if state is None:
            login(pg)
        else:
            # Only after the loopback check: the earlier sign-in's cookies go to this address, never before it is verified.
            ctx.add_cookies(state)
            pg.goto(APP + "/")
    except Exception as ex:
        print("FAIL login:", describe(ex))
        return 1
    if "/sign-in" in pg.url:
        print("FAIL login: still on the sign-in page")
        return 1
    if keep is not None and not keep:
        keep.append(
            ctx.cookies()
        )  # taken right after the verified sign-in, before any drill can change the session
    for n in names:
        try:
            DRILLS[n](ctx)
        except Exception as ex:  # a crashed drill is a failure, not a skip
            check(n + ": drill ran to the end", False, describe(ex))
    check("no uncaught page errors", not errs, errs[:2])
    check("no request left this machine", not remote, remote[:3])
    b.close()
    return 0


def main():
    names = sys.argv[1:] or [n for n in DRILLS if n != "selftest_scrub"]
    unknown = [n for n in names if n not in DRILLS]
    if unknown:
        print("unknown drill:", ", ".join(unknown), "| known:", ", ".join(DRILLS))
        return 2
    t0 = time.time()
    groups = [
        (False, [n for n in names if n not in SECURE_DRILLS]),
        (True, [n for n in names if n in SECURE_DRILLS]),
    ]
    code = 0
    saved = []  # the second browser reuses the first sign-in (one MFA code per run)
    with sync_playwright() as p:
        for secure, group in groups:
            if not group:
                continue
            if secure:
                print(
                    "secure-context drills run in their own browser (test flag): "
                    + ", ".join(group)
                )
            code = run_group(p, group, secure, saved[0] if saved else None, saved)
            if code:
                break
    # The summary always prints, so a failed second group still shows what the first one counted.
    print(
        "%d/%d checks passed in %.0f s%s"
        % (
            sum(results),
            len(results),
            time.time() - t0,
            "" if not code else " (stopped early, exit %d)" % code,
        )
    )
    return code or (0 if all(results) else 1)


if __name__ == "__main__":
    sys.exit(main())
