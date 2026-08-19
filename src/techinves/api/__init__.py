"""Read-only HTTP API serving the Next.js front-end.

Imports `techinves.models` (the scoring engine's Pydantic contract) but never
calls `techinves.scoring.engine.score_watchlist()` at request time -- scores
are precomputed and read from Postgres. See
`reports/research/BACKEND_IMPLEMENTATION_PLAN.md` Section 2.2.
"""
