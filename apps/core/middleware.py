import logging
import secrets
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
        # Per-request nonce: only <script nonce=...> tags and same-origin files run. No inline handlers.
        request.csp_nonce = secrets.token_urlsafe(18)
        response = self.get_response(request)

        # Content-Security-Policy: restrict source origins for scripts, styles, frames
        csp_directives = [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{request.csp_nonce}'",
            "script-src-attr 'none'",
            f"style-src 'self' 'nonce-{request.csp_nonce}'",
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
            "Permissions-Policy", "camera=(self), microphone=(), geolocation=(self)"
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
    SENSITIVE_LIMIT = getattr(
        settings, "RATE_LIMIT_SENSITIVE_LIMIT", 5
    )  # max 5 requests per window for sensitive auth endpoints
    DEFAULT_LIMIT = getattr(
        settings, "RATE_LIMIT_DEFAULT_LIMIT", 300
    )  # general requests per window
    WINDOW_SECONDS = 60

    _storage: Dict[str, List[float]] = {}
    _cleanup_counter: int = 0

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def reset(cls):
        """Reset rate limiter state (used in testing)."""
        cls._storage.clear()
        cls._cleanup_counter = 0
        try:
            from apps.core.models import RateLimitBucket, RateLimitRecord

            RateLimitBucket.objects.all().delete()
            RateLimitRecord.objects.all().delete()
        except Exception as e:
            logger.debug("Failed to reset rate limit records: %s", e)
        try:
            from apps.accounts.auth.stepup import _USER_SUDO_CACHE

            _USER_SUDO_CACHE.clear()
        except Exception as e:
            logger.debug("Failed to clear sudo cache: %s", e)


    def _get_client_ip(self, request) -> str:
        remote_addr = request.META.get("REMOTE_ADDR", "127.0.0.1")
        trusted_proxies = getattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "::1", "localhost", "testclient"])

        # 1. Direct Cloudflare header if request arrives via trusted reverse proxy
        cf_ip = request.META.get("HTTP_CF_CONNECTING_IP")
        if cf_ip and remote_addr in trusted_proxies:
            client_ip = cf_ip.strip()
            request.client_ip = client_ip
            return client_ip

        # 2. X-Forwarded-For: evaluated only when REMOTE_ADDR is an authenticated trusted proxy
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for and remote_addr in trusted_proxies:
            client_ip = x_forwarded_for.split(",")[0].strip()
            request.client_ip = client_ip
            return client_ip

        request.client_ip = remote_addr
        return remote_addr

    def _render_429_response(self, request, retry_after: int = 60) -> HttpResponse:
        """Render a branded HTML 429 page or JSON response with Retry-After header."""
        retry_str = str(retry_after)
        wants_json = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
            or request.path_info.startswith("/api/")
        )
        if wants_json:
            from django.http import JsonResponse

            resp = JsonResponse(
                {
                    "error": "Too Many Requests",
                    "message": f"Too Many Requests: Rate limit exceeded. Please retry in {retry_after} seconds.",
                    "retry_after": retry_after,
                },
                status=429,
            )
            resp["Retry-After"] = retry_str
            return resp

        from django.template.loader import render_to_string

        try:
            content = render_to_string(
                "core/429.html",
                {"retry_after": retry_after, "path": request.path_info},
                request=request,
            )
            resp = HttpResponse(content, status=429, content_type="text/html; charset=utf-8")
        except Exception as exc:
            logger.warning("Failed to render core/429.html, falling back to plain text: %s", exc)
            resp = HttpResponse(
                "Too Many Requests: Rate limit exceeded. Please retry in 60 seconds.",
                status=429,
                content_type="text/plain; charset=utf-8",
            )

        resp["Retry-After"] = retry_str
        return resp

    def __call__(self, request):  # noqa: C901
        self._get_client_ip(request)
        path = request.path_info
        if path.startswith("/static/"):
            return self.get_response(request)

        if len(self._storage) > 500:
            import time
            cutoff = time.time() - self.WINDOW_SECONDS
            stale_keys = [k for k, ts in list(self._storage.items()) if not ts or ts[-1] <= cutoff]
            for k in stale_keys:
                self._storage.pop(k, None)

        if path in ("/health", "/ready"):
            return self.get_response(request)

        client_ip = self._get_client_ip(request)

        # Differentiate GET vs. POST: Standard auth page views (GET) are unmetered
        # rendering requests evaluated against DEFAULT_LIMIT, while sensitive thresholds (SENSITIVE_LIMIT = 5)
        # strictly protect sensitive POST credential submissions.
        is_sensitive = False
        if any(path.startswith(prefix) for prefix in self.SENSITIVE_PREFIXES):
            if (
                path.startswith(("/auth/activate", "/auth/login", "/auth/recovery"))
                and request.method == "GET"
            ):
                is_sensitive = False
            else:
                is_sensitive = True

        limit = self.SENSITIVE_LIMIT if is_sensitive else self.DEFAULT_LIMIT

        # Build multidimensional keys (IP + endpoint type, plus account dimension if provided)
        account_id = None
        if hasattr(request, "user") and getattr(request.user, "is_authenticated", False):
            account_id = getattr(request.user, "employee_id", None)
        elif request.method == "POST":
            account_id = request.POST.get("employee_id", "").strip().upper() or None

        keys_to_check = [f"{client_ip}:{'sensitive' if is_sensitive else 'general'}"]
        if account_id and is_sensitive:
            # Account + IP together: one attacker's requests for a victim's ID cannot burn the victim's
            # own quota from another address. Per-IP is limited separately above; account lockout is
            # enforced separately by LoginAttempt.
            keys_to_check.append(f"acc:{account_id}:{client_ip}:sensitive")

        try:
            from datetime import timedelta
            from django.db import IntegrityError, transaction
            from django.utils import timezone
            from apps.core.models import RateLimitBucket, RateLimitRecord

            now_dt = timezone.now()
            now_ts = now_dt.timestamp()
            window_delta = timedelta(seconds=self.WINDOW_SECONDS)

            for k in keys_to_check:
                RateLimitRecord.objects.create(key=k, timestamp=now_ts)
                with transaction.atomic():
                    bucket = (
                        RateLimitBucket.objects.select_for_update()
                        .filter(bucket_key=k)
                        .first()
                    )
                    if not bucket:
                        # Two first requests can both see "no row" (a row lock cannot lock a missing row):
                        # the loser hits the unique key. Re-read the winner's row and count against it.
                        try:
                            with transaction.atomic():
                                bucket = RateLimitBucket.objects.create(
                                    bucket_key=k,
                                    count=1,
                                    reset_at=now_dt + window_delta,
                                )
                        except IntegrityError:
                            bucket = RateLimitBucket.objects.select_for_update().get(bucket_key=k)
                            bucket.count += 1
                            bucket.save(update_fields=["count", "updated_at"])
                    elif now_dt >= bucket.reset_at:
                        bucket.count = 1
                        bucket.reset_at = now_dt + window_delta
                        bucket.save(update_fields=["count", "reset_at", "updated_at"])
                    else:
                        bucket.count += 1
                        bucket.save(update_fields=["count", "updated_at"])

                    if bucket.count > limit:
                        return self._render_429_response(request, retry_after=self.WINDOW_SECONDS)

            RateLimitMiddleware._cleanup_counter += 1
            if RateLimitMiddleware._cleanup_counter % 50 == 0:
                RateLimitBucket.objects.filter(reset_at__lt=now_dt - timedelta(minutes=5)).delete()
        except Exception as e:
            if "database is locked" in str(e).lower():
                logger.debug("Rate limit SQLite lock contention: %s", e)
            else:
                logger.error("Rate limit database check error: %s", e)
            if is_sensitive and not getattr(settings, "DEBUG", True):
                # Fail-closed for sensitive authentication and recovery endpoints in production
                return HttpResponse(
                    "Security throttle service temporarily unavailable. Please retry later.",
                    status=503,
                    content_type="text/plain; charset=utf-8",
                )

        return self.get_response(request)

