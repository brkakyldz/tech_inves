# TechInves

Self-hosted, single-user tool for scoring and researching the US technology
sector. Nothing is scheduled: every run is triggered by hand, from the UI's run
panel or the CLI. See [README.md](README.md) for setup and screenshots.

## Architecture

Two layers that meet in the database:

- **Scoring** (`src/techinves/scoring/`) — deterministic, LLM-free. Fundamentals
  from SEC EDGAR `companyfacts`, price/market cap from FMP. Each company is
  scored 0-100 by percentile rank *within its cohort* across four categories
  (valuation, growth, quality, financial health), plus a separate risk score.
  Percentile normalization is a whole-cohort operation — scoring one ticker
  still requires every peer's raw data.
- **Report pipeline** (`pipeline/`) — a LangGraph graph, run only on demand. A
  cheap search probe ranks the watchlist and picks 3-4 highlights; research
  branches fan out over those plus ten fixed macro topics; an LLM synthesizes a
  draft; a rule-based + LLM-judge verifier grades it (`pass`,
  `pass_with_flags`, `degraded_publish`, `block`). Every verdict is stored and
  surfaced — a flawed report is published *with* its violations, never
  silently discarded.

`pipeline` imports `techinves`; the dependency never runs the other way.

The API (`src/techinves/api/`) is FastAPI + async SQLAlchemy. `/v1/companies*`,
`/v1/meta`, `/v1/reports*` are read-only; `/v1/runs*` is the only write surface.
Runs execute in a background thread with one in-flight run per trigger type,
enforced by a unique partial index — closing the page does not stop a run.

The front end (`front-end/`) is Next.js: server components read the cached
read-only endpoints, the run panel polls `/v1/runs` client-side with a
monotonic log cursor.

## Conventions

- **Run identity.** A run is the unit of work, keyed by a timestamped id. There
  is no week identity anywhere — two runs on the same afternoon are normal. The
  research *window* is a rolling last-7-days (`PIPELINE_RESEARCH_LOOKBACK_DAYS`),
  which is a retrieval setting, not an identity.
- **Degrade, never fake.** A missing optional key (`FRED_API_KEY`, `EXA_API_KEY`)
  skips a leg and says so; a missing required key refuses the trigger by name.
  Missing score data is rendered as an explicit reason, never omitted or
  interpolated.
- **Env vars** are read through `techinves.config` / `pipeline.config`, never
  `os.environ` at call sites. `.env.example` lists every one the code reads.
- **Schema changes** go through Alembic (`alembic revision --autogenerate`).
  The chain is single-headed; keep it that way.

## Commands

```bash
pip install -e ".[api-dev,pipeline]"   # everything
alembic upgrade head
uvicorn techinves.api.main:app --reload
npm run dev --prefix front-end

pytest                                 # back end + pipeline
npm test --prefix front-end
```

`pytest` skips collection of `tests/api`, `tests/runs` and `tests/pipeline`
unless the matching extras are installed; the header names what was skipped.
No test hits the network — clients are injected everywhere.

## Layout

```
src/techinves/    scoring engine, FastAPI app, run service, DB models
pipeline/         LangGraph report pipeline (research, synthesis, verifier)
front-end/        Next.js site
data/             watchlist + ticker->name table
tests/            unit / golden / api / runs / pipeline
alembic/          migrations
```

<!-- Local, untracked working notes for this checkout (absent in a fresh
     clone — the import simply resolves to nothing). -->
@CLAUDE.local.md
