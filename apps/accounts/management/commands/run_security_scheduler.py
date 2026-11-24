"""
Operational Security Scheduler Daemon.
Implements continuous and scheduled execution for all Subsystems:
- Subsystem 1: Periodic cryptographic audit checkpoint creation.
- Subsystem 2: 15-minute security alert escalation SLA enforcement.
- Subsystem 8: 60-day dormant account review and quarterly campaigns.
- Subsystem 10: Transactional email outbox processing with durable claims.
- Subsystem 11: Continuous NTP clock synchronization and 1.0s drift monitoring.
"""

import logging
import time
from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger("security.scheduler")


class Command(BaseCommand):
    help = "Runs the unified operational security scheduler daemon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single pass of all scheduled operations and exit (ideal for cron or test runner).",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Main loop polling tick interval in seconds (default: 10s).",
        )

    def handle(self, *args, **options):
        once = options["once"]
        interval = options["interval"]

        self.stdout.write(
            self.style.NOTICE(
                f"Starting Salow Operational Security Scheduler Daemon (Tick: {interval}s, Once: {once})..."
            )
        )

        last_drift_check = 0.0
        last_alert_escalation = 0.0
        last_audit_checkpoint = 0.0
        last_access_review = 0.0

        drift_interval = 60.0          # Check NTP drift every 60s
        escalation_interval = 60.0     # Check alert escalation every 60s (15m SLA)
        checkpoint_interval = 3600.0   # Check audit checkpoint every 1h
        review_interval = 86400.0      # Check dormant accounts daily

        while True:
            now = time.time()

            # 1. Process Outbox Queues (Every tick)
            try:
                call_command("process_email_outbox", batch_size=50)
            except Exception as e:
                self.stderr.write(f"[WARN] Email outbox error: {e}")

            try:
                call_command("process_outbox_events")
            except Exception as e:
                self.stderr.write(f"[WARN] Outbox events error: {e}")

            # 2. Clock Drift Monitoring (Subsystem 11)
            if now - last_drift_check >= drift_interval or once:
                try:
                    call_command("check_clock_drift", max_drift=1.0)
                    last_drift_check = now
                except Exception as e:
                    self.stderr.write(f"[WARN] Clock drift monitoring error: {e}")

            # 3. Security Alert Escalation (Subsystem 2)
            if now - last_alert_escalation >= escalation_interval or once:
                try:
                    call_command("escalate_security_alerts")
                    last_alert_escalation = now
                except Exception as e:
                    self.stderr.write(f"[WARN] Alert escalation error: {e}")

            # 4. Cryptographic Audit Checkpoint (Subsystem 1)
            if now - last_audit_checkpoint >= checkpoint_interval or once:
                try:
                    call_command("create_audit_checkpoint")
                    last_audit_checkpoint = now
                except Exception as e:
                    self.stderr.write(f"[WARN] Audit checkpoint error: {e}")

            # 5. Access Reviews & Dormant Account Scan (Subsystem 8)
            if now - last_access_review >= review_interval or once:
                try:
                    call_command("run_access_review")
                    last_access_review = now
                except Exception as e:
                    self.stderr.write(f"[WARN] Access review error: {e}")

            if once:
                self.stdout.write(self.style.SUCCESS("[SUCCESS] Security scheduler pass complete."))
                break

            time.sleep(interval)
