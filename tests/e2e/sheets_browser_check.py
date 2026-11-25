"""Browser check for the device-local Sheets page. Standalone and run by hand, not by CI.

Needs: a running workspace server, a test account with TOTP, `pip install playwright pyotp`,
and `playwright install chromium`. Credentials come only from the environment, never from this file:

  SALOW_E2E_BASE_URL      e.g. http://app.salowtech.com:8123
  SALOW_E2E_EMPLOYEE_ID   test account employee id
  SALOW_E2E_PASSWORD      its password
  SALOW_E2E_TOTP_SECRET   its TOTP secret (base32)
  SALOW_E2E_HOST_RULES    optional Chromium --host-resolver-rules value

Use a throwaway test account only. Exit code 0 means every check passed.
"""

import os
import sys
import tempfile

import pyotp
from playwright.sync_api import sync_playwright

FAILS = []


def need(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing environment variable {name}")
    return v


def check(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label, detail if not cond else "")
    if not cond:
        FAILS.append(label)


def main():
    base, eid = need("SALOW_E2E_BASE_URL").rstrip("/"), need("SALOW_E2E_EMPLOYEE_ID")
    pw, totp = need("SALOW_E2E_PASSWORD"), need("SALOW_E2E_TOTP_SECRET")
    args = (
        [f"--host-resolver-rules={os.environ['SALOW_E2E_HOST_RULES']}"]
        if os.environ.get("SALOW_E2E_HOST_RULES")
        else []
    )
    tmp = tempfile.mkdtemp()
    with sync_playwright() as p:
        b = p.chromium.launch(args=args)
        D = b.new_context(
            viewport={"width": 1100, "height": 1000}, accept_downloads=True
        ).new_page()
        D.set_default_timeout(20000)
        errs, msgs, mode = [], [], {"accept": True}
        D.on("pageerror", lambda e: errs.append(str(e)))
        D.on(
            "dialog",
            lambda d: (msgs.append(d.message), d.accept() if mode["accept"] else d.dismiss()),
        )
        D.goto(base + "/sign-in/")
        D.wait_for_timeout(800)
        D.click("#btn-signin")
        D.wait_for_timeout(500)
        D.fill("#employee_id", eid)
        D.fill("#password", pw)
        D.fill("#totp_code", pyotp.TOTP(totp).now())
        D.click("#confirm-btn")
        D.wait_for_load_state()
        D.wait_for_timeout(1200)
        D.goto(base + "/sheets/")
        D.wait_for_timeout(500)
        D.evaluate("localStorage.clear()")
        D.reload()
        D.wait_for_timeout(300)

        def A(a):
            return D.input_value(f'[data-addr="{a}"]')

        def st():
            return D.inner_text("#sheet-status")

        def typ(a, v):
            D.click(f'[data-addr="{a}"]')
            D.keyboard.press("Control+a")
            D.keyboard.type(v)
            D.keyboard.press("Enter")

        for a, v in [("A1", "5"), ("A2", "-8"), ("A3", "0"), ("A4", "=A1*2")]:
            typ(a, v)
        check("formula computes", A("A4") == "10")
        D.click('[data-addr="B1"]')
        D.keyboard.type("temp")
        D.keyboard.press("Escape")
        D.click('[data-addr="C1"]')
        check("Esc reverts a cell edit", A("B1") == "")
        D.click('[data-addr="A1"]')
        D.fill("#sheet-bar", "50")
        D.press("#sheet-bar", "Enter")
        check("formula bar edit recalculates", A("A1") == "50" and A("A4") == "100")
        D.click("#sheet-undo")
        check("undo after a bar edit", A("A1") == "5" and A("A4") == "10")
        D.click("#sheet-redo")
        check("redo", A("A1") == "50")
        D.click('[data-addr="A1"]')
        D.fill("#sheet-bar", "999")
        D.press("#sheet-bar", "Escape")
        D.click('[data-addr="C1"]')
        check("Esc in the formula bar reverts", A("A1") == "50")
        D.fill("#chart-range", "A1:A4")
        D.click("#chart-draw")
        D.wait_for_timeout(150)
        check(
            "chart: zero value has no bar",
            D.evaluate("document.querySelectorAll('#chart-out svg rect').length") == 3,
        )
        typ("A1", "7")
        titles = D.evaluate(
            "Array.from(document.querySelectorAll('#chart-out svg title')).map(t=>t.textContent)"
        )
        check("chart follows an edit", "A1: 7" in titles)
        D.click("#sheet-clear")
        check("chart empties after clear", "No numbers" in D.inner_text("#chart-out"))
        D.click("#sheet-undo")
        check(
            "chart returns after undo",
            D.evaluate("document.querySelectorAll('#chart-out svg rect').length") == 3,
        )
        snap = [A(a) for a in ("A1", "A2", "A3", "A4")]
        open(os.path.join(tmp, "o.json"), "w").write('{"A1":"1","A2":"2"}')

        def imp(body, name="x.json"):
            f = os.path.join(tmp, name)
            open(f, "w").write(body)
            D.set_input_files("#sheet-import", f)
            D.wait_for_timeout(350)

        mode["accept"] = False
        imp('{"A1":"1","A2":"2"}')
        check("import cancel leaves the sheet", [A(a) for a in ("A1", "A2", "A3", "A4")] == snap)
        for body in ("{}", '{"constructor":"x","ZZ99":"1"}', "nope", "[1,2]"):
            n = len(msgs)
            imp(body)
            check(
                f"unusable import refused ({body[:12]})",
                [A(a) for a in ("A1", "A2", "A3", "A4")] == snap and len(msgs) == n,
            )
        mode["accept"] = True
        imp('{"A1":"1","A2":"2"}')
        check("accepted import", A("A1") == "1" and A("A2") == "2")
        D.click("#sheet-undo")
        check("undo import", [A(a) for a in ("A1", "A2", "A3", "A4")] == snap)
        D.evaluate(
            "Object.defineProperty(Storage.prototype, 'setItem', {configurable:true, value: function(){ throw new DOMException('quota','QuotaExceededError') }})"
        )
        typ("C2", "blocked")
        check("blocked storage is reported on edit", "would not store" in st())
        D.click("#sheet-undo")
        check(
            "blocked storage stays visible through undo",
            "would not store" in st() and "Undone" in st(),
        )
        check("no page errors", not errs, str(errs))
        b.close()
    print(f"{'FAILED' if FAILS else 'PASSED'}: {len(FAILS)} failing")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
