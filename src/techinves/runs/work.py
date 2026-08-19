"""The three units of work the run service wraps (ADR 0010 §1).

| trigger_type | what it does                        | cost |
|---|---|---|
| `scores`     | `score_watchlist()` -> `ingest()`   | ~40 EDGAR + ~40 FMP requests, no LLM |
| `report`     | the full pipeline over the watchlist | the whole research/synthesis/verifier chain |
| `company`    | the same pipeline, narrowed to one ticker | one branch of it |

They are three functions rather than one parameterised one because ADR 0010
§1 keeps them separately invocable: their costs differ by orders of
magnitude, and bundling them makes the cheap deterministic path pay for the
expensive probabilistic one on every press.

`company` is emphatically **not** a second pipeline. The LangGraph fan-out
already takes a ticker list, so the company trigger narrows the existing
entry point -- same `run_pipeline`, a one-element `tickers`, and the same
ticker pinned as the highlight so the run actually researches what it was
asked about instead of re-deriving a selection from a list of one.

Every function here is synchronous and blocking: `RunService` executes them
in a worker thread (see its docstring), and each drives its own
`asyncio.run(...)` underneath, as the pipeline's storage layer already does.

None of this can be exercised against the live providers by an agent in this
repository -- the API keys live behind a credential guard
(`reports/backlog/live-run-verification-blocked-by-credential-guard.md`).
The service's tests inject fakes in place of this module; what is verified
here is the wiring, not a live run.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from techinves.runs.service import RunContext

logger = logging.getLogger(__name__)

_WATCHLIST_TABLE = Path(__file__).resolve().parents[3] / "data" / "WATCHLIST.md"
_NAME_ROW_RE = re.compile(r"^\|\s*([A-Z][A-Z.\-]{0,6})\s*\|\s*([^|]+?)\s*\|\s*$", re.MULTILINE)


def load_company_names() -> dict[str, str]:
    """Ticker -> company name, from the canonical table.

    `ScoreBlock` carries no company name and `data/watchlist.yaml` holds
    tickers only, so without this every seeded company would be named after
    its own ticker. Same source and same parse the weekly workflow used; a
    missing or unparseable file degrades to an empty map (companies keep
    their ticker as a name) rather than failing the run.
    """
    try:
        table = _WATCHLIST_TABLE.read_text(encoding="utf-8")
    except OSError:
        logger.warning("company-name table not readable at %s", _WATCHLIST_TABLE)
        return {}
    return {t: n for t, n in _NAME_ROW_RE.findall(table) if t != "Ticker"}


def run_scores(ctx: RunContext) -> int:
    """ADR 0010 §1's "Refresh scores": score the whole watchlist, then load
    the result into the database under this run's id.

    The intermediate JSON file is the same handoff the CLI and the workflow
    use (`techinves-score score-watchlist --output` -> `techinves-ingest`),
    kept rather than short-circuited so the on-demand path and the CLI path
    produce byte-identical input to the ingest validator.

    `manage_run_row=False`: the executor owns this run's row. Letting
    `ingest()` write its own terminal status would release the in-flight lock
    while the executor still considered the run in flight.
    """
    import asyncio

    from techinves.api.ingest import ingest
    from techinves.data.raw_facts import build_default_provider
    from techinves.db.session import make_engine
    from techinves.scoring.engine import score_watchlist

    ctx.log("scoring the watchlist (EDGAR + FMP, no LLM)")
    blocks = score_watchlist(provider=build_default_provider(refresh_cache=False))
    ctx.log(f"scored {len(blocks)} tickers")

    with tempfile.TemporaryDirectory() as tmp:
        scores_path = Path(tmp) / "scores.json"
        scores_path.write_text(
            json.dumps([b.model_dump(mode="json") for b in blocks.values()], indent=2),
            encoding="utf-8",
        )
        ctx.log("ingesting scores")

        async def _ingest() -> int:
            engine = make_engine()
            try:
                return await ingest(
                    engine,
                    scores_path,
                    run_id=ctx.run_id,
                    company_names=load_company_names(),
                    manage_run_row=False,
                )
            finally:
                await engine.dispose()

        count = asyncio.run(_ingest())

    ctx.log(f"ingested {count} companies")
    return count


def run_report(ctx: RunContext):
    """ADR 0010 §1's "Generate report": the full chain over the whole
    watchlist."""
    from pipeline.config import load_watchlist_tickers

    tickers = load_watchlist_tickers()
    ctx.log(f"starting the report pipeline over {len(tickers)} tickers")
    summary = _run_pipeline(ctx, tickers=tickers, highlight_tickers=None)
    ctx.log(f"pipeline finished: verdict={summary.verdict} findings={summary.findings_count}")
    return summary


def run_company(ctx: RunContext):
    """ADR 0010 §1's "Research this company": the same chain, one ticker.

    `highlight_tickers` is pinned to that ticker. Left to
    `select_highlight_tickers()`, a one-ticker universe would still go
    through a selection step whose answer is already known, and would spend a
    search call establishing it.
    """
    if not ctx.ticker:
        raise ValueError("the 'company' trigger requires a ticker")
    ctx.log(f"starting the company pipeline for {ctx.ticker}")
    summary = _run_pipeline(ctx, tickers=[ctx.ticker], highlight_tickers=[ctx.ticker])
    ctx.log(f"pipeline finished: verdict={summary.verdict} findings={summary.findings_count}")
    return summary


def _run_pipeline(ctx: RunContext, *, tickers: list[str], highlight_tickers: list[str] | None):
    from pipeline.run import run_pipeline

    return run_pipeline(
        tickers=tickers,
        run_id=ctx.run_id,
        trigger_type=ctx.trigger_type,
        ticker=ctx.ticker,
        highlight_tickers=highlight_tickers,
    )
