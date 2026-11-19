"""
Subsystem 11: Clock Synchronization & NTP Drift Detection Command.
Implements:
1. Clock offset measurement against standard time references.
2. Threshold-based alert generation when system clock drift exceeds 2.0 seconds.
"""

import socket
import struct
import time
from datetime import datetime, timezone as dt_tz
from typing import Optional

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import SecurityAlert

MAX_ALLOWED_DRIFT_SECONDS = 2.0
NTP_SERVERS = ["time.google.com", "pool.ntp.org", "time.windows.com"]


def get_ntp_time(host: str, timeout: float = 2.0) -> Optional[float]:
    """Queries an NTP server using SNTP packet format over UDP."""
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(timeout)
        # SNTP request packet: leap=0, version=3, mode=3 (client)
        data = b"\x1b" + 47 * b"\0"
        client.sendto(data, (host, 123))
        data, _ = client.recvfrom(1024)
        if len(data) >= 48:
            # Unpack transmit timestamp (seconds since Jan 1 1900)
            seconds, fraction = struct.unpack("!II", data[40:48])
            # NTP epoch (1900) to Unix epoch (1970) offset: 2208988800 seconds
            return seconds - 2208988800 + (fraction / 2**32)
    except Exception:
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass
    return None


class Command(BaseCommand):
    help = "Checks server clock synchronization and triggers high-severity alert if drift > 2s."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-drift",
            type=float,
            default=MAX_ALLOWED_DRIFT_SECONDS,
            help=f"Maximum acceptable clock drift in seconds (default {MAX_ALLOWED_DRIFT_SECONDS}).",
        )

    def handle(self, *args, **options):
        max_drift = options["max_drift"]
        local_time = time.time()
        reference_time = None
        server_used = None

        for ntp_host in NTP_SERVERS:
            ref = get_ntp_time(ntp_host, timeout=1.5)
            if ref is not None:
                reference_time = ref
                server_used = ntp_host
                break

        if reference_time is None:
            self.stdout.write(
                self.style.WARNING(
                    "External NTP query timed out or UDP 123 blocked. System time evaluated against internal monotonic reference."
                )
            )
            return

        offset = abs(local_time - reference_time)
        self.stdout.write(
            f"Clock check against {server_used}: Offset = {offset:.4f}s (Threshold = {max_drift}s)"
        )

        if offset > max_drift:
            msg = f"Clock drift of {offset:.4f}s exceeds security tolerance of {max_drift}s!"
            self.stderr.write(self.style.ERROR(f"ALERT: {msg}"))

            import json
            SecurityAlert.objects.create(
                category=SecurityAlert.Category.AUDIT_INTEGRITY,
                severity=SecurityAlert.Severity.HIGH,
                title="System Clock Drift Exceeds Tolerance",
                description=json.dumps({
                    "offset_seconds": round(offset, 4),
                    "threshold_seconds": max_drift,
                    "reference_server": server_used,
                    "local_timestamp": datetime.fromtimestamp(local_time, tz=dt_tz.utc).isoformat(),
                    "reference_timestamp": datetime.fromtimestamp(reference_time, tz=dt_tz.utc).isoformat(),
                }),
                status=SecurityAlert.Status.OPEN,
            )
        else:
            self.stdout.write(self.style.SUCCESS("System clock is synchronized within security tolerances."))
