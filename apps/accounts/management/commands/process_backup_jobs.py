"""Backup worker: claims queued BackupRun rows and runs them. Same loop pattern as the other workers."""

import time

from django.core.management.base import BaseCommand

from apps.accounts.auth.backup_jobs import claim_next_run, execute_run, recover_stale_runs


class Command(BaseCommand):
    help = "Process queued database backup requests."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep polling.")
        parser.add_argument("--interval", type=int, default=30)

    def handle(self, *args, **opts):
        while True:
            recover_stale_runs()
            run = claim_next_run()
            if run:
                run = execute_run(run)
                self.stdout.write(f"backup run {run.pk}: {run.status} {run.error_message}")
            if not opts["loop"]:
                return
            if not run:
                time.sleep(opts["interval"])
