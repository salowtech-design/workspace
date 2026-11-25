#!/bin/bash
# Repeatable browser drills: real server, real TiDB (unistore) on throwaway free ports, headless Chromium, real login with TOTP.
# Manual, not part of CI. Needs: a tidb-server binary, Python with the project's dependencies plus playwright, Chromium.
#   TIDB_SERVER=/path/to/tidb-server PYTHON=/path/to/python bash tests/browser/run.sh [drill-name ...]
# Safety: it refuses ports that are already in use, only stops processes it started, and runs the python steps with a
# cleared environment (allow-list below), so exported production database or auth settings cannot reach it.
# Output never contains secrets. Secrets are random per run, live in a 0600 file under a temp dir, and are deleted on exit.
set -u -o pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${TIDB_SERVER:?set TIDB_SERVER to a tidb-server binary}"
PY="${PYTHON:-python}"
TMP="$(mktemp -d)"; chmod 700 "$TMP"
TIDB_PID=""; APP_PID=""
cleanup() {
  [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null
  [ -n "$TIDB_PID" ] && kill "$TIDB_PID" 2>/dev/null
  if [ -n "${DRILL_KEEP_LOGS:-}" ] && [ "${RC:-0}" != 0 ]; then keep_logs; fi
  rm -rf "$TMP"
}
# Failure logs: copied only when DRILL_KEEP_LOGS names a directory, with the run's secrets removed, then checked.
keep_logs() {
  mkdir -p "$DRILL_KEEP_LOGS" && chmod 700 "$DRILL_KEEP_LOGS" || return
  "$PY" - "$TMP" "$DRILL_KEEP_LOGS" <<'PYEOF'
import json, os, sys
tmp, out = sys.argv[1:3]
try:
    vals = [v for v in json.load(open(os.path.join(tmp, "secrets.json"))).values() if v]
except Exception:
    vals = []
for name in ("uvicorn.log", "drills.out"):
    p = os.path.join(tmp, name)
    if not os.path.exists(p):
        continue
    t = open(p, errors="replace").read()
    for v in vals:
        t = t.replace(v, "[removed]")
    dst = os.path.join(out, name)
    open(dst, "w").write(t)
    os.chmod(dst, 0o600)
    if any(v in open(dst).read() for v in vals):
        os.remove(dst)
        print("kept log failed the secret check and was deleted:", name)
PYEOF
}
trap cleanup EXIT
free_port() { "$PY" -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])"; }
port_busy() { "$PY" -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',$1))==0 else 1)"; }
PORT_DB="${DRILL_DB_PORT:-$(free_port)}"; PORT_APP="${DRILL_APP_PORT:-$(free_port)}"; PORT_ST="${DRILL_DB_STATUS:-$(free_port)}"
for p in "$PORT_DB" "$PORT_APP" "$PORT_ST"; do
  if port_busy "$p"; then echo "Port $p is already in use. Not starting; nothing was touched."; RC=2; exit 2; fi
done
[ "$PORT_DB" != "$PORT_APP" ] || { echo "Same port for DB and app"; RC=2; exit 2; }
# Clean environment for everything below. Only these variables pass through.
E=(env -i PATH="$PATH" HOME="${HOME:-/tmp}" LANG=C.UTF-8 TMPDIR="$TMP" NO_PROXY=127.0.0.1,localhost,app.salowtech.com no_proxy=127.0.0.1,localhost,app.salowtech.com
   ${PLAYWRIGHT_BROWSERS_PATH:+PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH"}
   AUDIT_CHECKPOINT_DIR="$TMP/audit" DJANGO_SETTINGS_MODULE=config.settings.test_tidb TIDB_TEST_DB_NAME=drill_db
   TIDB_TEST_USER=root TIDB_TEST_PASSWORD= TIDB_TEST_HOST=127.0.0.1 TIDB_TEST_PORT="$PORT_DB"
   PYTHONPATH="$ROOT:$ROOT/tests/browser${EXTRA_PYTHONPATH:+:$EXTRA_PYTHONPATH}")
mkdir -p "$TMP/audit"
echo "Target: DB 127.0.0.1:$PORT_DB (new throwaway store), app 127.0.0.1:$PORT_APP, settings config.settings.test_tidb"
"$TIDB_SERVER" --store=unistore --path="$TMP/tidb" -P "$PORT_DB" --host 127.0.0.1 --status "$PORT_ST" --log-file="$TMP/tidb.log" > "$TMP/tidb.out" 2>&1 &
TIDB_PID=$!
ready=0
for i in $(seq 1 40); do
  kill -0 "$TIDB_PID" 2>/dev/null || { echo "TiDB exited early"; RC=2; exit 2; }
  "$PY" -c "import pymysql;pymysql.connect(host='127.0.0.1',port=$PORT_DB,user='root')" 2>/dev/null && { ready=1; break; }
  sleep 1
done
[ "$ready" = 1 ] || { echo "DB not ready"; RC=2; exit 2; }
cd "$ROOT" || exit 2
"${E[@]}" "$PY" -c "import pymysql;c=pymysql.connect(host='127.0.0.1',port=$PORT_DB,user='root');c.cursor().execute('create database drill_db')" || { echo "Could not create the throwaway database"; RC=2; exit 2; }
"${E[@]}" "$PY" manage.py migrate -v0 2>&1 | tail -3
[ "${PIPESTATUS[0]}" = 0 ] || { echo "migrate failed"; RC=2; exit 2; }
"${E[@]}" "$PY" tests/browser/seed.py "$TMP/secrets.json" || { RC=2; exit 2; }
"${E[@]}" "$PY" -m uvicorn asgi_dev:application --host 127.0.0.1 --port "$PORT_APP" --log-level warning > "$TMP/uvicorn.log" 2>&1 &
APP_PID=$!
ready=0
for i in $(seq 1 30); do
  kill -0 "$APP_PID" 2>/dev/null || { echo "App exited early"; RC=2; exit 2; }
  curl -s --noproxy '*' -o /dev/null "http://127.0.0.1:$PORT_APP/health" && { ready=1; break; }
  sleep 1
done
[ "$ready" = 1 ] || { echo "App not ready"; RC=2; exit 2; }
"${E[@]}" DRILL_SECRETS="$TMP/secrets.json" DRILL_APP="http://app.salowtech.com:$PORT_APP" DRILL_SHOTS="${DRILL_SHOTS:-}" "$PY" tests/browser/drills.py "$@" 2>&1 | tee "$TMP/drills.out"
RC=${PIPESTATUS[0]}
# Output must not hold this run's secrets. Checked here, in the shell, against the file that never prints them.
"$PY" - "$TMP" <<'PYEOF'
import json, os, sys
tmp = sys.argv[1]
vals = [v for v in json.load(open(os.path.join(tmp, "secrets.json"))).values() if v]
t = open(os.path.join(tmp, "drills.out")).read() + open(os.path.join(tmp, "uvicorn.log")).read()
if any(v in t for v in vals):
    print("SECRET FOUND IN OUTPUT OR APP LOG")
    sys.exit(3)
print("secret check on output and app log: clean")
PYEOF
SC=$?
[ "$SC" = 0 ] || RC=3
exit "$RC"
