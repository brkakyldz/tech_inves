"""ECB reference-rate fetch, cache, and the two lookup conventions ADR 0007
(`reports/decisions/0007-fx-translation-for-non-usd-filers.md`) defines:
average rate over a period for duration facts (income statement, cash flow),
closing rate at an instant -- with bounded carry-forward -- for instant facts
(balance sheet).

Follows the same shape as edgar_client.py / fmp_client.py: a thin HTTP class
(`ECBFxClient`) backed by the shared `FileCache`, with pure lookup functions
above it that only ever go through the `SupportsFxYearSeries` protocol -- so
tests can inject an in-memory fake exactly like `FakeEdgarClient` does for
`edgar_facts.py`, without touching the network.
"""

from __future__ import annotations

import csv
import io
import sys
import time
from datetime import date, timedelta
from typing import Protocol

import requests

from techinves.config import ECB_FX_BASE_URL, ECB_FX_DATAFLOW, FX_RATE_LOOKBACK_DAYS
from techinves.data.cache import FileCache

# Cache "endpoint" namespace, same convention as edgar_client.py's
# COMPANY_FACTS_ENDPOINT -- not a URL path, just a filename prefix.
FX_RATES_ENDPOINT = "ecb_fx_rates"


class SupportsFxYearSeries(Protocol):
    def get_year_series(self, currency: str, year: int) -> dict[date, float]: ...


class ECBFxClient:
    """Real HTTP client for the ECB Data Portal's EXR dataflow.

    One request per (currency, calendar year), cached: the yearly grain is a
    deliberate cache-locality choice, not an API constraint. A company's
    reporting periods rarely align to calendar years, but ASML alone needs on
    the order of 8 overlapping annual and quarterly windows -- fetching by
    year means every one of those windows after the first is served from
    cache instead of re-downloading overlapping daily data.
    """

    def __init__(
        self,
        base_url: str = ECB_FX_BASE_URL,
        dataflow: str = ECB_FX_DATAFLOW,
        cache: FileCache | None = None,
        max_retries: int = 3,
        timeout_seconds: int = 20,
        use_cache: bool = True,
        write_cache: bool | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.dataflow = dataflow
        self.cache = cache if cache is not None else FileCache()
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        # Read gate and write gate, separately: `--refresh-cache` reads
        # nothing and writes everything (see EdgarClient.__init__).
        self.use_cache = use_cache
        self.write_cache = use_cache if write_cache is None else write_cache
        self._session = requests.Session()
        self._session.headers.update({"Accept": "text/csv"})

    def get_year_series(self, currency: str, year: int) -> dict[date, float]:
        """{date: USD-per-`currency` rate} for every day the ECB published an
        observation in `year`. Weekends/TARGET holidays are simply absent
        keys -- ADR 0007 §3's carry-forward/refusal rule is applied by the
        lookup functions below, not here; this method only ever reports what
        was actually published.
        """
        cache_key = f"{currency}_{year}"
        if self.use_cache:
            cached = self.cache.get(FX_RATES_ENDPOINT, cache_key, None)
            if cached is not None:
                return {date.fromisoformat(d): v for d, v in cached.items()}

        # Series key shape: D.<CCY>.EUR.SP00.A (daily, currency vs EUR, spot,
        # ECB reference rate). ECB's EXR dataflow always quotes against EUR --
        # for EUR itself there is no "D.EUR.EUR..." series, so USD-per-EUR is
        # requested as D.USD.EUR.SP00.A. See ADR 0007 §5: this client is keyed
        # by currency so a future non-EUR filer only needs a new series key,
        # not a new module -- but only EUR is exercised today.
        series_key = f"D.USD.{currency}.SP00.A"
        start = date(year, 1, 1).isoformat()
        end = date(year, 12, 31).isoformat()
        url = (
            f"{self.base_url}/{self.dataflow}/{series_key}"
            f"?startPeriod={start}&endPeriod={end}&format=csvdata"
        )

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._session.get(url, timeout=self.timeout_seconds)
            except requests.RequestException as exc:  # network error, timeout
                last_exc = exc
                time.sleep(2**attempt)
                continue

            if resp.status_code == 404:
                # No observations at all for this currency/year (e.g. a year
                # before the series' 1999-01-04 inception). Real and
                # permanent, not transient -- matches EdgarClient's 404
                # contract.
                return {}
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = requests.HTTPError(f"{resp.status_code} from ECB: {url}")
                time.sleep(2 ** (attempt + 1))
                continue

            resp.raise_for_status()
            series = _parse_csv(resp.text)
            if self.write_cache:
                self.cache.set(
                    FX_RATES_ENDPOINT,
                    cache_key,
                    {d.isoformat(): v for d, v in series.items()},
                    None,
                )
            return series

        if last_exc is not None:
            print(f"  ECB FX {currency}/{year}: unavailable ({last_exc})", file=sys.stderr, flush=True)
        return {}


def _parse_csv(text: str) -> dict[date, float]:
    """Parses ECB's `csvdata` SDMX response into {date: rate}.

    Column names are matched case-insensitively rather than by fixed
    position: ECB's csvdata layout has changed column order/casing across API
    versions, and TIME_PERIOD/OBS_VALUE are the only two columns this needs.
    """
    series: dict[date, float] = {}
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return series
    field_map = {name.strip().upper(): name for name in reader.fieldnames}
    time_col = field_map.get("TIME_PERIOD")
    value_col = field_map.get("OBS_VALUE")
    if time_col is None or value_col is None:
        return series

    for row in reader:
        raw_date = row.get(time_col)
        raw_value = row.get(value_col)
        if not raw_date or not raw_value:
            continue
        try:
            day = date.fromisoformat(raw_date[:10])
            value = float(raw_value)
        except ValueError:
            continue
        series[day] = value
    return series


def _years_spanned(start: date, end: date) -> list[int]:
    return list(range(start.year, end.year + 1))


def _fetch_series(
    client: SupportsFxYearSeries, currency: str, start: date, end: date
) -> dict[date, float]:
    series: dict[date, float] = {}
    for year in _years_spanned(start, end):
        series.update(client.get_year_series(currency, year))
    return series


def closing_rate(
    client: SupportsFxYearSeries,
    currency: str,
    when: date,
    *,
    lookback_days: int = FX_RATE_LOOKBACK_DAYS,
) -> tuple[float, date] | None:
    """USD-per-`currency` rate at `when`, per ADR 0007 §2's instant-fact
    convention.

    If `when` itself has no published observation (weekend/TARGET holiday),
    carries forward the most recent published rate within `lookback_days`
    (ADR 0007 §3) -- the standard "last quotation before the date" market
    convention. Returns `(rate, date_actually_used)`, never just `rate`,
    because the date used can differ from `when` and that difference is
    exactly what the ADR requires to stay traceable (§4).

    Returns `None` -- never a fabricated or defaulted rate -- if nothing was
    published in the entire lookback window (before the series starts, or an
    unusually long gap). Callers must treat `None` as "this fact cannot be
    translated", not as zero or as the raw source-currency value.
    """
    window_start = when - timedelta(days=lookback_days)
    series = _fetch_series(client, currency, window_start, when)
    candidate = when
    while candidate >= window_start:
        rate = series.get(candidate)
        if rate is not None:
            return rate, candidate
        candidate -= timedelta(days=1)
    return None


def average_rate(
    client: SupportsFxYearSeries,
    currency: str,
    start: date,
    end: date,
) -> float | None:
    """Mean of every published USD-per-`currency` observation in the closed
    interval [start, end], per ADR 0007 §2's duration-fact convention.

    A handful of weekend/holiday gaps inside a normal annual or quarterly
    window barely move the mean, so no carry-forward is applied here (unlike
    `closing_rate`) -- only observations that actually fall inside the window
    are used. Returns `None` -- not a fabricated rate -- if the window
    contains zero observations at all (only possible for a span predating
    the series or pathologically short).
    """
    if start > end:
        start, end = end, start
    series = _fetch_series(client, currency, start, end)
    values = [rate for day, rate in series.items() if start <= day <= end]
    if not values:
        return None
    return sum(values) / len(values)
