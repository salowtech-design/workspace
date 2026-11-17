import logging
import threading
import time
from typing import Dict, List

from django.conf import settings
from django.http import HttpResponse

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware:
    """
    Enforces robust defense-in-depth HTTP security headers:
    - Content-Security-Policy (CSP)
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Referrer-Policy: strict-origin-when-cross-origin
    - Permissions-Policy: camera=(self), microphone=(), geolocation=()
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):  # noqa: C901
        response = self.get_response(request)

        # Content-Security-Policy: restrict source origins for scripts, styles, frames
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers.setdefault("Content-Security-Policy", "; ".join(csp_directives))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
        )

        return response


class RateLimitMiddleware:
    """
    Centralized, distributed-safe sliding window rate limiter.
    Defends against DoS, credential stuffing, and brute force abuse across multi-worker clusters.
    Uses TiDB-backed RateLimitRecord with trusted reverse proxy verification.
    """

    SENSITIVE_PREFIXES = (
        "/auth/login",
        "/auth/mfa",
        "/auth/activate",
        "/auth/recovery",
        "/auth/passkeys",
        "/auth/security/password/change",
        "/verify",
    )
    SENSITIVE_LIMIT = getattr(settings, "RATE_LIMIT_SENSITIVE_LIMIT", 5)  # max 5 requests per window for sensitive auth endpoints
    DEFAULT_LIMIT = getattr(settings, "RATE_LIMIT_DEFAULT_LIMIT", 300)  # general requests per window
    WINDOW_SECONDS = 60

    _storage: Dict[str, List[float]] = {}
    _lock = threading.Lock()
    _cleanup_counter: int = 0

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def reset(cls):
        """Reset rate limiter state (used in testing)."""
        with cls._lock:
            cls._storage.clear()
            cls._cleanup_counter = 0
        try:
            from apps.core.models import RateLimitRecord

            RateLimitRecord.objects.all().delete()
        except Exception as e:
            logger.debug("Failed to reset rate limit records: %s", e)

    def _get_client_ip(self, request) -> str:
        remote_addr = request.META.get("REMOTE_ADDR", "127.0.0.1")
        trusted_proxies = getattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "::1", "localhost"])
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

        if x_forwarded_for and remote_addr in trusted_proxies:
            return x_forwarded_for.split(",")[0].strip()
        return remote_addr

    def __call__(self, request):  # noqa: C901
        path = request.path_info
        if path.startswith("/static/"):
            return self.get_response(request)

        now = time.time()
        cutoff = now - self.WINDOW_SECONDS

        # Trigger cleanup if threshold reached (so even /health triggers eviction under high key volume)
        with self._lock:
            if len(self._storage) > 500:
                stale_keys = [k for k, ts in self._storage.items() if not ts or ts[-1] <= cutoff]
                for k in stale_keys:
                    self._storage.pop(k, None)

        if path in ("/health", "/ready"):
            return self.get_response(request)

        client_ip = self._get_client_ip(request)
        is_sensitive = any(path.startswith(prefix) for prefix in self.SENSITIVE_PREFIXES)
        limit = self.SENSITIVE_LIMIT if is_sensitive else self.DEFAULT_LIMIT

        # Build multidimensional keys (IP + endpoint type, plus account dimension if provided)
        account_id = None
        if hasattr(request, "user") and getattr(request.user, "is_authenticated", False):
            account_id = getattr(request.user, "employee_id", None)
        elif request.method == "POST":
            account_id = request.POST.get("employee_id", "").strip().upper() or None

        keys_to_check = [f"{client_ip}:{'sensitive' if is_sensitive else 'general'}"]
        if account_id and is_sensitive:
            keys_to_check.append(f"acc:{account_id}:sensitive")

        with self._lock:
            for k in keys_to_check:
                timestamps = self._storage.setdefault(k, [])
                timestamps[:] = [t for t in timestamps if t > cutoff]

                if len(timestamps) >= limit:
                    response = HttpResponse(
                        "Too Many Requests: Rate limit exceeded. Please retry in 60 seconds.",
                        status=429,
                        content_type="text/plain",
                    )
                    response["Retry-After"] = "60"
                    return response

                timestamps.append(now)

            if len(self._storage) > 500:
                stale_keys = [k for k, ts in self._storage.items() if not ts or ts[-1] <= cutoff]
                for k in stale_keys:
                    self._storage.pop(k, None)

        # Record in centralized database for distributed cluster coordination
        try:
            from apps.core.models import RateLimitRecord

            # Check database sliding window count across all workers
            for k in keys_to_check:
                db_count = RateLimitRecord.objects.filter(key=k, timestamp__gt=cutoff).count()
                if db_count >= limit:
                    response = HttpResponse(
                        "Too Many Requests: Rate limit exceeded. Please retry in 60 seconds.",
                        status=429,
                        content_type="text/plain",
                    )
                    response["Retry-After"] = "60"
                    return response

                RateLimitRecord.objects.create(key=k, timestamp=now)

            # Periodic background pruning of expired records
            RateLimitMiddleware._cleanup_counter += 1
            if RateLimitMiddleware._cleanup_counter % 50 == 0:
                RateLimitRecord.objects.filter(timestamp__lte=cutoff).delete()
        except Exception as e:
            logger.debug("Failed to record rate limit entry: %s", e)

        return self.get_response(request)
