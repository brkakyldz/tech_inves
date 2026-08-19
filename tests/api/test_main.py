"""Middleware ordering: CORS must be the outermost layer so its headers land
on every response, including 429s from the rate limiter -- otherwise a
rate-limited browser request looks like a CORS failure instead of a rate
limit to the client.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from techinves.api import main as main_module


async def test_cors_headers_present_on_rate_limited_response(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
    app = main_module.create_app()

    # Force the limiter to reject the very first request, before the
    # middleware stack is built on first use.
    for middleware in app.user_middleware:
        if middleware.cls is main_module.InProcessRateLimitMiddleware:
            middleware.kwargs["limit"] = 0

    transport = ASGITransport(app=app)
    origin = main_module.DEFAULT_ALLOWED_ORIGINS[0]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/scores/highlights", headers={"Origin": origin})

    assert r.status_code == 429
    assert r.headers.get("access-control-allow-origin") == origin
