"""FastAPI application entrypoint.

Run locally: `uvicorn techinves.api.main:app --reload`
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from techinves import __version__
from techinves.api import seed_mock
from techinves.api.rate_limit import InProcessRateLimitMiddleware
from techinves.api.routers import companies, health, meta, reports, runs
from techinves.db.session import make_engine
from techinves.runs.keys import app_mode
from techinves.runs.service import get_run_service

logger = logging.getLogger(__name__)

# BACKEND_IMPLEMENTATION_PLAN.md §7: no wildcard, prod domain + Vercel preview pattern only.
DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000"]
VERCEL_PREVIEW_REGEX = r"^https://techinves-[a-z0-9-]+\.vercel\.app$"


def _allowed_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    extra = [o.strip() for o in raw.split(",") if o.strip()]
    return DEFAULT_ALLOWED_ORIGINS + extra


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup reconciliation, and nothing else (Faz 3.2).

    **Ordering.** Everything in the `yield`'s prologue runs before uvicorn
    starts accepting connections, so reconciliation completes before the
    first request exists -- and therefore before the in-flight lock can be
    consulted by anything. `RunService.startup()` also sets the flag that
    `trigger()` refuses without, so the ordering is enforced by the service
    rather than merely established by this file: even a caller that reaches
    the service without going through the app cannot consult the lock first.

    **Failure.** `ReconciliationFailed` / `MultiWorkerRefused` propagate out
    of the prologue, which aborts startup. That is deliberate: an application
    that cannot tell whether the lock is held by live work or by a row left
    behind by a dead process must fail loudly rather than serve, because the
    quiet alternative is refusing every trigger forever with no explanation.
    """
    service = get_run_service()
    stale = await service.startup()
    if stale:
        logger.warning(
            "startup reconciliation failed %d abandoned run(s): %s", len(stale), ", ".join(stale)
        )

    # Faz 6 (ADR 0010 §7): a keyless clone comes up populated, not empty.
    # Gated on demo mode -- a live clone (real keys, real workflow) must
    # never have fixture data injected under it just because its database
    # happens to be empty on first boot. `seed_if_empty` re-checks
    # emptiness itself, so this is safe even if reconciliation and startup
    # somehow race with another process's first insert.
    mode = app_mode()
    if mode == "demo":
        seed_engine = make_engine()
        try:
            seeded = await seed_mock.seed_if_empty(seed_engine)
        finally:
            await seed_engine.dispose()
        if seeded:
            logger.info("demo mode: seeded %d companies into an empty database", seeded)
    else:
        logger.info("live mode: every trigger's required API key is present")

    yield


def create_app(*, lifespan_handler=lifespan) -> FastAPI:
    """`lifespan_handler` is injectable so tests that only exercise the
    read-only routers can build an app without a reconciliation pass against
    whatever database happens to be configured."""
    app = FastAPI(
        title="TechInves API",
        version=__version__,
        description=(
            "API serving pre-computed company scores and reports for the TechInves "
            "front-end, plus the on-demand run-trigger endpoints (ADR 0010)."
        ),
        lifespan=lifespan_handler,
    )

    # Starlette wraps middleware in reverse add order (last added = outermost),
    # so the rate limiter is added first here: CORSMiddleware ends up outermost
    # and applies its headers to every response, including 429s from the
    # limiter -- otherwise a rate-limited browser request looks like a CORS
    # failure instead of a rate limit.
    app.add_middleware(InProcessRateLimitMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_origin_regex=VERCEL_PREVIEW_REGEX,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(companies.router)
    app.include_router(meta.router)
    app.include_router(reports.router)
    app.include_router(runs.router)

    return app


app = create_app()
