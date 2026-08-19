"""Environment/config loading for the scoring engine.

Loads .env locally; in CI (e.g. GitHub Actions) FMP_API_KEY is already in the
environment as a repo secret, so load_dotenv() is a harmless no-op there.

Two data sources since ADR 0001 (reports/decisions/): company financials come
from SEC EDGAR (no key, contact User-Agent required), price/market cap from
FMP's `profile` endpoint (the one endpoint its free tier does not gate).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHLIST_PATH = REPO_ROOT / "data" / "watchlist.yaml"
CACHE_DIR = REPO_ROOT / ".cache"

# FMP endpoint base. Verify against https://site.financialmodelingprep.com/developer/docs
# at implementation/upgrade time -- FMP has moved endpoints between /api/v3 and /stable.
FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# Free tier has historically capped around 250 requests/day. Since ADR 0001 a
# watchlist refresh costs exactly ONE FMP call per ticker (`profile`, for price
# and market cap) -- the statement endpoints moved to EDGAR -- so 42 tickers now
# sit far under this cap even on a single key. Kept as documentation of the
# constraint the cache and multi-key rotation were built for.
FMP_DAILY_CALL_BUDGET = 250

# --- SEC EDGAR (primary fundamentals source, ADR 0001) ---

# XBRL company facts live on data.sec.gov; the ticker->CIK map is served from
# www.sec.gov, which is a different host (hence two constants, not a base + path).
SEC_EDGAR_BASE_URL = "https://data.sec.gov"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC's published fair-access limit. Exceeding it gets the IP blocked, so
# EdgarClient throttles to this rather than relying on politeness.
SEC_EDGAR_MAX_REQUESTS_PER_SECOND = 10

# data.sec.gov returns 403 for any request without a User-Agent identifying the
# requester with a contact address. This default keeps the CLI working out of
# the box; override via SEC_EDGAR_USER_AGENT when running under another owner.
SEC_EDGAR_DEFAULT_USER_AGENT = "TechInves/0.1 (contact@example.com)"

CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h, methodology only needs weekly/quarterly refresh

# --- ECB reference rates (FX translation for non-USD filers, ADR 0007) ---

# ECB Data Portal SDMX REST API. This is the *current* host: ECB retired
# sdw-wsrest.ecb.europa.eu for its 2023 data-portal migration, and some older
# documentation (including early drafts of the backlog item this closes)
# still points at it. `format=csvdata` avoids adding an XML/SDMX-ML parser
# for a single numeric column.
ECB_FX_BASE_URL = "https://data-api.ecb.europa.eu/service/data"

# EXR dataflow, daily reference rate, spot, "average" (ECB publishes one rate
# per TARGET business day around 16:00 CET, not an intraday series). Series
# key shape is D.<CCY>.EUR.SP00.A for any currency ECB tracks against EUR --
# only EUR is exercised today (ASML), see ADR 0007 §5.
ECB_FX_DATAFLOW = "EXR"

# Bounded carry-forward window for a missing closing-rate observation
# (weekend/TARGET holiday landing exactly on a fact's `end` date), ADR 0007
# §3. Exceeding this without a published rate means "refuse to translate",
# not "keep looking".
FX_RATE_LOOKBACK_DAYS = 7

# Metric coverage threshold below which a company's score is labeled "low reliability".
LOW_RELIABILITY_COVERAGE_THRESHOLD = 0.60

# Cohort minimum size (report_scoring_metadology.md v1.1, Section 2).
COHORT_MIN_SIZE = 13


class ConfigError(RuntimeError):
    """Raised when required configuration (e.g. FMP_API_KEY) is missing."""


def get_fmp_api_keys() -> list[str]:
    """One or more FMP keys, comma-separated in FMP_API_KEY. FMPClient
    rotates to the next key when one hits its quota (401/402/403/429),
    so a full watchlist refresh can span more than one free-tier key
    without manual re-running."""
    raw = os.environ.get("FMP_API_KEY")
    if not raw:
        raise ConfigError(
            "FMP_API_KEY is not set. Copy .env.example to .env and fill it in, "
            "or set the FMP_API_KEY environment variable directly."
        )
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ConfigError("FMP_API_KEY is set but empty after parsing.")
    return keys


def get_sec_user_agent() -> str:
    """Contact string sent as User-Agent on every data.sec.gov request.

    SEC's access policy requires it to identify the requester and include a
    working contact address; requests without one get 403. Falls back to the
    project default rather than raising, because unlike an API key this is not
    a secret and a missing value must not break a fresh checkout.
    """
    return os.environ.get("SEC_EDGAR_USER_AGENT", "").strip() or SEC_EDGAR_DEFAULT_USER_AGENT
