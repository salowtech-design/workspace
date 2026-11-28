import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.automation import engine


def session_txn_mode():
    """The database session's own transaction mode, read only. Empty text when it cannot be read."""
    try:
        with connection.cursor() as c:
            c.execute("SELECT @@session.tidb_txn_mode")
            row = c.fetchone()
    except Exception:  # noqa: BLE001 - any failure means "not verified"
        return ""
    return str(row[0] or "").strip().lower() if row else ""


class Command(BaseCommand):
    help = (
        "Carries out queued workflow runs and runs whose worker stopped (lease expired). "
        "Refuses to run unless the database session is verified pessimistic, because the run "
        "claim and the owner check rely on row locks."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--loop", action="store_true", help="Keep running until stopped.")
        parser.add_argument("--interval", type=int, default=5, help="Seconds between passes.")

    def handle(self, *args, **opts):
        mode = session_txn_mode()
        if mode != "pessimistic":
            raise CommandError(
                "Not running: this database session's transaction mode is %r, not 'pessimistic', or "
                "it could not be read. The run claim needs row locks. Nothing was changed; fix the "
                "database setting yourself and start the command again." % (mode or "unknown")
            )
        while True:
            d = engine.run_pending(opts["batch_size"])
            self.stdout.write(
                "Automation runs: %(succeeded)d succeeded, %(failed)d failed, "
                "%(cancelled)d cancelled, %(skipped)d not taken, %(errors)d raised, "
                "%(owner_inactive)d left because the owner is not active." % d
            )
            if not opts["loop"]:
                return
            time.sleep(max(1, opts["interval"]))
