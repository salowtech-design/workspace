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

## What a result means
- Identity of a record: head, dirty-file count, a hash of every `migrations/0*.py` and `models.py`, and the TiDB version.
  The template must match the hash (the run refuses otherwise, and also refuses if no migration files are found).
  If the identity changes between calls the old results are rotated to `results.<time>.txt`, never resumed.
- A file counts as done only when it has a pytest summary line. `FAIL <file> ...` lines (copy error, server not ready,
  server not exiting) never count as done and are reported separately from test failures in the summary.
- Seed data: the template holds what migrations and post_migrate hooks (permissions, content types, migration-created rows) create.
  Seeding done by code outside migrations and models.py would not change the hash, so rebuild the template if you add such code.
- Excluded here: `tests/unit/test_review_fixes_dependencies.py` needs `tomllib` (Python 3.11+). The supported image is Python 3.12;
  a run on 3.10 can pass `EXTRA_PYTHONPATH` with a `sitecustomize.py` that aliases `tomli` as `tomllib`, which only affects that file.
  Do not count that alias as a run of the file under the supported Python.
- Needs `pymysql` and `django_tidb` in the Python used, and a `tidb-server` binary. Single-node unistore, not a cluster.
