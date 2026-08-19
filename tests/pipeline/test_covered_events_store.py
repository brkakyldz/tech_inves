"""Covered-events de-dup, keyed on runs rather than ISO weeks (ADR 0010 §9).

The week->run re-key must not weaken de-duplication: ADR 0010 §9 names losing
de-dup as a side effect of the keying change as the failure it is written to
prevent, so the matching/carry-forward tests below are the same behaviour
re-expressed in run terms, plus two new ones for the trailing window and for
the consecutive-run guarantee itself.
"""

from __future__ import annotations

from pipeline.schemas import CoveredEvent, Finding
from pipeline.storage.covered_events_store import (
    _titles_match,
    covered_source_urls,
    load_covered_events,
    make_event_key,
    prune_to_window,
    save_covered_events,
    update_covered_events,
)


def _finding(**overrides) -> Finding:
    base = dict(
        scope="company",
        ticker="NVDA",
        topic=None,
        event_title="New export licensing rules",
        event_type="regulatory",
        narrative="Some narrative.",
        source_urls=["https://reuters.com/a"],
    )
    base.update(overrides)
    return Finding(**base)


def _event(**overrides) -> CoveredEvent:
    base = dict(
        scope="company",
        company="NVDA",
        event_type="regulatory",
        event_title="New export licensing rules",
        first_covered_run="run-1",
        last_updated_run="run-1",
        run_seq=1,
    )
    base.update(overrides)
    base.setdefault(
        "event_key",
        make_event_key(
            base["scope"],
            base.get("company"),
            base.get("topic"),
            base["event_type"],
            base["event_title"],
        ),
    )
    return CoveredEvent(**base)


def test_update_covered_events_adds_new_event():
    result = update_covered_events([], [_finding()], run_id="run-1")

    assert len(result) == 1
    event = result[0]
    assert event.company == "NVDA"
    assert event.event_title == "New export licensing rules"
    assert event.first_covered_run == "run-1"
    assert event.last_updated_run == "run-1"
    assert event.run_seq == 1
    assert event.event_key  # stable identity is assigned at creation


def test_update_covered_events_bumps_existing_match_and_merges_urls():
    existing = [_event(source_urls=["https://reuters.com/a"])]
    finding = _finding(source_urls=["https://bloomberg.com/b"])

    result = update_covered_events(existing, [finding], run_id="run-2")

    assert len(result) == 1
    event = result[0]
    assert event.first_covered_run == "run-1"  # unchanged
    assert event.last_updated_run == "run-2"  # bumped
    assert event.run_seq == 2
    assert event.event_key == existing[0].event_key  # identity survives the bump
    assert event.source_urls == ["https://reuters.com/a", "https://bloomberg.com/b"]


def test_update_covered_events_carries_forward_untouched_events():
    existing = [
        _event(
            scope="macro",
            company=None,
            topic="AI capex cycle / hyperscaler capital expenditure",
            event_type="macro",
            event_title="Hyperscaler capex guidance raised",
        )
    ]

    result = update_covered_events(existing, [], run_id="run-2")

    assert result == existing


def test_update_covered_events_matches_macro_findings_by_topic():
    finding = _finding(
        scope="macro",
        ticker=None,
        topic="AI capex cycle / hyperscaler capital expenditure",
        event_title="Hyperscaler capex guidance raised",
        event_type="macro",
    )
    existing = [
        _event(
            scope="macro",
            company=None,
            topic="AI capex cycle / hyperscaler capital expenditure",
            event_type="macro",
            event_title="Hyperscaler capex guidance raised",
        )
    ]

    result = update_covered_events(existing, [finding], run_id="run-2")

    assert len(result) == 1
    assert result[0].last_updated_run == "run-2"


def test_two_consecutive_runs_do_not_resurface_the_same_event():
    """ADR 0010 §9's actual guarantee, stated directly.

    Run 1 reports an event; run 2 retrieves the same event again. The store
    must recognise it as already covered -- one event, not two -- and carry
    the original `first_covered_run`, which is what
    `graph._covered_events_context` shows the research branch so it stops
    resurfacing it. This is the assertion that would have failed had the
    re-key quietly dropped de-duplication.
    """
    after_run_1 = update_covered_events([], [_finding()], run_id="run-1")
    assert len(after_run_1) == 1

    after_run_2 = update_covered_events(after_run_1, [_finding()], run_id="run-2")

    assert len(after_run_2) == 1
    assert after_run_2[0].first_covered_run == "run-1"
    assert after_run_2[0].last_updated_run == "run-2"


def test_events_outside_the_trailing_window_are_dropped():
    """Loading returns the last N runs, not all history (plan §9 Q1: N=4)."""
    stale = _event(event_title="Ancient news", run_seq=1, event_key="stale")
    events = [stale]
    # Distinct event_types put each finding in its own bucket, so these are
    # four genuinely different events rather than one repeatedly matched one.
    for seq in range(2, 6):
        events = update_covered_events(
            events,
            [_finding(event_type=f"type-{seq}", event_title=f"Fresh item {seq}")],
            run_id=f"run-{seq}",
        )

    titles = {e.event_title for e in events}
    assert "Ancient news" not in titles  # five runs deep, window is four
    assert titles == {f"Fresh item {seq}" for seq in range(2, 6)}


def test_prune_to_window_keeps_exactly_the_last_n_runs():
    events = [_event(event_title=f"e{seq}", run_seq=seq, event_key=f"k{seq}") for seq in range(1, 8)]

    kept = prune_to_window(events, window=4)

    assert {e.run_seq for e in kept} == {4, 5, 6, 7}


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "covered_events.json"
    events = [_event(last_updated_run="run-2", run_seq=2, source_urls=["https://reuters.com/a"])]

    save_covered_events(events, path=path)
    loaded = load_covered_events(path=path)

    assert loaded == events


def test_load_missing_file_returns_empty_list(tmp_path):
    path = tmp_path / "does-not-exist.json"
    assert load_covered_events(path=path) == []


def test_titles_match_identical():
    assert _titles_match("NVDA launches new AI chip", "NVDA launches new AI chip") is True


def test_titles_match_unrelated_titles_do_not_match():
    assert _titles_match("NVDA launches new AI chip", "Fed holds interest rates steady") is False


def test_reworded_headline_matches_existing_event_via_event_type_bucket():
    """R18: a headline reworded between runs must not read as a new event
    as long as it shares scope/company/event_type and overlaps enough."""
    existing = [
        _event(
            event_type="product",
            event_title="NVDA launches new AI chip for datacenters",
            source_urls=["https://reuters.com/a"],
        )
    ]
    finding = _finding(
        event_type="product",
        event_title="NVDA chip launch for datacenters expands",
        source_urls=["https://cnbc.com/b"],
    )

    result = update_covered_events(existing, [finding], run_id="run-2")

    assert len(result) == 1
    # Updated to this run's headline, not frozen at the first one seen -- a
    # matched event is still evolving, and de-dup context shown to later runs
    # should reflect the latest wording, not silently discard it.
    assert result[0].event_title == "NVDA chip launch for datacenters expands"
    assert result[0].last_updated_run == "run-2"
    # ...but its identity does not move with the title, so the store can
    # update the row in place instead of inserting a second one.
    assert result[0].event_key == existing[0].event_key
    assert "https://cnbc.com/b" in result[0].source_urls


def test_distinct_events_of_the_same_type_stay_separate():
    """Widening must not collapse two genuinely different same-type events
    for the same entity into one."""
    existing = [
        _event(event_type="product", event_title="NVDA launches new AI chip for datacenters")
    ]
    finding = _finding(
        event_type="product",
        event_title="NVDA announces partnership with automotive manufacturer",
    )

    result = update_covered_events(existing, [finding], run_id="run-2")

    assert len(result) == 2


def test_different_event_type_does_not_match_even_with_similar_title():
    existing = [_event(event_type="product", event_title="NVDA chip launch event")]
    finding = _finding(event_type="regulatory", event_title="NVDA chip launch event")

    result = update_covered_events(existing, [finding], run_id="run-2")

    assert len(result) == 2


def test_covered_source_urls_flattens_every_cited_url():
    """Feeds the highlight pre-selection probe, which must be able to tell
    "this ticker had a busy week" from "this ticker's week was already
    extracted by the previous run" without reading a single result body."""
    events = [
        CoveredEvent(
            event_key="a",
            scope="company",
            company="NVDA",
            topic=None,
            event_type="Product launch",
            event_title="New accelerator",
            first_covered_run="r1",
            last_updated_run="r1",
            run_seq=1,
            source_urls=["https://reuters.com/1", "https://cnbc.com/2"],
        ),
        CoveredEvent(
            event_key="b",
            scope="macro",
            company=None,
            topic="Fed / interest rate policy",
            event_type="Policy",
            event_title="Rates held",
            first_covered_run="r1",
            last_updated_run="r2",
            run_seq=2,
            # Overlaps the event above -- a set, so it is counted once.
            source_urls=["https://reuters.com/1", "https://apnews.com/3"],
        ),
    ]

    assert covered_source_urls(events) == {
        "https://reuters.com/1",
        "https://cnbc.com/2",
        "https://apnews.com/3",
    }


def test_covered_source_urls_of_nothing_is_an_empty_set():
    """First run, or a caller that passes no de-dup state: the probe must
    degrade to an unfiltered ranking, never to "everything is covered"."""
    assert covered_source_urls([]) == set()
