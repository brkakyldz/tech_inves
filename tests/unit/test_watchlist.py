"""Tests the scoring_excluded/scoring-eligible split in techinves.watchlist.

ADR 0005 §5: RKLB, ASTS, SPCX stay in the watchlist's research universe
(pipeline.config.load_watchlist_tickers(), out of this module's scope) but
must never be exposed as scoreable by load_watchlist() -- even though they
also appear under cohort B in data/watchlist.yaml.
"""

from __future__ import annotations

from techinves.models import Cohort
from techinves.watchlist import load_scoring_excluded, load_watchlist, tickers_in_cohort

_EXCLUDED = {"RKLB", "ASTS", "SPCX"}


def test_load_watchlist_excludes_scoring_excluded_tickers():
    watchlist = load_watchlist()
    assert _EXCLUDED.isdisjoint(watchlist)


def test_load_watchlist_has_exactly_40_scoreable_tickers():
    watchlist = load_watchlist()
    assert len(watchlist) == 40


def test_load_watchlist_never_yields_scoring_excluded_as_a_cohort():
    # A regression guard for the Phase 0 bug: `scoring_excluded` is a flat
    # list, not a fourth Cohort, and must never reach Cohort(cohort_code).
    watchlist = load_watchlist()
    assert set(watchlist.values()) <= set(Cohort)


def test_cohort_b_membership_excludes_scoring_excluded_tickers():
    watchlist = load_watchlist()
    cohort_b = tickers_in_cohort(Cohort.HARDWARE_SEMI_SPACE, watchlist)
    assert _EXCLUDED.isdisjoint(cohort_b)
    # AAPL, NVDA etc. are still there -- this isn't an empty-cohort bug.
    assert "AAPL" in cohort_b
    assert "TSM" in cohort_b


def test_load_scoring_excluded_returns_the_three_excluded_tickers():
    assert load_scoring_excluded() == frozenset(_EXCLUDED)
