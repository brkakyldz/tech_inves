from __future__ import annotations

import inspect
from datetime import date

from pipeline.research.highlight_selection import _build_query, select_highlight_tickers
from pipeline.research.tavily_client import TavilySearchResult
from pipeline.schemas import CoveredEvent
from pipeline.verifier.rules import COMPLETENESS_MAX_HIGHLIGHTS, COMPLETENESS_MIN_HIGHLIGHTS


class _UrlFakeSearcher:
    """Search-only fake returning an explicit URL list per ticker, so a test
    can control freshness (against covered_events) and domain breadth
    independently of raw count."""

    def __init__(self, urls_by_ticker: dict[str, list[str]]):
        self._urls = urls_by_ticker
        self.queries: list[str] = []

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        self.queries.append(query)
        ticker = next((t for t in self._urls if query.startswith(t)), None)
        return [TavilySearchResult({"url": u}) for u in self._urls.get(ticker, [])]


def _covered(urls: list[str]) -> CoveredEvent:
    return CoveredEvent(
        event_key="k" + str(abs(hash(tuple(urls)))),
        scope="company",
        company="X",
        topic=None,
        event_type="Earnings",
        event_title="Prior event",
        first_covered_run="r1",
        last_updated_run="r1",
        run_seq=1,
        source_urls=urls,
    )


class _CountingFakeSearcher:
    """Search-only fake: returns `counts[ticker]` results for a query built
    from that ticker, optionally raising for tickers in `fail_tickers`.
    Query -> ticker is resolved by prefix match since
    `highlight_selection._build_query` always starts with the ticker."""

    def __init__(self, counts: dict[str, int], fail_tickers: set[str] | None = None):
        self._counts = counts
        self._fail_tickers = fail_tickers or set()
        self.queries: list[str] = []
        self.windows: list[tuple[str | None, str | None]] = []

    def _ticker_for(self, query: str) -> str | None:
        for ticker in self._counts:
            if query.startswith(ticker):
                return ticker
        return None

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        self.queries.append(query)
        self.windows.append((start_date, end_date))
        ticker = self._ticker_for(query)
        if ticker in self._fail_tickers:
            raise RuntimeError("simulated Tavily failure")
        count = self._counts.get(ticker, 0)
        return [
            TavilySearchResult({"url": f"https://reuters.com/{ticker}/{i}"})
            for i in range(count)
        ]


def test_selects_top_n_ranked_by_result_count():
    searcher = _CountingFakeSearcher({"NVDA": 5, "MSFT": 3, "AMD": 1, "ORCL": 0})

    selected = select_highlight_tickers(
        searcher=searcher,
        tickers=["NVDA", "MSFT", "AMD", "ORCL"],
        company_names={},
        limit=3,
        min_limit=3,
    )

    assert selected == ["NVDA", "MSFT", "AMD"]


def test_clamps_to_verifier_bounds_by_default():
    counts = {t: i for i, t in enumerate(["A", "B", "C", "D", "E", "F"])}
    searcher = _CountingFakeSearcher(counts)

    selected = select_highlight_tickers(
        searcher=searcher, tickers=list(counts), company_names={}
    )

    assert len(selected) == COMPLETENESS_MAX_HIGHLIGHTS
    assert len(selected) >= COMPLETENESS_MIN_HIGHLIGHTS
    # highest counts (E=4, D=3, C=2, B=1) win over the lowest (A=0, F=5 excluded by limit? )
    # F has the highest count (5) so it must be included ahead of lower counts.
    assert selected[0] == "F"


def test_ties_break_deterministically_by_watchlist_order():
    # All four tied at the same count -- input order must decide, not dict
    # iteration order or ticker alphabetization.
    searcher = _CountingFakeSearcher({"ZETA": 2, "ALPHA": 2, "MID": 2})

    selected_1 = select_highlight_tickers(
        searcher=searcher,
        tickers=["ZETA", "ALPHA", "MID"],
        company_names={},
        limit=3,
        min_limit=3,
    )
    selected_2 = select_highlight_tickers(
        searcher=_CountingFakeSearcher({"ZETA": 2, "ALPHA": 2, "MID": 2}),
        tickers=["ZETA", "ALPHA", "MID"],
        company_names={},
        limit=3,
        min_limit=3,
    )

    assert selected_1 == ["ZETA", "ALPHA", "MID"]  # input order preserved on a full tie
    assert selected_1 == selected_2  # same input -> same output, every time


def test_provider_failure_for_one_ticker_does_not_crash_the_run():
    searcher = _CountingFakeSearcher(
        {"NVDA": 5, "MSFT": 3, "AMD": 1}, fail_tickers={"NVDA"}
    )

    selected = select_highlight_tickers(
        searcher=searcher,
        tickers=["NVDA", "MSFT", "AMD"],
        company_names={},
        limit=3,
        min_limit=3,
        max_retries=1,
        backoff_seconds=0.0,
        sleep_fn=lambda _seconds: None,
    )

    # NVDA's search failed every attempt -> treated as 0, not dropped, not
    # a crash -- it's simply outranked by tickers that returned real counts.
    assert set(selected) == {"NVDA", "MSFT", "AMD"}
    assert selected[0] == "MSFT"  # 3 beats AMD's 1 and NVDA's degraded 0
    assert selected[-1] == "NVDA"


def test_provider_failure_for_every_ticker_still_returns_min_limit_tickers():
    searcher = _CountingFakeSearcher({}, fail_tickers={"NVDA", "MSFT", "AMD"})

    selected = select_highlight_tickers(
        searcher=searcher,
        tickers=["NVDA", "MSFT", "AMD"],
        company_names={},
        limit=3,
        min_limit=3,
        max_retries=0,
        backoff_seconds=0.0,
        sleep_fn=lambda _seconds: None,
    )

    # A total provider outage degrades ranking quality (everyone ties at 0,
    # so watchlist order decides) but never availability.
    assert selected == ["NVDA", "MSFT", "AMD"]


def test_no_score_input_can_influence_selection():
    """ADR 0006 §3 rejects any score-based fallback or tie-break, including
    as a secondary signal. Structural guarantee: the function accepts no
    `scores`/`composite_score`-shaped argument at all, so there is nothing
    for a caller to even pass in."""
    params = set(inspect.signature(select_highlight_tickers).parameters)
    assert not any("score" in p.lower() for p in params)


def test_query_includes_company_name_when_known():
    searcher = _CountingFakeSearcher({"NVDA": 1})

    select_highlight_tickers(
        searcher=searcher,
        tickers=["NVDA"],
        company_names={"NVDA": "NVIDIA Corp."},
        limit=1,
        min_limit=1,
    )

    assert any("NVIDIA Corp." in q for q in searcher.queries)


def test_already_covered_urls_are_discounted_from_the_ranking():
    """The 2026-08-19 failure in miniature: a ticker whose whole week of
    coverage was already extracted by a previous run must not outrank one
    with genuinely fresh material, because the extraction step that follows
    will correctly recognise the covered material and yield nothing."""
    searcher = _UrlFakeSearcher(
        {
            "STALE": [f"https://reuters.com/stale/{i}" for i in range(6)],
            "FRESH": [f"https://cnbc.com/fresh/{i}" for i in range(3)],
        }
    )
    covered = [_covered([f"https://reuters.com/stale/{i}" for i in range(6)])]

    # Without the de-dup context STALE wins on raw volume, 6 to 3.
    assert select_highlight_tickers(
        searcher=_UrlFakeSearcher(
            {
                "STALE": [f"https://reuters.com/stale/{i}" for i in range(6)],
                "FRESH": [f"https://cnbc.com/fresh/{i}" for i in range(3)],
            }
        ),
        tickers=["STALE", "FRESH"],
        company_names={},
        limit=1,
        min_limit=1,
    ) == ["STALE"]

    # With it, every one of STALE's hits is already covered -> FRESH wins.
    assert select_highlight_tickers(
        searcher=searcher,
        tickers=["STALE", "FRESH"],
        company_names={},
        covered_events=covered,
        limit=1,
        min_limit=1,
    ) == ["FRESH"]


def test_duplicate_urls_do_not_inflate_a_tickers_rank():
    """One wire story syndicated across the allowlist is one event, not
    five -- `len(results)` used to score it as five."""
    searcher = _UrlFakeSearcher(
        {
            "DUPE": ["https://reuters.com/a"] * 5,
            "REAL": ["https://reuters.com/b", "https://cnbc.com/c"],
        }
    )

    selected = select_highlight_tickers(
        searcher=searcher, tickers=["DUPE", "REAL"], company_names={}, limit=1, min_limit=1
    )

    assert selected == ["REAL"]


def test_domain_breadth_breaks_a_tie_on_fresh_count():
    """Second component of the rank key: equal fresh counts, but coverage
    spread across independent outlets is the stronger event signal -- and it
    keeps discriminating after the raw count has saturated the provider cap."""
    searcher = _UrlFakeSearcher(
        {
            "NARROW": [f"https://reuters.com/n/{i}" for i in range(3)],
            "BROAD": [
                "https://reuters.com/b",
                "https://cnbc.com/b",
                "https://apnews.com/b",
            ],
        }
    )

    selected = select_highlight_tickers(
        searcher=searcher, tickers=["NARROW", "BROAD"], company_names={}, limit=1, min_limit=1
    )

    assert selected == ["BROAD"]


def test_probe_query_is_event_shaped_and_carries_no_calendar_phrasing():
    """The probe must ask for the same kind of material the research branch
    it predicts asks for. "this week" is gone deliberately: the window is
    pinned via start_date/end_date, so repeating it as query text only
    biases retrieval toward documents containing the phrase."""
    query = _build_query("NVDA", "NVIDIA Corp.")

    assert query.startswith("NVDA")
    assert "NVIDIA Corp." in query
    assert "this week" not in query
    assert any(word in query for word in ("earnings", "acquisition", "product launch"))


def test_covered_events_omitted_leaves_ranking_unfiltered():
    """The discount is additive: a caller that passes no de-dup state gets
    exactly the pre-existing behaviour, never an empty-covered-set penalty."""
    searcher = _UrlFakeSearcher(
        {"A": ["https://reuters.com/1", "https://reuters.com/2"], "B": ["https://cnbc.com/1"]}
    )

    selected = select_highlight_tickers(
        searcher=searcher, tickers=["A", "B"], company_names={}, limit=1, min_limit=1
    )

    assert selected == ["A"]


def test_search_window_pinned_to_as_of():
    searcher = _CountingFakeSearcher({"NVDA": 1})

    select_highlight_tickers(
        searcher=searcher,
        tickers=["NVDA"],
        company_names={},
        as_of=date(2026, 8, 10),
        limit=1,
        min_limit=1,
    )

    assert searcher.windows[0] == ("2026-08-04", "2026-08-10")


def test_no_as_of_leaves_window_unpinned():
    searcher = _CountingFakeSearcher({"NVDA": 1})

    select_highlight_tickers(
        searcher=searcher, tickers=["NVDA"], company_names={}, limit=1, min_limit=1
    )

    assert searcher.windows[0] == (None, None)
