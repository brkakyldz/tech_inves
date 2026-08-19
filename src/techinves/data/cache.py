"""File-based JSON cache for provider responses, keyed by (endpoint, ticker,
params). Shared by both data sources (see fmp_client.py and edgar_client.py).

Originally written because FMP's free tier caps around 250 requests/day. Since
ADR 0001 the quota pressure is gone -- a refresh is one EDGAR request and one
FMP request per ticker -- but the cache earns its keep on payload size instead:
EDGAR's companyfacts documents run 0.5-4 MB each, so a same-day re-run of the
CLI would otherwise re-download ~60-100 MB. The methodology only requires
weekly/quarterly refreshes anyway, so a 24h TTL costs nothing in freshness.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from techinves.config import CACHE_DIR, CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)


class FileCache:
    def __init__(self, cache_dir: Path | None = None, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.cache_dir = cache_dir or CACHE_DIR
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, endpoint: str, ticker: str, params: dict[str, Any] | None) -> Path:
        raw_key = f"{endpoint}|{ticker}|{json.dumps(params or {}, sort_keys=True)}"
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
        safe_endpoint = endpoint.strip("/").replace("/", "_")
        return self.cache_dir / f"{safe_endpoint}__{ticker}__{digest}.json"

    def get(self, endpoint: str, ticker: str, params: dict[str, Any] | None = None) -> Any | None:
        path = self._key_path(endpoint, ticker, params)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                envelope = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, KeyError) as exc:
            logger.warning("Cache file %s is corrupted or unreadable (%s); treating as a cache miss.", path, exc)
            return None
        if "cached_at" not in envelope or "data" not in envelope:
            logger.warning("Cache file %s has an unexpected shape; treating as a cache miss.", path)
            return None
        if time.time() - envelope["cached_at"] > self.ttl_seconds:
            return None
        return envelope["data"]

    def set(self, endpoint: str, ticker: str, data: Any, params: dict[str, Any] | None = None) -> None:
        path = self._key_path(endpoint, ticker, params)
        envelope = {"cached_at": time.time(), "data": data}

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(envelope, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
