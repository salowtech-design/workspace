import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.middleware import RateLimitMiddleware


class Command(BaseCommand):
    help = (
        "Resets in-memory and database rate limit records (development and test environments only)."
    )

    def handle(self, *args, **options):
        env = getattr(settings, "ENVIRONMENT", "").lower()
        settings_module = getattr(settings, "SETTINGS_MODULE", "").lower()
        is_safe_env = (
            settings.DEBUG
            or env in ("development", "test", "demo")
            or "test" in settings_module
            or "pytest" in sys.modules
            or getattr(settings, "ALLOW_RATE_LIMIT_RESET", False)
        )
        if not is_safe_env:
            raise CommandError(
                "reset_ratelimit is restricted to development, test, and demo environments."
            )

        RateLimitMiddleware.reset()
        self.stdout.write(
            self.style.SUCCESS("Successfully reset in-memory and database rate limit records.")
        )
