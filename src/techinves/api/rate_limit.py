"""Minimal in-process, per-IP rate limiting.

Deliberately not Redis-backed (BACKEND_IMPLEMENTATION_PLAN.md §6/§7): v1 runs
a single API instance, so a shared counter store buys nothing. When the API
scales horizontally, this is replaced by Cloudflare edge rate limiting or a
Redis-backed limiter -- see the plan's scaling roadmap (§11, Aşama 1).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

WINDOW_SECONDS = 60
# 60/min (BACKEND_IMPLEMENTATION_PLAN.md §7's initial suggestion) turned out
# too tight in practice: the front-end's `next build` calls generateStaticParams
# for all ~42 companies from a single build-machine IP, which alone is
# ~90 requests/min (list + per-ticker detail + per-ticker history). 300/min/IP
# keeps real per-visitor abuse protection while not breaking SSG builds.
DEFAULT_LIMIT = 300
EXEMPT_PATHS = {"/health"}
# How often (in seconds) a full sweep of `_hits` runs to drop keys whose
# deque has drained to empty. Without this, `_hits` is a dict that only ever
# grows: every distinct client IP that has *ever* made one request gets a
# permanent entry, even long after its own hits have aged out of the window,
# because pruning otherwise only happens lazily on that same IP's next
# request. A multiple of the window keeps the sweep infrequent relative to
# request volume while still bounding memory to recently-active IPs.
SWEEP_INTERVAL_SECONDS = WINDOW_SECONDS * 10


class InProcessRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int = DEFAULT_LIMIT, window_seconds: int = WINDOW_SECONDS) -> None:
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_sweep = time.monotonic()

    @staticmethod
    def _client_ip(request: Request) -> str:
        # Deployment topology (reports/research/research_backend_deployment.md
        # §"Seçenek C") runs the API behind a PaaS-managed proxy/load balancer
        # (Fly.io/Railway), and a Cloudflare edge is called out as a likely
        # future addition -- in both cases the socket peer seen by uvicorn is
        # the proxy, not the visitor, so `request.client.host` alone would key
        # every visitor's rate limit off the same proxy IP. But the left-most
        # hop in X-Forwarded-For is client-supplied and trivially spoofable by
        # anyone who can reach the API directly -- prepend any value there and
        # the limiter keys on it instead of the real client. Take the
        # right-most hop instead: that's the address the nearest (trusted)
        # proxy actually observed, which is safe whether or not there's a
        # proxy chain in front. Falls back to the direct connection IP when
        # the header is absent (e.g. local dev, or no proxy in front at all).
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            last = forwarded_for.split(",")[-1].strip()
            if last:
                return last
        return request.client.host if request.client else "unknown"

    def _sweep_stale_keys(self, now: float) -> None:
        """Drop `_hits` entries whose deque has no hits left in the window.
        Bounds `_hits` to currently-active IPs instead of every IP ever seen.
        """
        stale_keys = []
        for key, hits in self._hits.items():
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if not hits:
                stale_keys.append(key)
        for key in stale_keys:
            del self._hits[key]
        self._last_sweep = now

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        client_ip = self._client_ip(request)
        now = time.monotonic()
        hits = self._hits[client_ip]

        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()

        if len(hits) >= self.limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(self.window_seconds)},
            )

        hits.append(now)

        if now - self._last_sweep > SWEEP_INTERVAL_SECONDS:
            self._sweep_stale_keys(now)

        return await call_next(request)
