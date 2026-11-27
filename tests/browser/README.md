# Browser drills

Manual regression drills for the device-local modules (Slides, Sheets). They start a real server on TiDB (unistore) and drive headless Chromium through the real sign-in with a TOTP code. They are **not** part of CI.

Run:

    TIDB_SERVER=/path/to/tidb-server PYTHON=/path/to/python bash tests/browser/run.sh            # all drills
    TIDB_SERVER=... PYTHON=... bash tests/browser/run.sh sheets_sort_filter                       # one drill

Optional: `DRILL_SHOTS=/some/dir` saves screenshots, `EXTRA_PYTHONPATH` for shims, `DRILL_KEEP_LOGS=/some/dir` keeps secret-scrubbed logs after a failed run, `DRILL_DB_PORT`, `DRILL_APP_PORT` (default: free ports it picks itself).

Safety: it refuses to start if a port is busy, stops only the two processes it started, and runs Python with a cleared environment (only PATH, HOME, LANG, TMPDIR, PLAYWRIGHT_BROWSERS_PATH and its own settings pass), so exported database or auth settings are ignored. Chromium runs with no proxy, requests to any host but the local app are blocked, and the server address must be loopback before anything is typed. Error text printed by the drills is scrubbed of this run's password, TOTP secret and recent codes. `run.sh selftest_scrub` forces failures that carry the real secrets to prove it; the run ends with a check of the output and app log for those secrets. `asgi_dev.py` is used only by this script; the production image starts gunicorn on config.wsgi and never references it.
Needs the project's dependencies, `playwright` and a Chromium build (`channel="chromium"`). The app is served on `app.salowtech.com:<port>` through a host-resolver rule, so no DNS or hosts change is needed.

Output is `PASS` or `FAIL <reason>` per check and a final count. Exit code 1 on any failure, 2 on bad input or a server that did not start.

Secrets: a fresh random password and TOTP secret are made for one synthetic employee on each run, written to a 0600 file in a temp dir, never printed, and deleted when the run ends. Nothing is stored in tracked source.

What it covers: Slides show (keys, shortcut guard, notes off on each open, close paths, storage unchanged), Slides editing (emoji wrapping, item order, duplicate, reload, id uniqueness), Sheets sort, filter, CSV with hidden rows, and focus recovery when a filter hides the active row (edit, undo, redo).
What it does not cover: PDF, Drive, IAM and the other modules; phones or touch; screen readers; full screen; other browsers; load. A passing run is evidence for those checks on one local node, not a readiness claim.
Add a drill: write a function in `drills.py` that uses `check(name, condition, detail)` and register it in `DRILLS`. Keep checks to behavior the page can show, and clear storage with `fresh()` first.

Database target: before any migration or seed write, `check_target.py` loads Django's effective settings under the same cleared environment and aborts unless they are config.settings.test_tidb, host 127.0.0.1, the owned port, database drill_db (test name test_drill_db) and a live `SELECT DATABASE()`/`@@port` agrees. It prints those non-secret facts. Migrations run as `python -m django migrate`, not manage.py, so manage.py's .env loading is bypassed. The secret check on output and the app log looks for plain text and URL-encoded, base64 and JSON-escaped forms of the password and TOTP secret; other encodings are not checked. tests/browser is listed in .dockerignore, so the image no longer contains these files (no Dockerfile step uses them; CI runs tests from the checkout).

## load_http: bounded local HTTP load diagnostic (not a capacity test)

`LOAD_LEVELS=10,50,100 LOAD_SECONDS=30 bash tests/browser/run.sh load_http` (same env as the other drills).
It starts a throwaway TiDB 8.5.1 unistore and a real uvicorn, seeds synthetic users with stored sessions, then runs one thread per
user over real HTTP with a weighted page mix (tasks, notifications, chat list and space, calendar, attendance, posts, 5% chat sends).
A success is a 200 that shows the user's own employee id and the expected page text; a redirect or empty page is a failure.
Report: `/tmp/load_report.json` (override with `LOAD_REPORT`). Stops after a level on fail rate over 10%, p95 over 8 s, or
MemAvailable under 250 MB, and never escalates after a stop. 250 users only if you set it.

Limits: the generator, uvicorn and TiDB share one box (1 CPU, about 2 GB in the sandbox run); sessions are stored, so login and MFA
are bypassed; per-user X-Forwarded-For emulates many clients for the per-IP limiter; the first 3 s warmup is included in the stats;
think time 0.4 to 1.2 s; page text markers are checked, not rendered. It says nothing about 1,000-user capacity or real hardware.
