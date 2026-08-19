"""Tests InProcessRateLimitMiddleware: the sweep that bounds `_hits` memory
growth, and X-Forwarded-For-aware client-IP extraction for the reverse-proxy
deployment case (reports/research/research_backend_deployment.md recommends
Fly.io/Railway, both of which sit the app behind a proxy/load balancer).
"""

from __future__ import annotations

import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from techinves.api.rate_limit import InProcessRateLimitMiddleware


def _make_app(limit: int = 300, window_seconds: int = 60) -> Starlette:
    async def ok(request: Request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/thing", ok)])
    app.add_middleware(InProcessRateLimitMiddleware, limit=limit, window_seconds=window_seconds)
    return app


def test_client_ip_prefers_x_forwarded_for_right_most_entry():
    middleware = InProcessRateLimitMiddleware(app=lambda scope, receive, send: None)

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1, 10.0.0.2")],
        "client": ("10.0.0.2", 12345),
    }
    req = Request(scope)
    # The right-most hop is what the nearest proxy actually observed --
    # trustworthy even when the left-most (client-supplied) entries are
    # spoofed.
    assert middleware._client_ip(req) == "10.0.0.2"


def test_client_ip_ignores_spoofed_left_most_x_forwarded_for_entry():
    middleware = InProcessRateLimitMiddleware(app=lambda scope, receive, send: None)

    scope = {
        "type": "http",
        # An attacker prepends an arbitrary address to spoof their identity.
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 9.9.9.9")],
        "client": ("9.9.9.9", 12345),
    }
    req = Request(scope)
    assert middleware._client_ip(req) != "1.2.3.4"
    assert middleware._client_ip(req) == "9.9.9.9"


def test_client_ip_falls_back_to_direct_connection_without_proxy_header():
    middleware = InProcessRateLimitMiddleware(app=lambda scope, receive, send: None)
    scope = {"type": "http", "headers": [], "client": ("198.51.100.9", 12345)}
    req = Request(scope)
    assert middleware._client_ip(req) == "198.51.100.9"


def test_sweep_drops_keys_whose_hits_all_aged_out():
    middleware = InProcessRateLimitMiddleware(app=lambda scope, receive, send: None, window_seconds=60)
    now = time.monotonic()

    # Simulate two IPs that each made one request long ago (outside the
    # window) and have not been seen since -- the memory-leak scenario:
    # nothing ever prunes these because dispatch() only prunes a key's own
    # deque when that same key makes another request.
    middleware._hits["1.2.3.4"].append(now - 1000)
    middleware._hits["5.6.7.8"].append(now - 1000)
    # And one IP that is still active (recent hit) -- must survive the sweep.
    middleware._hits["9.9.9.9"].append(now - 1)

    assert len(middleware._hits) == 3
    middleware._sweep_stale_keys(now)

    assert list(middleware._hits.keys()) == ["9.9.9.9"]


def test_sweep_runs_periodically_and_bounds_hits_dict_size():
    # Drive dispatch() directly on a middleware instance so `_hits` and
    # `_last_sweep` can be inspected/seeded (Starlette's add_middleware()
    # rebuilds instances internally, so there's no handle on one via the app).
    middleware = InProcessRateLimitMiddleware(app=lambda scope, receive, send: None, window_seconds=1)
    from techinves.api.rate_limit import SWEEP_INTERVAL_SECONDS

    now = time.monotonic()
    middleware._last_sweep = now - SWEEP_INTERVAL_SECONDS - 1
    middleware._hits["stale-ip"].append(now - 1000)

    class DummyRequest:
        url = type("u", (), {"path": "/thing"})()
        headers = {}
        client = type("c", (), {"host": "6.6.6.6"})()

    import asyncio

    async def call_next(_request):
        return PlainTextResponse("ok")

    asyncio.run(middleware.dispatch(DummyRequest(), call_next))

    # The stale IP from before the sweep window is gone; only the IP that
    # just made a request remains.
    assert "stale-ip" not in middleware._hits
    assert "6.6.6.6" in middleware._hits


def test_rate_limit_still_enforced_after_sweep_logic_added():
    app = _make_app(limit=2, window_seconds=60)
    with TestClient(app) as client:
        assert client.get("/thing").status_code == 200
        assert client.get("/thing").status_code == 200
        resp = client.get("/thing")
        assert resp.status_code == 429
