"""Browser drills for the device-local modules (Slides, Sheets). Each check prints PASS or FAIL with a short reason.
Exit code is 1 if any check fails. Run through run.sh, which supplies a live server and throwaway secrets.
Not in CI. Needs playwright and Chromium. Secrets are read from a 0600 file and never printed."""

import json
import os
import sys
import time
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

    for i, v in enumerate(["3", "1", "0", "-2"], 1):
        setc("A%d" % i, v)
    pg.fill("#chart-range", "A1:A4")
    pg.click("#chart-draw")
    check(
        "chart: bar is the default and draws a bar per non-zero value",
        label().startswith("Bar") and count("rect") == 3,
        label(),
    )
    pg.select_option("#chart-type", "line")
    pg.click("#chart-draw")
    check(
        "chart: line draws one line and a point per value",
        label().startswith("Line") and count("polyline") == 1 and count("circle") == 4,
        (label(), count("circle")),
    )
    pg.select_option("#chart-type", "pie")
    pg.click("#chart-draw")
    check(
        "chart: pie draws one slice per value above zero",
        label().startswith("Pie") and count("path") == 2 and count("li") == 2,
        (label(), count("path")),
    )
    check(
        "chart: pie says what it left out",
        "2 values at or below zero left out" in pg.inner_text("#chart-out"),
        pg.inner_text("#chart-out"),
    )
    shares = pg.eval_on_selector_all("#chart-out li", "e=>e.map(x=>x.textContent)")
    check(
        "chart: pie shares are 75.0% and 25.0%",
        shares == ["A1: 3 (75.0%)", "A2: 1 (25.0%)"],
        shares,
    )
    shot(pg, "sheets-chart-42-pie")
    setc("A2", "9")
    check(
        "chart: the pie follows an edit",
        "A2: 9 (75.0%)" in pg.inner_text("#chart-out"),
        pg.inner_text("#chart-out"),
    )
    setc("A1", "0")
    setc("A2", "-1")
    check(
        "chart: a pie with nothing above zero says so",
        "needs at least one value above zero" in pg.inner_text("#chart-out"),
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


DRILLS = {
    "slides_show": slides_show,
    "slides_edit": slides_edit,
    "sheets_sort_filter": sheets_sort_filter,
    "sheets_tabs": sheets_tabs,
    "sheets_chart": sheets_chart,
    "sheets_tabs_stale": sheets_tabs_stale,
    "sheets_tabs_safety": sheets_tabs_safety,
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
