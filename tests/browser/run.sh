#!/bin/bash
# Repeatable browser drills: real server, real TiDB (unistore) on a throwaway port, headless Chromium, real login with TOTP.
# Manual, not part of CI. Needs: a tidb-server binary, Python with the project's dependencies plus playwright, Chromium.
#   TIDB_SERVER=/path/to/tidb-server PYTHON=/path/to/python bash tests/browser/run.sh [drill-name ...]
# Output never contains secrets. Secrets are random per run, live in a 0600 file under a temp dir, and are deleted on exit.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${TIDB_SERVER:?set TIDB_SERVER to a tidb-server binary}"
PY="${PYTHON:-python}"
PORT_DB="${DRILL_DB_PORT:-4002}"; PORT_APP="${DRILL_APP_PORT:-8123}"
TMP="$(mktemp -d)"; chmod 700 "$TMP"
cleanup() { pkill -f "asgi_dev:application --host 127.0.0.1 --port $PORT_APP" 2>/dev/null; kill "${TIDB_PID:-0}" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT
"$TIDB_SERVER" --store=unistore --path="$TMP/tidb" -P "$PORT_DB" --host 127.0.0.1 --status "${DRILL_DB_STATUS:-10082}" --log-file="$TMP/tidb.log" > "$TMP/tidb.out" 2>&1 &
TIDB_PID=$!
for i in $(seq 1 40); do "$PY" -c "import pymysql;pymysql.connect(host='127.0.0.1',port=$PORT_DB,user='root')" 2>/dev/null && break; sleep 1; done
cd "$ROOT" || exit 2
"$PY" -c "import pymysql;c=pymysql.connect(host='127.0.0.1',port=$PORT_DB,user='root');c.cursor().execute('create database drill_db')" || { echo "DB not ready"; exit 2; }
export AUDIT_CHECKPOINT_DIR="$TMP/audit" DJANGO_SETTINGS_MODULE=config.settings.test_tidb TIDB_TEST_DB_NAME=drill_db TIDB_TEST_HOST=127.0.0.1 TIDB_TEST_PORT="$PORT_DB"
mkdir -p "$AUDIT_CHECKPOINT_DIR"
export PYTHONPATH="$ROOT:$ROOT/tests/browser${EXTRA_PYTHONPATH:+:$EXTRA_PYTHONPATH}"
"$PY" manage.py migrate -v0 2>&1 | tail -3
"$PY" tests/browser/seed.py "$TMP/secrets.json" || exit 2
"$PY" -m uvicorn asgi_dev:application --host 127.0.0.1 --port "$PORT_APP" --log-level warning > "$TMP/uvicorn.log" 2>&1 &
for i in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$PORT_APP/health" && break; sleep 1; done
export DRILL_SECRETS="$TMP/secrets.json" DRILL_APP="http://app.salowtech.com:$PORT_APP" DRILL_SHOTS="${DRILL_SHOTS:-}"
"$PY" tests/browser/drills.py "$@"
exit $?
