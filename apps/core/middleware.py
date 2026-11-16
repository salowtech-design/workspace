import threading
import time
from typing import Dict, List

from django.http import HttpResponse


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

    def __call__(self, request):
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
    Sliding window rate limiter defending against denial-of-service and brute force abuse.
    Applies stricter limits to authentication and public verification endpoints.
    """

    # Endpoints with strict thresholds (e.g. 60 requests / minute)
    SENSITIVE_PREFIXES = (
        "/auth/login",
        "/auth/activate",
        "/verify",
    )
    SENSITIVE_LIMIT = 60  # max requests per window
    DEFAULT_LIMIT = 300  # general requests per window
    WINDOW_SECONDS = 60

    _storage: Dict[str, List[float]] = {}
    _lock = threading.Lock()

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def reset(cls):
        """Reset rate limiter state (used in testing)."""
        with cls._lock:
            cls._storage.clear()

    def __call__(self, request):
        # Exclude static assets
        path = request.path_info
        if path.startswith("/static/"):
            return self.get_response(request)

        # Resolve client IP
        ip = request.META.get("HTTP_X_FORWARDED_FOR")
        if ip:
            client_ip = ip.split(",")[0].strip()
        else:
            client_ip = request.META.get("REMOTE_ADDR", "127.0.0.1")

        now = time.time()
        is_sensitive = any(path.startswith(prefix) for prefix in self.SENSITIVE_PREFIXES)
        limit = self.SENSITIVE_LIMIT if is_sensitive else self.DEFAULT_LIMIT
        key = f"{client_ip}:{':sensitive' if is_sensitive else ':general'}"

        with self._lock:
            timestamps = self._storage.setdefault(key, [])
            cutoff = now - self.WINDOW_SECONDS
            # Purge timestamps outside the sliding window
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

            # Evict stale keys to prevent memory leak under heavy/diverse IP access
            if len(self._storage) > 500:
                stale_keys = [k for k, ts in self._storage.items() if not ts or ts[-1] <= cutoff]
                for k in stale_keys:
                    self._storage.pop(k, None)

        return self.get_response(request)
