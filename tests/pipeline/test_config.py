from __future__ import annotations

from pipeline.config import (
    RESEARCH_CONCURRENCY,
    TRUSTED_DOMAINS,
    domain_tier,
    get_sec_edgar_user_agent,
    load_scoring_eligible_tickers,
    load_watchlist_company_names,
    load_watchlist_tickers,
)
from pipeline.graph import default_run_config


def test_load_watchlist_tickers_reads_all_cohorts():
    tickers = load_watchlist_tickers()

    assert "NVDA" in tickers  # cohort B
    assert "MSFT" in tickers  # cohort A
    assert "ORCL" in tickers  # cohort C
    assert len(tickers) == len(set(tickers))  # no dupes across cohorts
    assert len(tickers) == 43  # ADR 0005: 43-company research universe


def test_load_watchlist_tickers_does_not_treat_scoring_excluded_as_a_cohort():
    """ADR 0005 §5: `scoring_excluded` (RKLB/ASTS/SPCX) is a flag, not a
    fourth cohort -- it must not be swept into the flattened list a second
    time (they already appear once under cohort B), and the research
    universe must still carry them (only financial *scoring*, a separate
    loader in src/techinves/watchlist.py, drops them)."""
    tickers = load_watchlist_tickers()

    assert tickers.count("RKLB") == 1
    assert tickers.count("ASTS") == 1
    assert tickers.count("SPCX") == 1
    assert "RKLB" in tickers
    assert "ASTS" in tickers
    assert "SPCX" in tickers


def test_load_scoring_eligible_tickers_drops_scoring_excluded():
    """REPORT_SPEC.md §1/D1: the Full Watchlist table -- and the verifier's
    completeness predicate against it -- is scoped to the 40 scoring-eligible
    tickers, not the 43-ticker research universe RKLB/ASTS/SPCX (ADR 0005 §5,
    `scoring_excluded`) still belong to."""
    eligible = load_scoring_eligible_tickers()
    all_tickers = load_watchlist_tickers()

    assert "RKLB" not in eligible
    assert "ASTS" not in eligible
    assert "SPCX" not in eligible
    assert "RKLB" in all_tickers
    assert "ASTS" in all_tickers
    assert "SPCX" in all_tickers
    assert len(eligible) == 40
    assert len(all_tickers) == 43
    assert len(eligible) == len(set(eligible))  # no dupes
    assert set(eligible) <= set(all_tickers)


def test_load_scoring_eligible_tickers_keeps_ordinary_tickers():
    eligible = load_scoring_eligible_tickers()
    assert "NVDA" in eligible
    assert "MSFT" in eligible
    assert "ORCL" in eligible


def test_default_run_config_caps_concurrency():
    config = default_run_config()
    assert config == {"max_concurrency": RESEARCH_CONCURRENCY}


def test_paywalled_domains_dropped_from_trusted_domains():
    assert "bloomberg.com" not in TRUSTED_DOMAINS
    assert "wsj.com" not in TRUSTED_DOMAINS


def test_domain_tier_ranks_sec_above_wire_service_above_unknown():
    assert domain_tier("https://sec.gov/filing/123") == 1
    assert domain_tier("https://www.reuters.com/article") == 2
    assert domain_tier("https://random-blog.example.com/post") == 3


def test_domain_tier_matches_subdomains():
    assert domain_tier("https://ir.sec.gov/edgar") == 1


def test_load_watchlist_company_names_from_markdown_table(tmp_path):
    md = tmp_path / "WATCHLIST.md"
    md.write_text(
        "# Watchlist\n\n"
        "| Ticker | Şirket |\n"
        "|---|---|\n"
        "| NVDA | NVIDIA Corp. |\n"
        "| MSFT | Microsoft |\n",
        encoding="utf-8",
    )
    names = load_watchlist_company_names(md)
    assert names == {"NVDA": "NVIDIA Corp.", "MSFT": "Microsoft"}


def test_load_watchlist_company_names_missing_file_returns_empty(tmp_path):
    assert load_watchlist_company_names(tmp_path / "nope.md") == {}


def test_load_watchlist_company_names_real_file_covers_full_watchlist():
    """R17 depends on this covering (most of) load_watchlist_tickers()'s
    output -- a query-widening feature that silently resolves nothing would
    be dead code."""
    names = load_watchlist_company_names()
    tickers = set(load_watchlist_tickers())
    assert set(names) & tickers  # non-empty overlap


def test_techcrunch_is_trusted_and_tiered_as_trade_press():
    assert "techcrunch.com" in TRUSTED_DOMAINS
    assert domain_tier("https://techcrunch.com/2026/08/14/some-launch/") == 3
    # ranked below the wires, above unclassified-by-default is not possible
    # in the current 3-tier map -- see the ADR-0004 drift noted in config.py
    assert domain_tier("https://techcrunch.com/x") > domain_tier("https://reuters.com/x")


def test_apnews_and_pr_wires_added_per_adr_0010_item_1():
    """ADR 0004 §2's tier table named apnews.com and the PR wires Tier 2 and
    they were never added -- ADR 0010 §8 item 1 / Faz 7b closes that gap."""
    for domain in ("apnews.com", "businesswire.com", "prnewswire.com", "globenewswire.com"):
        assert domain in TRUSTED_DOMAINS
        assert domain_tier(f"https://{domain}/release/123") == 2


def test_company_ir_domains_tier_one():
    """ADR 0010 §8 item 2: company IR / press-release feeds are Tier 1, same
    as an SEC filing -- see pipeline.research.ir_feeds.TICKER_IR_FEEDS for
    which tickers actually have a confirmed working feed."""
    assert domain_tier("https://www.apple.com/newsroom/x") == 1
    assert domain_tier("https://ir.amd.com/news/x") == 1
    assert domain_tier("https://nvidianews.nvidia.com/releases/x") == 1


def test_federal_register_domain_tier_one():
    """ADR 0010 §8 item 4: the Federal Register itself is a primary source,
    Tier 1."""
    assert domain_tier("https://www.federalregister.gov/documents/2026/08/15/x") == 1


def test_sec_edgar_user_agent_falls_back_the_same_way_techinves_does(monkeypatch):
    """A fresh clone with no SEC_EDGAR_USER_AGENT used to get a working
    scoring engine (`techinves.config` falls back to the project default UA)
    but *no* EDGAR research at all: `pipeline/run.py` gates both SEC searchers
    on this being truthy, and this reader alone returned None. Same env var,
    two absent-value semantics, duplicated on the strength of a "never imports
    techinves" rule that had already stopped being true."""
    from techinves.config import SEC_EDGAR_DEFAULT_USER_AGENT

    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    assert get_sec_edgar_user_agent() == SEC_EDGAR_DEFAULT_USER_AGENT

    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Someone (someone@example.com)")
    assert get_sec_edgar_user_agent() == "Someone (someone@example.com)"
