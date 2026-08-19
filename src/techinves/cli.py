"""CLI entrypoint: python -m techinves score-watchlist / score --ticker."""

from __future__ import annotations

import argparse
import json
import sys

from techinves.config import ConfigError
from techinves.data.raw_facts import HybridFactsProvider, build_default_provider
from techinves.models import Cohort
from techinves.output.formatter import format_debug_report, format_score_block, format_watchlist_report
from techinves.scoring.debug import debug_ticker
from techinves.scoring.engine import score_watchlist
from techinves.watchlist import load_scoring_excluded, load_watchlist, tickers_in_cohort


def _build_provider(refresh_cache: bool) -> HybridFactsProvider:
    return build_default_provider(refresh_cache=refresh_cache)


def _write_output(text: str, output_path: str | None) -> None:
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)


class UnknownTickerError(Exception):
    """Raised when a ticker argument isn't in data/watchlist.yaml at all."""


class ScoringExcludedTickerError(Exception):
    """Raised when a ticker is in data/watchlist.yaml but listed under
    `scoring_excluded` -- it is a legitimate watchlist member (still covered
    by the research pipeline) but the financial scoring engine must never
    score it. See ADR 0005 §5. Kept distinct from UnknownTickerError so the
    two cases don't collapse into the same message: "RKLB" and "ZZZZ" are
    different failures with different fixes for the user.
    """


def _require_known_ticker(ticker: str) -> None:
    # Order matters: an excluded ticker IS in load_watchlist()'s cohort keys
    # under raw YAML, but load_watchlist() itself already drops it -- so the
    # scoring_excluded check must run first, or it would fall through to the
    # generic "unknown ticker" message and misreport RKLB/ASTS/SPCX as not in
    # the watchlist at all, which is false (ADR 0005 §5: they stay in the
    # watchlist, just not in scoring).
    if ticker in load_scoring_excluded():
        raise ScoringExcludedTickerError(
            f"{ticker} is in the watchlist but excluded from financial scoring (ADR 0005 §5)"
        )
    if ticker not in load_watchlist():
        raise UnknownTickerError(f"Unknown ticker '{ticker}' -- not in watchlist")


def _cmd_score(args: argparse.Namespace) -> int:
    # Checked up front: score_watchlist() silently drops tickers that aren't
    # in the watchlist from its result dict (they never enter its `universe`),
    # so `[args.ticker]` below used to raise a bare, unhandled KeyError for a
    # typo'd or delisted ticker instead of a clear user-facing error.
    _require_known_ticker(args.ticker)
    provider = _build_provider(args.refresh_cache)
    block = score_watchlist(tickers=[args.ticker], provider=provider)[args.ticker]
    if args.format == "json":
        _write_output(block.model_dump_json(indent=2), args.output)
    else:
        _write_output(format_score_block(block), args.output)
    return 0


def _cmd_debug_ticker(args: argparse.Namespace) -> int:
    """Fetches + computes raw metrics for ONE ticker only -- no cohort fetch,
    no percentiles, no composite score. Two requests (EDGAR companyfacts +
    FMP profile) instead of the ~80 `score --ticker` needs, since that must
    fetch the whole cohort for percentile ranking. Use this to test the data
    integration itself (XBRL concept mapping, regime detection, missing-source
    handling) one company at a time.
    """
    provider = _build_provider(args.refresh_cache)
    report = debug_ticker(args.ticker, provider=provider)
    if args.format == "json":
        _write_output(report.model_dump_json(indent=2), args.output)
    else:
        _write_output(format_debug_report(report), args.output)
    return 0


def _cmd_score_watchlist(args: argparse.Namespace) -> int:
    provider = _build_provider(args.refresh_cache)
    tickers = None
    if args.cohort:
        watchlist = load_watchlist()
        tickers = tickers_in_cohort(Cohort(args.cohort), watchlist)

    blocks = score_watchlist(tickers=tickers, provider=provider)

    if args.format == "json":
        # A list, not a ticker-keyed dict -- techinves-ingest (and the
        # weekly GitHub Actions job) reads this file straight into
        # TypeAdapter(list[ScoreBlock]); each block already carries its own
        # `ticker` field, so the dict keying was redundant duplication, not
        # an intentional shape difference.
        payload = [block.model_dump(mode="json") for block in blocks.values()]
        _write_output(json.dumps(payload, indent=2), args.output)
    else:
        _write_output(format_watchlist_report(blocks), args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="techinves")
    subparsers = parser.add_subparsers(dest="command", required=True)

    score = subparsers.add_parser("score", help="Score a single ticker (within its full cohort)")
    score.add_argument("--ticker", required=True)
    score.add_argument("--refresh-cache", action="store_true")
    score.add_argument("--format", choices=["text", "json"], default="text")
    score.add_argument("--output", default=None)
    score.set_defaults(func=_cmd_score)

    debug_cmd = subparsers.add_parser(
        "debug-ticker",
        help="Fetch+compute raw metrics for ONE ticker only, no cohort/percentile (cheap, for testing the EDGAR+FMP integration)",
    )
    debug_cmd.add_argument("--ticker", required=True)
    debug_cmd.add_argument("--refresh-cache", action="store_true")
    debug_cmd.add_argument("--format", choices=["text", "json"], default="text")
    debug_cmd.add_argument("--output", default=None)
    debug_cmd.set_defaults(func=_cmd_debug_ticker)

    score_watchlist_cmd = subparsers.add_parser("score-watchlist", help="Score the full watchlist, or one cohort")
    score_watchlist_cmd.add_argument("--cohort", choices=["A", "B", "C"], default=None)
    score_watchlist_cmd.add_argument("--refresh-cache", action="store_true")
    score_watchlist_cmd.add_argument("--format", choices=["text", "json"], default="text")
    score_watchlist_cmd.add_argument("--output", default=None)
    score_watchlist_cmd.set_defaults(func=_cmd_score_watchlist)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except UnknownTickerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ScoringExcludedTickerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        # debug_ticker() (scoring/debug.py) raises a plain ValueError for a
        # ticker not in the watchlist ("<ticker> is not in the watchlist
        # (data/watchlist.yaml)") -- report it the same way as the other
        # user-input errors above instead of letting it crash the CLI with a
        # traceback.
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
