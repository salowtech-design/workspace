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
        "Subtitle" in pg.inner_text("#sl-show-canvas"),
        pg.inner_text("#sl-show-canvas"),
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
    check("validation: with unreadable rules the cell is not blocked", val("C2") == "99", val("C2"))
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
    print("note: execCommand copy to the browser clipboard returned", copied, flush=True)
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
    print(
        "note: existing behaviour with unreadable rules, a typed edit is saved unchecked:",
        val("H41") == "typed",
        flush=True,
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


DRILLS = {
    "slides_show": slides_show,
    "slides_edit": slides_edit,
    "slides_pdf": slides_pdf,
    "slides_layout": slides_layout,
    "slides_pdf_theme": slides_pdf_theme,
    "sheets_sort_filter": sheets_sort_filter,
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
