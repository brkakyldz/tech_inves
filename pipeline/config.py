"""Environment/config loading for the research pipeline.

Mirrors src/techinves/config.py's load_dotenv() pattern. The old claim that
this package "never imports from src/techinves" is retired: it did not survive
contact with the DB layer, and stating it here was actively harmful, because
several values below were duplicated *on the strength of it* and then drifted.
`pipeline` depends on `techinves` today -- `pipeline/storage/report_store.py`,
`pipeline/data/scores_repository.py` and `pipeline/run.py` all import it -- so
a value that must agree with `techinves.config` is read from there rather than
re-derived here (see `get_sec_edgar_user_agent`). The dependency runs one way
only: `techinves` does not import `pipeline` at module scope.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[1]

# Read-only: the watchlist data file, not the scoring/data code in
# src/techinves (out of scope, see pipeline/__init__.py). Parsed directly
# with PyYAML so this package has no import dependency on src/techinves.
WATCHLIST_PATH = REPO_ROOT / "data" / "watchlist.yaml"

# Research agent concurrency (ARCHITECTURE_PROPOSAL.md §1.4 / §2.2b): shared by
# company and macro branches, 4-8 range. Enforced via the `max_concurrency`
# key in the RunnableConfig passed to graph.invoke()/.stream() -- see
# pipeline/graph.py's default_run_config(). Matters most now that the whole
# watchlist (not just a handful of highlighted tickers) fans out at once.
RESEARCH_CONCURRENCY = int(os.environ.get("PIPELINE_RESEARCH_CONCURRENCY", "6"))

# Retry behaviour for a single research branch (research/agent.py). A branch
# never raises out of run_research_branch -- after exhausting retries it
# becomes a FailureNote instead, so LangGraph's node-level RetryPolicy isn't
# used here (it re-raises after exhausting attempts, which would kill the
# whole fan-out instead of isolating the one failing branch).
RESEARCH_MAX_RETRIES = int(os.environ.get("PIPELINE_RESEARCH_MAX_RETRIES", "2"))
RESEARCH_RETRY_BACKOFF_SECONDS = float(
    os.environ.get("PIPELINE_RESEARCH_RETRY_BACKOFF_SECONDS", "1.0")
)

# R7 / 2026-08-19: the rolling search window is a *count of days ending on
# `as_of`, inclusive of both endpoints* -- 7 means [as_of-6, as_of]. It was
# hardcoded as `timedelta(days=6)` inside
# `pipeline.research.agent._search_window`, which made the single most
# yield-relevant retrieval parameter in the pipeline invisible to anyone
# reading config and unchangeable without an edit. Widening it is the first
# lever to reach for when a run comes back thin; it is a knob, not a
# decision, so it lives here.
#
# Used by both the research branches and the highlight pre-selection probe
# (`pipeline/research/highlight_selection.py`), which share `_search_window`
# on purpose: a probe that ranks tickers over a different window than the
# branch that follows it is measuring a different week.
RESEARCH_LOOKBACK_DAYS = int(os.environ.get("PIPELINE_RESEARCH_LOOKBACK_DAYS", "7"))

# Highlight pre-selection probe result cap (ADR 0006 §3, see
# `pipeline/research/highlight_selection.py`).
#
# 2026-08-19: raised 10 -> 20 after run 20260819T112959-a883d9 selected
# MSFT/ADBE/CRM/NOW -- exactly positions 1-4 of `data/watchlist.yaml`, i.e.
# the output of a *total tie* resolved by file order. A ranking signal that
# is `len(results)` cannot discriminate above its own cap, and over a 7-day
# window a large-cap watchlist saturates a 10-result cap almost everywhere.
# Tavily bills per request, not per result, so a larger cap buys dynamic
# range at zero additional cost.
HIGHLIGHT_PROBE_MAX_RESULTS = int(os.environ.get("PIPELINE_HIGHLIGHT_PROBE_MAX_RESULTS", "20"))


# ADR 0005 §5 / R1: `scoring_excluded` is a flat list of tickers, not a
# cohort -- it flags RKLB/ASTS/SPCX as excluded from *financial scoring*
# (src/techinves.watchlist.load_watchlist()) while they still sit under
# their normal cohort (B) for the *research* universe below. Skip it
# explicitly when flattening cohorts; sweeping it in as a fourth cohort
# would double-list those three tickers (they already appear under B).
_NON_COHORT_KEYS = {"scoring_excluded"}


def load_watchlist_tickers(path: Path | None = None) -> list[str]:
    """Flat list of every ticker across all cohorts in data/watchlist.yaml.

    This is the *research* universe (news/qualitative fan-out): it
    deliberately still includes RKLB/ASTS/SPCX (ADR 0005 §5) even though
    they're excluded from the separate financial-scoring cohort
    (`src/techinves/watchlist.py`'s `load_watchlist()`), so callers must not
    assume "in this list" implies "has a score row".
    """
    data = yaml.safe_load((path or WATCHLIST_PATH).read_text(encoding="utf-8"))
    tickers: list[str] = []
    for cohort_key, cohort_tickers in data.items():
        if cohort_key in _NON_COHORT_KEYS:
            continue
        tickers.extend(cohort_tickers)
    return tickers


def load_scoring_eligible_tickers(path: Path | None = None) -> list[str]:
    """Flat list of every ticker across all cohorts in data/watchlist.yaml,
    *excluding* `scoring_excluded` (RKLB/ASTS/SPCX, ADR 0005 §5) -- the
    "every scoring-eligible watchlist ticker" universe REPORT_SPEC.md §1/D1
    defines the Full Watchlist table (§5.1) and its verifier completeness
    predicate (§10 item 1) against (40 of 43 as of D5).

    Deliberately duplicated from (rather than delegating to)
    `src/techinves/watchlist.py`'s `load_watchlist()`, which returns the same
    ticker set keyed by cohort: this `pipeline` package must not import from
    `src/techinves` (see `pipeline/__init__.py`), and `pipeline/verifier/rules.py`
    needs a plain ticker list, not a `{ticker: Cohort}` mapping. Keep the
    exclusion logic here in sync with `src/techinves/watchlist.py` if
    `scoring_excluded` semantics ever change.
    """
    data = yaml.safe_load((path or WATCHLIST_PATH).read_text(encoding="utf-8"))
    excluded = set(data.get("scoring_excluded", []))
    tickers: list[str] = []
    for cohort_key, cohort_tickers in data.items():
        if cohort_key in _NON_COHORT_KEYS:
            continue
        tickers.extend(t for t in cohort_tickers if t not in excluded)
    return tickers


# R17: data/WATCHLIST.md's `| Ticker | Company |` table -- the
# canonical ticker->name mapping (data/watchlist.yaml holds tickers only).
# This loader is now the only reader of that table: the CI workflow that
# duplicated the same parse in embedded YAML+bash was deleted with the
# scheduled product (ADR 0010).
WATCHLIST_MD_PATH = REPO_ROOT / "data" / "WATCHLIST.md"
_TICKER_NAME_RE = re.compile(r"^\|\s*([A-Z][A-Z.\-]{0,6})\s*\|\s*([^|]+?)\s*\|\s*$", re.MULTILINE)


def load_watchlist_company_names(path: Path | None = None) -> dict[str, str]:
    """ticker -> company name. Used (R17) to widen a company branch's search
    queries beyond the bare ticker symbol, which retrieves stock-price
    commentary rather than company events. Returns {} if the file is
    missing rather than raising -- the query-widening it feeds is additive,
    not required for a branch to function."""
    md_path = path or WATCHLIST_MD_PATH
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding="utf-8")
    return {
        ticker: name.strip()
        for ticker, name in _TICKER_NAME_RE.findall(text)
        if ticker != "Ticker"
    }

# Default LLM model, used when the writer/judge overrides below aren't set
# -- ARCHITECTURE_PROPOSAL.md §1.10 / §3. Overridable via env.
LLM_MODEL = os.environ.get("PIPELINE_LLM_MODEL", "gpt-5.6-luna")

# R10: research extraction + synthesis (the writer) vs. the verifier's LLM
# consistency layer (the judge) are separate models. Grading a model's own
# writing with itself is a self-preference bias that made the LLM verifier
# layer near-worthless -- a cheaper/different model as judge doesn't share
# the writer's blind spots. Both default to LLM_MODEL so a single-model
# setup (e.g. tests, or an operator who hasn't opted in yet) is unaffected.
WRITER_MODEL = os.environ.get("PIPELINE_WRITER_MODEL", LLM_MODEL)
JUDGE_MODEL = os.environ.get("PIPELINE_JUDGE_MODEL", LLM_MODEL)

# Fixed sector/macro research topics (ARCHITECTURE_PROPOSAL.md §2.2b) -- hand
# maintained by the operator, not discovered by the LLM. Expanded 4 -> 10
# (ADR 0006 §1 / R7): the report's primary purpose is a macro/sector health
# read, with company deep-dives now a small, deliberately narrow secondary
# feature (§3 below) -- the qualitative research budget shifts accordingly.
# Phrasing matches the original four's search-query shape (not bare nouns):
# these strings feed Tavily directly (pipeline/research/agent.py's
# `_build_query`/`_build_queries`), so they're written for retrieval quality.
MACRO_TOPICS: list[str] = [
    "Fed / interest rate policy - technology sector impact",
    "US-China trade / export policy - semiconductor and hardware impact",
    "AI capex cycle / hyperscaler capital expenditure",
    "Regulation: antitrust / data privacy / AI legislation - big tech impact",
    "Semiconductor supply chain / foundry capacity",
    "Cloud and enterprise IT spend trends",
    "Tech labor market - layoffs and hiring trends",
    "Tech IPO / M&A / private funding activity",
    "Rates and valuation multiple pressure on technology stocks",
    "Data center energy and power constraints",
]

# TavilySearch include_domains filter (ARCHITECTURE_PROPOSAL.md §2.2b).
# R14: bloomberg.com/wsj.com dropped -- both paywalled, so search budget was
# being spent retrieving content the research LLM could never read past the
# teaser paragraph.
#
# ADR 0010 §8 item 1 / ADR 0004 §2: apnews.com and the PR wires were named
# Tier 2 in the original tier table and simply never added here. Pure
# configuration -- the decision was already taken, this closes the gap.
# PR-wire copy is a company's own announcement moved over a distribution
# service, which makes it the material *least* susceptible to the
# fabrication failure mode Faz 7b exists to reduce.
TRUSTED_DOMAINS: list[str] = [
    "reuters.com",
    "cnbc.com",
    "sec.gov",
    # Tech trade press: covers product launches, funding rounds and
    # personnel moves that the wires only pick up once they move a stock,
    # which is later than a screening digest wants to notice them. Free to
    # read, so it does not repeat the bloomberg/wsj problem above.
    "techcrunch.com",
    "apnews.com",
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
]

# R14: source-quality tier per domain, carried onto each Finding
# (pipeline.schemas.Finding.source_tier) instead of treating every trusted
# domain as equally weighted. Lower number = higher quality/primary.
#   1 = primary source (the filing itself)
#   2 = wire service / non-paywalled reputable news
#   3 = default for anything else that made it past include_domains
DOMAIN_TIERS: dict[str, int] = {
    "sec.gov": 1,
    "reuters.com": 2,
    "cnbc.com": 2,
    # Listed explicitly even though it equals DEFAULT_DOMAIN_TIER: trade
    # press is interpretive by intent (ADR 0004's tier table), not merely
    # unclassified, so it should not drift if the default ever changes.
    "techcrunch.com": 3,
    # ADR 0010 §8 item 1: PR wires -- factual distribution is the business
    # model (ADR 0004 §2's tier table), same tier as the newswires above.
    "apnews.com": 2,
    "businesswire.com": 2,
    "prnewswire.com": 2,
    "globenewswire.com": 2,
    # ADR 0010 §8 item 2: company IR / press-release feeds
    # (pipeline.research.ir_feeds) -- the company's own announcement, Tier 1
    # same as an SEC filing. Only domains with a confirmed working feed
    # (see ir_feeds.TICKER_IR_FEEDS) are listed.
    "apple.com": 1,
    "salesforce.com": 1,
    "nvidianews.nvidia.com": 1,
    "amd.com": 1,
    "newsroom.intel.com": 1,
    "news.microsoft.com": 1,
    "blog.google": 1,
    "about.fb.com": 1,
    "aboutamazon.com": 1,
    "cisco.com": 1,
    # ADR 0010 §8 item 4: the Federal Register itself
    # (pipeline.research.federal_register) -- the government's own
    # publication of a rule, as primary as a filing.
    "federalregister.gov": 1,
}
DEFAULT_DOMAIN_TIER = 3


def domain_tier(url: str) -> int:
    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, tier in DOMAIN_TIERS.items():
        if host == domain or host.endswith(f".{domain}"):
            return tier
    return DEFAULT_DOMAIN_TIER


# Cost estimation (R1): no per-model pricing table exists for the
# operator-supplied LLM_MODEL, so a single blended $/1K-token rate is used
# for both input and output tokens. Deliberately approximate -- this feeds a
# cost *trend*, not an invoice.
COST_PER_1K_INPUT_TOKENS = float(os.environ.get("PIPELINE_COST_PER_1K_INPUT_TOKENS", "0.005"))
COST_PER_1K_OUTPUT_TOKENS = float(os.environ.get("PIPELINE_COST_PER_1K_OUTPUT_TOKENS", "0.015"))

# R3: warn (never block) when a run's total findings fall below this
# fraction of the trailing median findings_count across prior persisted
# runs. 0.5 means "less than half of the recent normal" trips the warning.
YIELD_FLOOR_FRACTION = float(os.environ.get("PIPELINE_YIELD_FLOOR_FRACTION", "0.5"))
YIELD_FLOOR_TRAILING_RUNS = int(os.environ.get("PIPELINE_YIELD_FLOOR_TRAILING_RUNS", "8"))

# ADR 0010 §9 / plan §9 Q1 (decided): `covered_events` de-dup context is a
# trailing window of the last N runs rather than all history. Replaced the
# old 26-*week* retention window, which had no meaning once a unit of work
# stopped being a week. Too small and repeated runs resurface the same
# events; too large and a genuinely developing story is suppressed.
COVERED_EVENTS_TRAILING_RUNS = int(
    os.environ.get("PIPELINE_COVERED_EVENTS_TRAILING_RUNS", "4")
)


def estimate_cost_usd(*, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1000 * COST_PER_1K_INPUT_TOKENS
        + output_tokens / 1000 * COST_PER_1K_OUTPUT_TOKENS
    )


class ConfigError(RuntimeError):
    """Raised when required configuration (e.g. an API key) is missing."""


def get_tavily_api_key() -> str:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise ConfigError(
            "TAVILY_API_KEY is not set. Copy .env.example to .env and fill it "
            "in, or set the TAVILY_API_KEY environment variable directly."
        )
    return key


# R15: Exa fallback, invoked when Tavily raises or returns too few results
# (single-provider burst rate-limiting under a full-watchlist fan-out).
# Optional -- unset means no fallback, matching today's Tavily-only
# behaviour (pipeline/run.py only builds the fallback wrapper when this is
# configured).
EXA_FALLBACK_MIN_RESULTS = int(os.environ.get("PIPELINE_EXA_FALLBACK_MIN_RESULTS", "2"))


def get_exa_api_key() -> str | None:
    return os.environ.get("EXA_API_KEY") or None


# R16: SEC EDGAR full-text search leg. Delegates to
# `techinves.config.get_sec_user_agent()` rather than re-reading
# SEC_EDGAR_USER_AGENT here.
#
# The two used to be separate readers of the same env var with *different*
# absent-value semantics -- `techinves` fell back to the project default UA,
# this returned None -- justified by a "never imports from src/techinves" rule
# that stopped being true (see the module docstring). The consequence was not
# cosmetic: `pipeline/run.py` gates both SEC legs (EDGAR full-text search and
# EDGAR submissions) on this being truthy, so a fresh clone with no
# SEC_EDGAR_USER_AGENT set got a working scoring engine and silently *no*
# EDGAR research at all -- degraded coverage with nothing in the logs saying
# why. The UA is a contact address, not a secret; there is no reason for the
# two callers to disagree about what "unset" means.
#
# Return type stays `str | None` so run.py's `if user_agent:` gate keeps
# working; in practice it is now never None, which is the point.
def get_sec_edgar_user_agent() -> str | None:
    # Imported lazily: this module is imported by nearly everything in
    # `pipeline`, including contexts (the CLI's `--help`) that must not pull
    # in the techinves package's own import chain.
    from techinves.config import get_sec_user_agent

    return get_sec_user_agent() or None


def get_openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ConfigError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it "
            "in, or set the OPENAI_API_KEY environment variable directly."
        )
    return key
