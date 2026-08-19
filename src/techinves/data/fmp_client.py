"""Low-level HTTP client for the FMP API: auth, retry/backoff, caching, and
multi-key rotation.

This is the ONLY module that talks to the network. Everything above it
(fmp_endpoints.py, raw_facts.py) goes through an FMPClient instance so tests
can inject a fake one -- see tests/conftest.py's FakeFMPClient.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Protocol

import requests

from techinves.config import FMP_BASE_URL
from techinves.data.cache import FileCache

# 401/429: this key specifically is rate-limited or rejected -- another key
# may still work, worth rotating.
_ROTATE_STATUS_CODES = {401, 429}
# 402/403: FMP's "Premium Query Parameter / Special Endpoint" response --
# this symbol isn't covered by the plan tier, not a per-key quota issue.
# Same-tier sibling keys reject it identically, so rotating just burns
# requests; treat it as unavailable straight away.
_PLAN_GATED_STATUS_CODES = {402, 403}


class SupportsGet(Protocol):
    def get(
        self, endpoint: str, ticker: str, params: dict[str, Any] | None = None
    ) -> Any: ...


class FMPClient:
    """Real HTTP client. Retries transient failures, caches all responses."""

    def __init__(
        self,
        api_key: str | list[str],
        base_url: str = FMP_BASE_URL,
        cache: FileCache | None = None,
        max_retries: int = 3,
        timeout_seconds: int = 20,
        use_cache: bool = True,
        write_cache: bool | None = None,
    ) -> None:
        self.api_keys = [api_key] if isinstance(api_key, str) else list(api_key)
        if not self.api_keys:
            raise ValueError("FMPClient needs at least one api_key")
        self._key_index = 0
        # Indices into self.api_keys that have already returned a
        # quota-exceeded response this run. FMP's daily cap never clears
        # mid-run (see the 429 handling below), so once a key is known
        # exhausted there's no reset condition to wait for within a single
        # process -- it's skipped for the rest of the run without spending a
        # request to re-confirm it. A process that outlives a UTC-day
        # boundary would need timestamp-based expiry here; out of scope for
        # the runs this client is used in (one on-demand scoring pass).
        self._exhausted_keys: set[int] = set()
        self.base_url = base_url.rstrip("/")
        self.cache = cache if cache is not None else FileCache()
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        # Read gate and write gate, separately: `--refresh-cache` reads
        # nothing and writes everything (see EdgarClient.__init__).
        self.use_cache = use_cache
        self.write_cache = use_cache if write_cache is None else write_cache
        self._session = requests.Session()

    def get(self, endpoint: str, ticker: str, params: dict[str, Any] | None = None) -> Any:
        """Fetch `endpoint` for `ticker`. Returns parsed JSON, or None if the
        endpoint 404s/is empty, or every configured key is quota-exhausted
        (treated as "unavailable", not an exception -- callers record this in
        RawFinancialFacts.missing_endpoints).
        """
        if self.use_cache:
            cached = self.cache.get(endpoint, ticker, params)
            if cached is not None:
                return cached

        # FMP's /stable API takes the ticker as a `symbol` query param, not a
        # path segment (verified against live responses -- differs from the
        # older /api/v3 path-based convention this was originally written for).
        url = f"{self.base_url}/{endpoint.strip('/')}"
        base_query = dict(params or {})
        base_query["symbol"] = ticker

        last_exc: Exception | None = None
        keys_tried = 0
        while keys_tried < len(self.api_keys):
            if self._key_index in self._exhausted_keys:
                # Already known quota-exhausted from an earlier call this
                # run -- skip it without spending a request to re-confirm.
                keys_tried += 1
                self._key_index = (self._key_index + 1) % len(self.api_keys)
                continue
            query = dict(base_query)
            query["apikey"] = self.api_keys[self._key_index]
            quota_exhausted = False

            for attempt in range(self.max_retries):
                try:
                    resp = self._session.get(url, params=query, timeout=self.timeout_seconds)
                except requests.RequestException as exc:  # network error, timeout
                    last_exc = exc
                    time.sleep(2**attempt)
                    continue

                is_last_key = keys_tried == len(self.api_keys) - 1
                if resp.status_code == 429 and is_last_key and attempt < self.max_retries - 1:
                    # No sibling key left to fall back to -- give this one a
                    # backoff in case it's a short burst limit rather than
                    # the day's quota. With spare keys, skip straight to
                    # rotating instead: retrying a 429 in place before moving
                    # on multiplies real requests per exhausted key by
                    # max_retries for no benefit (FMP's daily-cap 429 never
                    # clears mid-run).
                    time.sleep(2 ** (attempt + 1))
                    continue
                if resp.status_code in _PLAN_GATED_STATUS_CODES:
                    print(
                        f"  {ticker}/{endpoint}: not covered by this plan (HTTP {resp.status_code}), skipping",
                        file=sys.stderr,
                        flush=True,
                    )
                    return None
                if resp.status_code in _ROTATE_STATUS_CODES:
                    # Rate-limited (after backoff) or auth-rejected: this key
                    # is done for now, but a sibling key may still work.
                    quota_exhausted = True
                    break
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 500:
                    last_exc = requests.HTTPError(f"{resp.status_code} from FMP: {url}")
                    time.sleep(2**attempt)
                    continue

                resp.raise_for_status()
                data = resp.json()
                if not data:
                    return None

                if self.write_cache:
                    self.cache.set(endpoint, ticker, data, params)
                return data

            keys_tried += 1
            if quota_exhausted:
                self._exhausted_keys.add(self._key_index)
                if keys_tried < len(self.api_keys):
                    exhausted_key = self._key_index + 1
                    self._key_index = (self._key_index + 1) % len(self.api_keys)
                    print(
                        f"  key #{exhausted_key} exhausted, rotating to key #{self._key_index + 1}",
                        file=sys.stderr,
                        flush=True,
                    )
            else:
                # Retries exhausted on a network/5xx error, not a quota
                # issue -- rotating keys won't help, stop trying.
                break

        if last_exc is not None:
            raise last_exc
        return None
