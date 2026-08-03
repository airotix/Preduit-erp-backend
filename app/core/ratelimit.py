"""Lightweight in-process sliding-window rate limiter for sensitive endpoints.

Keyed by (bucket, client-ip). This is per-process — fine for a single instance
or as a first line of defence; a multi-instance deployment should move the
counters to Redis (redis_url is already configured). Disabled via
settings.rate_limit_enabled (e.g. in tests).
"""
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings

settings = get_settings()

# bucket:ip -> deque[timestamps]
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, *, max_attempts: int | None = None, window_seconds: int | None = None):
    """Dependency factory: throttle `bucket` per client IP."""
    cap = max_attempts or settings.rate_limit_max_attempts
    window = window_seconds or settings.rate_limit_window_seconds

    def _guard(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        key = f"{bucket}:{_client_ip(request)}"
        now = time.monotonic()
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= cap:
            retry = int(window - (now - q[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts — please wait a moment and try again.",
                headers={"Retry-After": str(max(retry, 1))},
            )
        q.append(now)

    return Depends(_guard)
