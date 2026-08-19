"""Loads data/watchlist.yaml into a ticker -> Cohort mapping.

`data/watchlist.yaml` has one non-cohort sibling key, `scoring_excluded`: a
flat list of tickers that stay in their cohort for the *research* universe
(`pipeline.config.load_watchlist_tickers()`) but must never be exposed as
scoreable by this module -- see ADR 0005 §5. `_NON_COHORT_KEYS` is where any
future non-cohort top-level key must be added so it doesn't get fed into
`Cohort(...)` and blow up like `scoring_excluded` did in Phase 0.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from techinves.config import WATCHLIST_PATH
from techinves.models import Cohort

_NON_COHORT_KEYS = frozenset({"scoring_excluded"})


def _load_raw(path: Path | None) -> dict:
    target = path or WATCHLIST_PATH
    with open(target, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_watchlist(path: Path | None = None) -> dict[str, Cohort]:
    """Returns {ticker: Cohort} for every company scoreable by the financial
    scoring engine. Tickers listed under `scoring_excluded` are dropped from
    the result even though they also appear under a cohort key (ADR 0005 §5)
    -- callers of this function (score_watchlist(), the CLI) must never see
    them as scoreable. Use `load_scoring_excluded()` to distinguish "excluded
    from scoring" from "not in the watchlist at all".

    Cached like the original: only the first call's `path` argument takes
    effect for the lifetime of the cache. Tests that pass a custom `path`
    should clear the cache first (`load_watchlist.cache_clear()`) so they are
    not poisoned by a cached result from a different path.
    """
    raw = _load_raw(path)
    excluded = frozenset(raw.get("scoring_excluded", []))

    watchlist: dict[str, Cohort] = {}
    for cohort_code, tickers in raw.items():
        if cohort_code in _NON_COHORT_KEYS:
            continue
        cohort = Cohort(cohort_code)
        for ticker in tickers:
            if ticker in excluded:
                continue
            watchlist[ticker] = cohort
    return watchlist


@lru_cache(maxsize=None)
def load_scoring_excluded(path: Path | None = None) -> frozenset[str]:
    """Returns the set of tickers present in the watchlist but excluded from
    financial scoring (ADR 0005 §5) -- e.g. RKLB, ASTS, SPCX. These tickers
    remain in `pipeline.config.load_watchlist_tickers()`'s research universe;
    they are simply absent from `load_watchlist()`'s result.

    Cached per distinct `path` (unlike `load_watchlist()`'s single-slot
    cache), since `maxsize=None` keys on the argument itself -- a call with a
    custom `path` in tests never collides with the default-path result.
    """
    raw = _load_raw(path)
    return frozenset(raw.get("scoring_excluded", []))


def tickers_in_cohort(cohort: Cohort, watchlist: dict[str, Cohort] | None = None) -> list[str]:
    wl = watchlist if watchlist is not None else load_watchlist()
    return [ticker for ticker, c in wl.items() if c == cohort]
