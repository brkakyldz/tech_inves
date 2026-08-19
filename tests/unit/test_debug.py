"""Tests scoring/debug.py: single-ticker inspection that must NOT touch any
other watchlist member and must NOT require a cohort-wide fetch. This is the
cheap path used to exercise the EDGAR+FMP integration without fetching a whole
cohort.
"""

from __future__ import annotations

import pytest

from techinves.data.raw_facts import HybridFactsProvider
from techinves.scoring.debug import debug_ticker
from tests.conftest import FakeEdgarClient, FakeFMPClient, synthetic_company_facts

_PROFILE = [
    {"sector": "Technology", "industry": "Software", "marketCap": 3_000_000_000.0, "price": 150.0}
]


def _provider_for(ticker: str) -> tuple[HybridFactsProvider, FakeFMPClient, FakeEdgarClient]:
    fmp = FakeFMPClient({("profile", ticker, None): _PROFILE})
    edgar = FakeEdgarClient({ticker: synthetic_company_facts()})
    provider = HybridFactsProvider(fmp, edgar, {ticker: "0001234567"})
    return provider, fmp, edgar


def test_debug_ticker_only_calls_for_the_requested_ticker():
    provider, fmp, edgar = _provider_for("NVDA")
    report = debug_ticker("NVDA", provider=provider)

    assert report.ticker == "NVDA"
    assert all(call_ticker == "NVDA" for _, call_ticker, _ in fmp.calls)
    assert all(call_ticker == "NVDA" for _, call_ticker in edgar.calls)
    # The whole point of the hybrid split: one EDGAR request covers all three
    # statements and every year, and FMP is asked only for the one endpoint
    # its free tier does not gate. No cohort peers are touched, unlike
    # score_ticker()/score_watchlist().
    assert len(edgar.calls) == 1
    assert [endpoint for endpoint, _, _ in fmp.calls] == ["profile"]


def test_debug_ticker_returns_raw_metrics_without_percentiles():
    provider, _, _ = _provider_for("NVDA")
    report = debug_ticker("NVDA", provider=provider)

    assert report.metrics  # non-empty
    names = {m.name for m in report.metrics}
    assert "gross_margin" in names or any("margin" in n for n in names)
    # MetricValue has no percentile field at all -- this is a structural
    # guarantee, not just a runtime check, but assert the raw values are
    # present so the report is actually useful for debugging.
    available = [m for m in report.metrics if m.available]
    assert available


def test_debug_ticker_rejects_ticker_not_in_watchlist():
    provider, _, _ = _provider_for("NOTAREALTICKER")
    with pytest.raises(ValueError, match="not in the watchlist"):
        debug_ticker("NOTAREALTICKER", provider=provider)


def test_debug_ticker_rejects_scoring_excluded_ticker_with_distinct_message():
    # RKLB is in data/watchlist.yaml (cohort B) but listed under
    # scoring_excluded (ADR 0005 §5). This must fail with a message distinct
    # from the plain "not in the watchlist" case above -- RKLB genuinely is
    # in the watchlist, it's just not scoreable.
    provider, _, _ = _provider_for("RKLB")
    with pytest.raises(ValueError, match="excluded from financial scoring"):
        debug_ticker("RKLB", provider=provider)
