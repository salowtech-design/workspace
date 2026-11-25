#!/bin/bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; : "${TIDB_SERVER:?path to a tidb-server binary}"; WORK="${TIDB_UNIT_WORK:-$HOME/.tidb_unit}"; mkdir -p "$WORK"
cd "$ROOT"
rm -f "$WORK"/tidb_template.meta "$WORK"/tidb_template.leftover
NMIG=$(find apps -path '*migrations/0*.py' | wc -l); [ "$NMIG" -gt 0 ] || { echo "FAIL: no migration files found under apps/"; exit 1; }
PY=${PYTHON:-python}; TMP=$(mktemp -d)
fp(){ $PY -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])"; }
PD=$(fp)
"$TIDB_SERVER" --store=unistore --path="$WORK"/tidb_template -P $PD --host 127.0.0.1 --status $(fp) --log-file=$TMP/t.log >/dev/null 2>&1 & TP=$!
for i in $(seq 1 40); do $PY -c "import pymysql;pymysql.connect(host='127.0.0.1',port=$PD,user='root')" 2>/dev/null && break; sleep 1; done
$PY -c "import pymysql;pymysql.connect(host='127.0.0.1',port=$PD,user='root')" 2>/dev/null || { echo "FAIL: TiDB never became ready"; kill $TP 2>/dev/null; exit 1; }
cd "$ROOT"; TVER=$($PY -c "import pymysql;c=pymysql.connect(host='127.0.0.1',port=$PD,user='root');k=c.cursor();k.execute('select version()');print(k.fetchone()[0])" 2>/dev/null || echo unknown); env -i PATH="$PATH" HOME=/tmp LANG=C.UTF-8 TMPDIR=$TMP AUDIT_CHECKPOINT_DIR=$TMP/audit DJANGO_SETTINGS_MODULE=config.settings.test_tidb TIDB_TEST_DB_NAME=unit TIDB_TEST_USER=root TIDB_TEST_HOST=127.0.0.1 TIDB_TEST_PORT=$PD PYTHONPATH="$ROOT${EXTRA_PYTHONPATH:+:$EXTRA_PYTHONPATH}" timeout 100 $PY -m pytest tests/unit/test_device_approval_audit_actor.py --create-db --reuse-db -q -p no:cacheprovider 2>&1 | tail -2; [ ${PIPESTATUS[0]} -eq 0 ] || { echo "FAIL: template build test failed"; kill $TP; rm -rf "$WORK"/tidb_template; exit 1; }
export PD; $PY - <<'PYEOF' > "$WORK"/tidb_template.leftover
import os,pymysql
c=pymysql.connect(host='127.0.0.1',port=int(os.environ['PD']),user='root',database='test_unit');k=c.cursor()
k.execute("show tables");rows=[]
for (t,) in k.fetchall():
    k.execute("select count(*) from `%s`"%t);n=k.fetchone()[0]
    if n: rows.append((t,n))
print("nonempty tables in template:",rows)
PYEOF
kill $TP; for i in $(seq 1 30); do kill -0 $TP 2>/dev/null || break; sleep 1; done
if kill -0 $TP 2>/dev/null; then echo "FAIL: tidb-server still alive after 30 s, template NOT usable"; rm -rf "$WORK"/tidb_template; exit 1; fi
echo "template shutdown confirmed (pid $TP exited)"
rm -f "$WORK"/tidb_template.meta; echo "template head=$(git log --oneline -1 | cut -c1-7) identity_sha=$(find apps \( -path '*migrations/0*.py' -o -name models.py \) | sort | xargs sha256sum | sha256sum | cut -c1-16) tidb=$TVER" > "$WORK"/tidb_template.meta; rm -rf $TMP; du -sh "$WORK"/tidb_template
