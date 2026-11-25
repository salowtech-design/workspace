# Unit tests on a real TiDB (scratch, local only)

Manual, not in CI. Each file runs on its own fresh copy of a migrated template store, on a throwaway
`tidb-server --store=unistore` bound to 127.0.0.1 on a free port, with a cleared environment and a temporary
audit folder. `config/settings/test_tidb.py` refuses any non-loopback host and has no default port.

    export TIDB_SERVER=/path/to/tidb-server PYTHON=/path/to/python
    bash tests/tidb_unit/make_template.sh      # builds the template, asserts the server exited, writes head + migrations hash
    ls tests/unit/test_*.py > "${TIDB_UNIT_WORK:-$HOME/.tidb_unit}/files.txt"
    bash tests/tidb_unit/run.sh 105            # resumable; a call stops at the time budget (seconds). Repeat until "done N of N"

`run.sh` refuses to run if the migrations hash differs from the template's and records head, dirty-file count and
migrations hash per call in `results.txt`. A stale `.meta` is deleted before a template build. A failed copy, a server that
never becomes ready or one that does not exit is recorded as FAIL. Results are per file; a TiDB CHECK constraint is not
enforced by default (`tidb_enable_check_constraint` is OFF), so the one CHECK test skips there. Do not point this at a real cluster.
