# Research pipeline (LangGraph + Tavily)

Implements the qualitative-research fan-out, synthesis, and verifier nodes
from `reports/research/ARCHITECTURE_PROPOSAL.md` §2.2 (b, e, f). Out of scope: the
scoring engine / financial data (`src/techinves`) and the (d.5) highlight-ticker
selection node. The proposal's email delivery, S3, EventBridge/Fargate infra and
human approval gate are not merely out of scope here — ADR 0010 removed them from
the product entirely.

## Layout

- `schemas.py` — `ReportState`, `Finding`, `FailureNote`, `VerifierReport`.
  `Finding` has no numeric fields by design (schema-level half of the
  numeric-leak guard).
- `research/` — one Tavily search + one structured-output LLM call per
  branch (`agent.py`), a thin `TavilySearcher` wrapper (`tavily_client.py`)
  kept behind a `Protocol` so tests inject a fake instead of hitting the
  live API. Retries use exponential backoff
  (`PIPELINE_RESEARCH_MAX_RETRIES` / `PIPELINE_RESEARCH_RETRY_BACKOFF_SECONDS`,
  default 2 retries / 1s base) inside the node itself rather than
  LangGraph's `RetryPolicy`, since that re-raises after exhausting attempts
  and would abort the whole fan-out instead of isolating one branch as a
  `FailureNote`.
- `synthesis/` — turns `research_findings` + `scores`/`financials` into a
  draft report; prompted to reference numbers only via `{{ticker.field}}`
  placeholders, never write them itself.
- `verifier/` — `rules.py` is the deterministic, LLM-free pre-screen (number
  leak scan, citation-fidelity check, disclaimer check, low-reliability
  labeling check) and is authoritative for `block`; `node.py` adds an LLM
  consistency layer on top when the rule pre-screen doesn't already block.
- `graph.py` — assembles the LangGraph `StateGraph`: `init` → `Send`
  fan-out over `highlight_tickers` (company) + `macro_topics` (macro) into a
  shared `research_branch` node → `synthesis` → `verifier`.
- `fixtures/mock_data.py` — stand-in `scores`/`financials` shaped like
  `report_scoring_metadology.md`'s score block, until the real scoring
  engine is wired in as graph input.
- `config.load_watchlist_tickers()` — reads `data/watchlist.yaml` directly
  (PyYAML, no import from `src/techinves`) and flattens all cohorts into one
  ticker list, for feeding the *entire* watchlist into `highlight_tickers`
  instead of a hand-picked subset.
- `run.py` — `python -m pipeline.run` CLI entrypoint. Fails fast via
  `get_openai_api_key()`/`get_tavily_api_key()` before building anything.
  `run_pipeline()` wires `load_watchlist_tickers()`, the covered_events
  store, `default_run_config()`, and `observability.run_with_summary()`
  together for one on-demand run; `graph`/`llm`/`searcher` are injectable so
  tests don't need real API keys.
- `observability.py` — `run_with_summary(graph, state_input, config=...)`
  times `graph.invoke()`, logs a one-line run summary (branch counts,
  findings, failures, verdict, leak/citation-violation counts) plus one
  warning per `FailureNote`, and returns `(result, RunSummary)`.
  `research_branch_node` (`graph.py`) also logs a per-branch duration line.
  No external tracing (LangSmith etc.) is wired in.
- `storage/covered_events_store.py` — load/save the `CoveredEvent` de-dup log
  to/from `data/covered_events.json`, and `update_covered_events()` to merge
  a finished run's `research_findings` into it (bumps `last_updated_run` on
  a title match, otherwise appends a new event) and prune it to the trailing
  `COVERED_EVENTS_TRAILING_RUNS` runs (ADR 0010 §9). Not a graph node — a
  plain helper the caller runs before/after `graph.invoke()`. Without this,
  the `covered_events` de-dup context (§2.2b) reset to empty every run.

## Running

```bash
pip install -e ".[pipeline]"
cp .env.example .env   # fill in TAVILY_API_KEY, OPENAI_API_KEY
```

Via the CLI (`pipeline/run.py`) — full watchlist, current covered_events
carried forward automatically, fails fast if a key is missing:

```bash
python -m pipeline.run                                          # run id generated
python -m pipeline.run --label before-the-fed-meeting           # run id = the label
python -m pipeline.run --tickers NVDA MSFT                      # subset
```

Or from Python, wiring the same pieces by hand:

```python
from langchain_openai import ChatOpenAI

from pipeline.config import LLM_MODEL, load_watchlist_tickers
from pipeline.fixtures.mock_data import MOCK_FINANCIALS, MOCK_SCORES
from pipeline.graph import build_graph, default_run_config
from pipeline.research.tavily_client import LiveTavilySearcher

graph = build_graph(searcher=LiveTavilySearcher(), llm=ChatOpenAI(model=LLM_MODEL))
result = graph.invoke(
    {
        "run_id": "20260810T093000-a1b2c3",
        "as_of": date(2026, 8, 10),  # research-window end date, keys nothing
        "highlight_tickers": load_watchlist_tickers(),  # all ~42 companies
        "macro_topics": [...],  # pipeline.config.MACRO_TOPICS
        "covered_events": [],
        "scores": MOCK_SCORES,
        "financials": MOCK_FINANCIALS,
    },
    config=default_run_config(),  # caps concurrent research branches at RESEARCH_CONCURRENCY
)
```

Running the entire watchlist means one Tavily search + one LLM structured-
output call per company (~42 branches instead of a handful) — cheap on
Tavily (`reports/research/research_tavily_integration.md`: ~2 credits/advanced search
× ~50 topics ≈ 100-150 credits/week, well under the 1,000/month free tier)
but scales the LLM call count 1:1 with watchlist size, so
`default_run_config()`'s concurrency cap matters here.

## Tests

```bash
pytest tests/pipeline
```

All 31 tests run fully offline against fake `TavilySearcher`/`BaseChatModel`
doubles (`tests/pipeline/conftest.py`) — no API keys required. The full
graph, live Tavily + live `gpt-5.6-luna`, has also been run end-to-end
manually and passes the verifier (`pass` verdict, no number-leak or
citation violations).

## Known gaps / not built here

- `highlight_tickers`/`macro_topics`/`scores`/`financials` are plain graph
  inputs here, not computed — wiring the real (d.5) selection node (which
  would pick a cost-bounded subset by score movement) and the real scoring
  engine is separate work in `src/techinves`. Right now the caller decides
  between a hand-picked subset or the full watchlist via
  `load_watchlist_tickers()`.
- No checkpointer/persistence for graph state itself (mid-run resume). The
  `covered_events` JSON store (`storage/covered_events_store.py`) covers
  cross-run de-dup, but is caller-invoked, not automatic.
- A full-watchlist run has been validated structurally (concurrency-capped
  fan-out, `load_watchlist_tickers()` reads all 42 tickers correctly) but
  not yet run live end-to-end against all ~42 companies at once — only
  smoke-tested with a small ticker subset live.
