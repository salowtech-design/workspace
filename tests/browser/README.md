# Browser drills

Manual regression drills for the device-local modules (Slides, Sheets). They start a real server on TiDB (unistore) and drive headless Chromium through the real sign-in with a TOTP code. They are **not** part of CI.

Run:

    TIDB_SERVER=/path/to/tidb-server PYTHON=/path/to/python bash tests/browser/run.sh            # all drills
    TIDB_SERVER=... PYTHON=... bash tests/browser/run.sh sheets_sort_filter                       # one drill

Optional: `DRILL_SHOTS=/some/dir` saves screenshots, `EXTRA_PYTHONPATH` for shims, `DRILL_DB_PORT`, `DRILL_APP_PORT`.
Needs the project's dependencies, `playwright` and a Chromium build (`channel="chromium"`). The app is served on `app.salowtech.com:<port>` through a host-resolver rule, so no DNS or hosts change is needed.

Output is `PASS` or `FAIL <reason>` per check and a final count. Exit code 1 on any failure, 2 on bad input or a server that did not start.

Secrets: a fresh random password and TOTP secret are made for one synthetic employee on each run, written to a 0600 file in a temp dir, never printed, and deleted when the run ends. Nothing is stored in tracked source.

What it covers: Slides show (keys, shortcut guard, notes off on each open, close paths, storage unchanged), Slides editing (emoji wrapping, item order, duplicate, reload, id uniqueness), Sheets sort, filter, CSV with hidden rows, and focus recovery when a filter hides the active row (edit, undo, redo).
What it does not cover: PDF, Drive, IAM and the other modules; phones or touch; screen readers; full screen; other browsers; load. A passing run is evidence for those checks on one local node, not a readiness claim.
Add a drill: write a function in `drills.py` that uses `check(name, condition, detail)` and register it in `DRILLS`. Keep checks to behavior the page can show, and clear storage with `fresh()` first.
