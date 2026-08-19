"""Assembles RawFinancialFacts for one ticker from the two sources ADR 0001
settled on: company financials from SEC EDGAR, price and market cap from FMP.

This is the boundary between "where the numbers come from" and the rest of the
scoring engine, which only ever sees RawFinancialFacts. The split exists
because neither source is sufficient alone:

* EDGAR has every statement a company ever filed, for free and without a key,
  but it is a filings archive -- it carries no market data at all. Four of the
  five valuation metrics need enterprise value, which needs market cap.
* FMP's free plan gates the statement endpoints per symbol (30 of 42 watchlist
  tickers returned HTTP 402), but its `profile` endpoint is not gated and
  answers for all of them.

Enterprise value is therefore no longer fetched from anywhere -- it is derived
here as `market_cap + total_debt - cash` (ADR 0001 clause 1).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Protocol

from techinves.config import get_fmp_api_keys
from techinves.data import fmp_endpoints as ep
from techinves.data.cache import FileCache
from techinves.data.cik_map import load_cik_map, resolve_cik
from techinves.data.edgar_client import EdgarClient, SupportsCompanyFacts
from techinves.data.edgar_facts import build_periods
from techinves.data.fmp_client import FMPClient, SupportsGet
from techinves.data.fx_rates import ECBFxClient, SupportsFxYearSeries
from techinves.models import PeriodFacts, RawFinancialFacts


class FactsProvider(Protocol):
    def fetch(self, ticker: str) -> RawFinancialFacts: ...


def _latest_balance_period(
    annual: list[PeriodFacts], quarterly: list[PeriodFacts]
) -> PeriodFacts | None:
    """The most recent period that carries both debt and cash.

    Quarterly is considered alongside annual, and deliberately: enterprise
    value is a present-tense measure, so it should use the newest balance sheet
    on file rather than one that can be up to four quarters stale. Balance-sheet
    items are instants, so there is no annual-vs-quarterly comparability
    problem here -- unlike the flow items, a quarter-end balance is the same
    kind of number as a year-end one.
    """
    candidates = [
        p
        for p in (*annual, *quarterly)
        if p.total_debt is not None and p.cash_and_equivalents is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.fiscal_date)


def _derive_enterprise_value(
    market_cap: float | None, annual: list[PeriodFacts], quarterly: list[PeriodFacts]
) -> float | None:
    """EV = market cap + total debt - cash (ADR 0001 clause 1).

    Returns None rather than falling back to market cap alone when debt or
    cash could not be resolved. A missing field means "not reported in a form
    this mapping recognizes", not zero -- and quietly treating unresolved debt
    as zero would inflate every ratio EV sits in the numerator of, for exactly
    the companies whose data is weakest.
    """
    if market_cap is None:
        return None
    period = _latest_balance_period(annual, quarterly)
    if period is None:
        return None
    return market_cap + period.total_debt - period.cash_and_equivalents


class HybridFactsProvider:
    """Fetches one ticker's facts from EDGAR + FMP.

    Holds the ticker->CIK map for the whole run: it is a single ~1 MB document
    covering every registrant, so resolving it once per process rather than
    once per ticker is the difference between one request and 42.
    """

    def __init__(
        self,
        fmp_client: SupportsGet,
        edgar_client: SupportsCompanyFacts,
        cik_map: dict[str, str],
        fx_client: SupportsFxYearSeries | None = None,
    ) -> None:
        self.fmp_client = fmp_client
        self.edgar_client = edgar_client
        self.cik_map = cik_map
        # ADR 0007: translates EUR-only filers (ASML) to USD at the
        # edgar_facts boundary. None keeps the pre-ADR-0007 behaviour --
        # non-USD facts are dropped, not translated -- which is what every
        # test that constructs this provider without an fx_client still gets.
        self.fx_client = fx_client

    def fetch(self, ticker: str) -> RawFinancialFacts:
        missing: list[str] = []

        # -- market data (FMP) --
        profile = ep.get_profile(self.fmp_client, ticker)
        if profile is None:
            missing.append("fmp profile: empty/unavailable")
        profile = profile or {}
        # /stable's profile calls this `marketCap`; `mktCap` was the /api/v3
        # name and silently yields None against the current API.
        market_cap = profile.get("marketCap")
        price = profile.get("price")

        # -- financial statements (SEC EDGAR) --
        annual: list[PeriodFacts] = []
        quarterly: list[PeriodFacts] = []
        cik = resolve_cik(ticker, self.cik_map)
        if cik is None:
            missing.append("edgar cik: ticker not found in SEC company_tickers.json")
        else:
            company_facts = self.edgar_client.get_company_facts(cik, ticker)
            if not company_facts:
                missing.append(f"edgar companyfacts (CIK {cik}): empty/unavailable")
            else:
                annual, quarterly, taxonomy = build_periods(
                    company_facts, fx_client=self.fx_client
                )
                if taxonomy is None:
                    missing.append("edgar companyfacts: no us-gaap or ifrs-full facts filed")
                if not annual:
                    missing.append(f"edgar annual periods ({taxonomy}): none resolved")
                if not quarterly:
                    missing.append(f"edgar quarterly periods ({taxonomy}): none resolved")

        enterprise_value = _derive_enterprise_value(market_cap, annual, quarterly)
        if enterprise_value is None:
            missing.append("enterprise value: not derivable (market cap, debt or cash missing)")

        return RawFinancialFacts(
            ticker=ticker,
            sector=profile.get("sector"),
            industry=profile.get("industry"),
            market_cap=market_cap,
            price=price,
            enterprise_value=enterprise_value,
            annual=annual,
            quarterly=quarterly,
            # ADR 0001 clause 4: no free source provides analyst consensus, and
            # the metrics that consumed it (forward P/E, forward revenue growth)
            # were dropped from the methodology rather than left permanently
            # unavailable. Kept on the model so the field survives if a paid
            # estimates source is ever added.
            analyst_estimates=[],
            fetched_at=datetime.now(timezone.utc),
            missing_endpoints=missing,
        )


def build_default_provider(refresh_cache: bool = False) -> HybridFactsProvider:
    """Wires the real clients together and resolves the CIK map up front.

    Raises ConfigError if FMP_API_KEY is unset -- price and market cap have no
    free substitute, and without them the whole valuation category is dead, so
    failing here beats producing 42 half-scored companies.
    """
    cache = FileCache()
    # `--refresh-cache` means "ignore what is cached", not "do not cache".
    # `use_cache=False` alone used to mean both, so a refresh run scored fresh
    # data and then left the stale pre-refresh payloads sitting in the cache
    # under their original timestamps -- and the next ordinary run silently
    # reverted to them. Refreshing after earnings (README's stated reason for
    # the flag) therefore lasted exactly one run. The write is unconditional:
    # a response that was just fetched is the freshest thing there is, and
    # there is no case where the cached copy should outlive it.
    fmp_client = FMPClient(
        api_key=get_fmp_api_keys(), cache=cache, use_cache=not refresh_cache, write_cache=True
    )
    edgar_client = EdgarClient(cache=cache, use_cache=not refresh_cache, write_cache=True)
    fx_client = ECBFxClient(cache=cache, use_cache=not refresh_cache, write_cache=True)
    cik_map = load_cik_map(edgar_client)
    if not cik_map:
        print(
            "  warning: SEC ticker->CIK map came back empty; no financials will resolve",
            file=sys.stderr,
            flush=True,
        )
    return HybridFactsProvider(fmp_client, edgar_client, cik_map, fx_client=fx_client)


def fetch_raw_facts(provider: FactsProvider, ticker: str) -> RawFinancialFacts:
    """Kept as a free function so call sites read the same as before the
    provider split; the provider carries the per-run state (clients, CIK map)
    that a bare function had nowhere to put.
    """
    return provider.fetch(ticker)
