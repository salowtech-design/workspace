#!/bin/bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; : "${TIDB_SERVER:?path to a tidb-server binary}"; WORK="${TIDB_UNIT_WORK:-$HOME/.tidb_unit}"; mkdir -p "$WORK"
# Real-TiDB unit run, one pristine copy of the verified template store PER FILE (no state shared between files).
# Per file: copy template to a fresh temp dir, start throwaway unistore tidb-server on a free loopback port, run the file
# with --reuse-db (template already holds the migrated test DB), stop the server, assert it exited, delete the copy.
# Results appended to "$WORK"/results.txt. usage: run.sh [budget_seconds]   files from "$WORK"/files.txt
BUDGET=${1:-105}; START=$(date +%s)
cd "$ROOT"; PY=${PYTHON:-python}
[ -f "$WORK"/tidb_template.meta ] || { echo "no verified template (run make_template.sh)"; exit 1; }
HEAD=$(git log --oneline -1 | cut -c1-7)
MSHA=$(find apps -path '*migrations/0*.py' | sort | xargs sha256sum | sha256sum | cut -c1-16)
THEAD=$(sed -n 1p "$WORK"/tidb_template.meta | sed 's/.*head=\([0-9a-f]*\) .*/\1/'); TSHA=$(sed -n 1p "$WORK"/tidb_template.meta | sed 's/.*migrations_sha=//')
[ "$MSHA" = "$TSHA" ] || { echo "FAIL: migrations hash $MSHA differs from template $TSHA, rebuild the template"; exit 1; }
[ "$HEAD" = "$THEAD" ] && HEADNOTE="template_head_same" || HEADNOTE="template_head=$THEAD(code differs, migrations identical)"; TREE=$(git status --short | wc -l)
fp(){ $PY -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])"; }
echo "# call $(date +%H:%M) head=$HEAD dirty_files=$TREE migrations_sha=$MSHA $HEADNOTE" >> "$WORK"/results.txt
for f in $(cat "$WORK"/files.txt); do
  grep -q "^$f " "$WORK"/results.txt 2>/dev/null && continue
  left=$((BUDGET-($(date +%s)-START))); [ $left -lt 20 ] && break
  TMP=$(mktemp -d); cp -r "$WORK"/tidb_template $TMP/tidb || { echo "$f FAIL: template copy error" >> "$WORK"/results.txt; rm -rf $TMP; break; }; PD=$(fp); PS=$(fp)
  "$TIDB_SERVER" --store=unistore --path=$TMP/tidb -P $PD --host 127.0.0.1 --status $PS --log-file=$TMP/t.log >$TMP/t.out 2>&1 & TP=$!
  ready=0; for i in $(seq 1 40); do $PY -c "import pymysql;pymysql.connect(host='127.0.0.1',port=$PD,user='root')" 2>/dev/null && { ready=1; break; }; sleep 1; done
  if [ $ready = 0 ]; then echo "$f FAIL: TiDB never became ready" >> "$WORK"/results.txt; kill $TP; rm -rf $TMP; break; fi
  left=$((BUDGET-($(date +%s)-START)))
  out=$(env -i PATH="$PATH" HOME=/tmp LANG=C.UTF-8 TMPDIR=$TMP NO_PROXY=127.0.0.1 AUDIT_CHECKPOINT_DIR=$TMP/audit DJANGO_SETTINGS_MODULE=config.settings.test_tidb TIDB_TEST_DB_NAME=unit TIDB_TEST_USER=root TIDB_TEST_PASSWORD= TIDB_TEST_HOST=127.0.0.1 TIDB_TEST_PORT=$PD PYTHONPATH="$ROOT${EXTRA_PYTHONPATH:+:$EXTRA_PYTHONPATH}" timeout $left $PY -m pytest $f --reuse-db -q -rs -p no:cacheprovider 2>&1); rc=$?
  kill $TP; for i in $(seq 1 20); do kill -0 $TP 2>/dev/null || break; sleep 0.5; done
  kill -0 $TP 2>/dev/null && { echo "FAIL: server did not exit" >> "$WORK"/results.txt; kill -9 $TP; }
  rm -rf $TMP
  [ $rc -eq 124 ] && break
  echo "$f $(echo "$out" | tail -1)" >> "$WORK"/results.txt
  echo "$out" | grep -E "^(FAILED|ERROR|SKIPPED|E  )" | sed "s#^#$f: #" >> "$WORK"/detail.txt
done
echo "done $(grep -vc '^#' "$WORK"/results.txt) of $(wc -l < "$WORK"/files.txt) head=$HEAD"
