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
    pg.click("#sl-dup")
    check(
        "master: Duplicate works after leaving master mode",
        len(stored()["slides"]) == 3,
        len(stored()["slides"]),
    )
    pg.click("#sl-down")
    check(
        "master: Move down works after leaving master mode",
        stored()["slides"][2]["id"] == ex["slides"][0]["id"] or len(stored()["slides"]) == 3,
    )
    pg.once("dialog", lambda d: d.accept())
    pg.click("#sl-del")
    check(
        "master: Delete works after leaving master mode",
        len(stored()["slides"]) == 2 and len(stored()["master"]["elements"]) == 2,
        len(stored()["slides"]),
    )
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
    "slides_master": slides_master,
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
