"""Low-level HTTP client for SEC EDGAR's XBRL API: contact User-Agent, the
10 req/sec fair-access limit, retry/backoff, and caching.

The EDGAR counterpart to fmp_client.py, and like it, the only module in the
EDGAR path that touches the network. Everything above it (cik_map.py,
edgar_facts.py) goes through an EdgarClient instance so tests can inject a
fake -- see tests/conftest.py's FakeEdgarClient.

Two things differ from the FMP client and drive the design here:

* There are no API keys, so there is no rotation and no per-key quota. The
  only budget is requests/second, which is enforced by a sleep in _throttle()
  rather than left to chance -- exceeding SEC's published limit gets the
  source IP blocked outright, which is not a recoverable per-ticker failure.
  That budget is held **process-wide** (`EDGAR_THROTTLE`), not per client
  instance: SEC counts per source IP, and this process can have more than one
  EdgarClient issuing requests at once.
* One `companyfacts` response covers all three statements for a company's
  entire filing history (0.5-4 MB), so a full watchlist refresh is 42
  requests, not ~250. Caching still matters, but for latency and bandwidth
  rather than for staying under a daily cap.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Protocol

import requests

from techinves.config import (
    SEC_EDGAR_BASE_URL,
    SEC_EDGAR_MAX_REQUESTS_PER_SECOND,
    ConfigError,
    get_sec_user_agent,
)
from techinves.data.cache import FileCache

# Cache "endpoint" namespaces. These are not URL paths -- FileCache only uses
# them to build a filename -- but keeping them URL-shaped makes the .cache
# directory readable.
COMPANY_FACTS_ENDPOINT = "edgar_companyfacts"
COMPANY_TICKERS_ENDPOINT = "edgar_company_tickers"


class _ProcessWideThrottle:
    """One requests/second budget for every EDGAR request this process makes.

    The budget SEC enforces is per source IP, not per Python object, so it can
    only be respected by a limiter that is per source IP too -- and in a
    single-process application that means module level. Instance state was not
    enough: `build_default_provider` mints a fresh `EdgarClient` per call, and
    `runs.service`'s in-flight lock is per *trigger type*, so a `scores` run
    and a `company` run may legitimately execute at the same time. Two clients
    each pacing themselves at 10 req/s is 20 req/s out of one IP, and
    README.md is explicit that tripping the fair-access limit blocks the IP
    rather than failing one ticker.

    The lock is held **across the sleep**, which is the point rather than an
    oversight: releasing it first would let every waiting thread compute the
    same deadline, sleep in parallel and then fire together, which spaces
    nothing. Holding it serialises callers into a queue, so N concurrent
    threads issue requests `min_interval` apart exactly as one thread would.
    Each wait is at most `min_interval` (0.1s), so the queueing is not a
    meaningful source of latency.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request_at: float | None = None

    def wait(self, min_interval: float) -> None:
        """Block until `min_interval` has passed since the previous request.

        A monotonic clock, so a system clock adjustment mid-run cannot turn
        the wait into a very long sleep (or skip it entirely).
        """
        if min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                wait = min_interval - (now - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
            self._last_request_at = time.monotonic()

    def reset(self) -> None:
        """Forget the last request. For tests only -- process-wide state that
        leaks between test cases would make them order-dependent."""
        with self._lock:
            self._last_request_at = None


#: The single EDGAR budget. Shared by every `EdgarClient`; see the class
#: docstring for why it cannot live on the instance.
EDGAR_THROTTLE = _ProcessWideThrottle()


class SupportsCompanyFacts(Protocol):
    def get_company_facts(self, cik: str, ticker: str) -> dict[str, Any] | None: ...


class EdgarClient:
    """Real HTTP client for data.sec.gov. Retries transient failures, caches
    all responses, and never exceeds SEC's requests/second limit.
    """

    def __init__(
        self,
        user_agent: str | None = None,
        base_url: str = SEC_EDGAR_BASE_URL,
        cache: FileCache | None = None,
        max_retries: int = 3,
        timeout_seconds: int = 30,
        use_cache: bool = True,
        write_cache: bool | None = None,
        max_requests_per_second: float = SEC_EDGAR_MAX_REQUESTS_PER_SECOND,
    ) -> None:
        self.user_agent = user_agent or get_sec_user_agent()
        self.base_url = base_url.rstrip("/")
        self.cache = cache if cache is not None else FileCache()
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        # `use_cache` gates the *read*; `write_cache` gates the *write*, and
        # defaults to following the read so a plain `use_cache=False` still
        # means "no cache at all". They are separate because
        # `--refresh-cache` needs exactly the combination the single flag
        # could not express: read nothing, write everything. See
        # `data/raw_facts.build_default_provider`.
        self.use_cache = use_cache
        self.write_cache = use_cache if write_cache is None else write_cache
        self._min_interval = 1.0 / max_requests_per_second if max_requests_per_second > 0 else 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": self.user_agent,
                # data.sec.gov serves gzip; requests negotiates this anyway, but
                # being explicit keeps the 4 MB companyfacts payloads compressed
                # even if a proxy strips the default header.
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            }
        )

    def _throttle(self) -> None:
        """Blocks until at least `_min_interval` has passed since the last
        EDGAR request made *anywhere in this process* -- see
        `_ProcessWideThrottle`."""
        EDGAR_THROTTLE.wait(self._min_interval)

    def get_json(self, url: str, endpoint: str, cache_key: str) -> Any:
        """Fetch and parse `url`. Returns None when EDGAR has nothing for this
        entity (404) -- callers record that in RawFinancialFacts.missing_endpoints
        rather than treating it as an error, matching the FMP client's contract.
        """
        if self.use_cache:
            cached = self.cache.get(endpoint, cache_key, None)
            if cached is not None:
                return cached

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self._session.get(url, timeout=self.timeout_seconds)
            except requests.RequestException as exc:  # network error, timeout
                last_exc = exc
                time.sleep(2**attempt)
                continue

            if resp.status_code == 404:
                # No XBRL facts filed under this CIK. Real and permanent for
                # some entities (see ADR 0001's note on SPCX), not transient.
                return None
            if resp.status_code == 403:
                # Almost always a rejected User-Agent -- a config problem, not
                # a per-company data gap. Retrying identical headers cannot
                # fix it, so fail loudly with the cause. Deliberately raised
                # as ConfigError rather than requests.HTTPError: the latter is
                # a RequestException, which get_company_facts's per-ticker
                # `except requests.RequestException` catches and downgrades to
                # a one-line stderr message -- exactly the silent-degrade this
                # is meant to avoid. A misconfigured SEC_EDGAR_USER_AGENT would
                # otherwise quietly zero out fundamentals for all 42 companies
                # instead of surfacing as a startup failure.
                raise ConfigError(
                    f"403 from SEC EDGAR for {url}. SEC requires a User-Agent with a "
                    f"contact address; the current one is {self.user_agent!r}. "
                    "Set SEC_EDGAR_USER_AGENT to 'Name (email@example.com)'."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                # 429 means the fair-access limit was tripped despite the
                # throttle (e.g. another process sharing this IP). Backing off
                # is the documented remedy.
                last_exc = requests.HTTPError(f"{resp.status_code} from SEC EDGAR: {url}")
                time.sleep(2 ** (attempt + 1))
                continue

            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None

            if self.write_cache:
                self.cache.set(endpoint, cache_key, data, None)
            return data

        if last_exc is not None:
            raise last_exc
        return None

    def get_company_facts(self, cik: str, ticker: str) -> dict[str, Any] | None:
        """All XBRL facts a company has ever filed, in one request.

        `cik` must already be zero-padded to 10 digits (cik_map.py does this) --
        data.sec.gov 404s on an unpadded CIK.
        """
        url = f"{self.base_url}/api/xbrl/companyfacts/CIK{cik}.json"
        try:
            return self.get_json(url, COMPANY_FACTS_ENDPOINT, ticker)
        except requests.RequestException as exc:
            print(f"  {ticker}: EDGAR companyfacts unavailable ({exc})", file=sys.stderr, flush=True)
            return None
