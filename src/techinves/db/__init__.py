"""Async SQLAlchemy layer for the read-only API and the ingestion job.

Kept deliberately separate from `techinves.models` (the Pydantic contract the
scoring engine speaks): the DB schema evolves with front-end/API needs, the
engine's models evolve with the scoring methodology. See
`reports/research/BACKEND_IMPLEMENTATION_PLAN.md` Section 4 for the rationale.
"""
