import time

from django.core.management.base import BaseCommand

from apps.automation.engine import run_pending


class Command(BaseCommand):
    help = "Carries out queued workflow runs and runs whose worker stopped (lease expired)."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--loop", action="store_true", help="Keep running until stopped.")
        parser.add_argument("--interval", type=int, default=5, help="Seconds between passes.")

    def handle(self, *args, **opts):
        while True:
            d = run_pending(opts["batch_size"])
            self.stdout.write(
                "Automation runs: %(succeeded)d succeeded, %(failed)d failed, "
                "%(cancelled)d cancelled, %(skipped)d not taken, %(errors)d raised, "
                "%(owner_inactive)d left because the owner is not active." % d
            )
            if not opts["loop"]:
                return
            time.sleep(max(1, opts["interval"]))
