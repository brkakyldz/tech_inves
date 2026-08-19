"""LangGraph + Tavily qualitative research pipeline for TechInves weekly reports.

Scope of this package (see reports/research/ARCHITECTURE_PROPOSAL.md): the qualitative
research fan-out (Tavily agents, company + macro), the synthesis/writer node,
and the verifier node. It does NOT contain the scoring engine, financial data
fetching, email delivery, or deployment infra (`src/techinves` and the rest of
the architecture doc's pipeline) -- those are out of scope here. `financials`/
`scores`/`highlight_tickers` are consumed as plain inputs (see
`pipeline/fixtures/mock_data.py` for the shape used until the real scoring
engine is wired in).
"""
