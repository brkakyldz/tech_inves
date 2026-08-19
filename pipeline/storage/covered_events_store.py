"""Load/save `CoveredEvent` de-dup state between runs, and merge a run's
`research_findings` into it.

Not a LangGraph node -- the covered_events-update node is explicitly out of
scope for this package (see pipeline/__init__.py). This is a plain helper a
caller (e.g. pipeline/run.py) invokes before building the graph input and
after the graph finishes.

This module owns the *merge and window* semantics for both stores. The
DB-backed store (`covered_events_db_store.py`) persists exactly what
`update_covered_events()` returns and prunes on the same `run_seq` window,
so the two cannot drift apart: ADR 0010 §9 is explicit that losing de-dup as
a side effect of the week->run re-key is the failure this is written to
prevent.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pipeline.config import COVERED_EVENTS_TRAILING_RUNS, REPO_ROOT
from pipeline.schemas import CoveredEvent, Finding

DEFAULT_STORE_PATH = REPO_ROOT / "data" / "covered_events.json"

# R18: exact event_title equality was the entire de-dup key, so a headline
# reworded between runs ("NVDA launches new AI chip" vs "NVIDIA unveils
# next-gen AI accelerator") read as a brand-new event. `event_type` now
# buckets candidates (scope + company/topic + event_type), and matching
# within a bucket is a coarse word-overlap check rather than exact title
# equality -- wide enough to catch a reworded headline, narrow enough that
# two genuinely different same-type events for the same entity inside one
# trailing window still stay separate. Real semantic similarity (embeddings) is deferred to
# Phase 5; this is the cheap interim widening.
TITLE_OVERLAP_THRESHOLD = 0.3
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "its", "new", "at"}
)


def _significant_words(title: str) -> set[str]:
    return {
        w for w in _WORD_RE.findall(title.lower()) if w not in _STOPWORDS and len(w) > 2
    }


def _titles_match(a: str, b: str) -> bool:
    """Jaccard overlap of significant words >= TITLE_OVERLAP_THRESHOLD, or
    exact (normalized) equality when either title has no significant words
    at all (e.g. a title that's entirely stopwords/short tokens)."""
    words_a, words_b = _significant_words(a), _significant_words(b)
    if not words_a or not words_b:
        return a.strip().lower() == b.strip().lower()
    overlap = len(words_a & words_b) / len(words_a | words_b)
    return overlap >= TITLE_OVERLAP_THRESHOLD


def load_covered_events(path: Path | None = None) -> list[CoveredEvent]:
    """Read the de-dup log from disk. Missing file -> empty list (first run)."""
    store_path = path or DEFAULT_STORE_PATH
    if not store_path.exists():
        return []
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    return [CoveredEvent.model_validate(item) for item in raw]


def save_covered_events(events: list[CoveredEvent], path: Path | None = None) -> None:
    store_path = path or DEFAULT_STORE_PATH
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [e.model_dump(mode="json") for e in events]
    store_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def covered_source_urls(events: list[CoveredEvent]) -> set[str]:
    """Every source URL already cited by an event inside the trailing window.

    Exists for the highlight pre-selection probe
    (`pipeline/research/highlight_selection.py`), which ranks tickers by how
    much *fresh* material a search returns. Without this, a ticker whose
    entire week of coverage was already extracted by a previous run still
    ranks as if it were breaking news, and then yields nothing when the full
    branch runs and the extraction step correctly recognises it as covered.

    A URL set is the right currency for that check because it is exactly what
    both sides have: the probe holds search-result URLs and never reads their
    contents (it must stay LLM-free), and a `CoveredEvent` carries the URLs
    its finding was grounded in. It is deliberately *not* a similarity check
    -- title matching is `_titles_match`'s job, on the extraction side, where
    there is a title to match.

    This is de-duplication state, not a score: nothing derived from
    `composite_score` or any scoring output passes through here, so feeding it
    into selection stays inside ADR 0006 §3.
    """
    return {url for e in events for url in e.source_urls if url}


def _bucket_key(scope: str, company: str | None, topic: str | None, event_type: str) -> tuple:
    return (scope, company, topic, event_type)


def make_event_key(
    scope: str, company: str | None, topic: str | None, event_type: str, first_title: str
) -> str:
    """Stable per-event id, derived once from the bucket plus the title the
    event was *first* seen under.

    Derived rather than random so the JSON store round-trips deterministically
    and two stores fed the same history agree. Based on the first title, not
    the current one, because a matched event's headline is deliberately
    updated in place (see `update_covered_events`) and the identity has to
    survive that -- it is what lets the DB store update a row instead of
    wiping the table and re-inserting.
    """
    raw = "|".join([scope, company or "", topic or "", event_type, first_title.strip().lower()])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def next_run_seq(existing: list[CoveredEvent]) -> int:
    """The ordinal this run occupies in the trailing window.

    Derived from the store rather than from the `runs` table so the window
    means the same thing in both stores, and so a run that persists no events
    (e.g. a blocked run, which deliberately leaves covered_events untouched)
    does not consume a slot in it.
    """
    return max((e.run_seq for e in existing), default=0) + 1


def prune_to_window(
    events: list[CoveredEvent], *, window: int = COVERED_EVENTS_TRAILING_RUNS
) -> list[CoveredEvent]:
    """Keep only the events last touched within the trailing `window` runs.

    The newest `run_seq` present defines "now", so this gives the same answer
    whether it is applied at save time (JSON store) or at load time (DB
    store)."""
    if not events:
        return []
    cutoff = max(e.run_seq for e in events) - window + 1
    return [e for e in events if e.run_seq >= cutoff]


def update_covered_events(
    existing: list[CoveredEvent],
    findings: list[Finding],
    *,
    run_id: str,
    window: int = COVERED_EVENTS_TRAILING_RUNS,
) -> list[CoveredEvent]:
    """Merge this run's findings into the covered-events log.

    A finding matches an existing event (R18) if they share a
    scope/company/topic/event_type bucket *and* their titles overlap
    (`_titles_match`) -- not exact event_title equality. A match bumps that
    event's `last_updated_run`/`run_seq`; no match becomes a new event with
    `first_covered_run == run_id`. Events not touched by this run are carried
    forward unchanged, so they stay available as de-dup context
    (`_covered_events_context` in graph.py) to later runs -- that carry-forward
    is what stops two consecutive runs resurfacing the same events, and the
    week->run re-key leaves it exactly as it was (ADR 0010 §9).

    `window` is the trailing-run retention: events last touched more than
    `window - 1` runs ago are dropped, replacing the 26-*week* cutoff that had
    no meaning once a unit of work stopped being a week.
    """
    seq = next_run_seq(existing)

    buckets: dict[tuple, list[CoveredEvent]] = {}
    for e in existing:
        buckets.setdefault(_bucket_key(e.scope, e.company, e.topic, e.event_type), []).append(e)

    for f in findings:
        bucket = buckets.setdefault(_bucket_key(f.scope, f.ticker, f.topic, f.event_type), [])
        match_idx = next(
            (i for i, e in enumerate(bucket) if _titles_match(e.event_title, f.event_title)), None
        )
        if match_idx is None:
            bucket.append(
                CoveredEvent(
                    event_key=make_event_key(
                        f.scope, f.ticker, f.topic, f.event_type, f.event_title
                    ),
                    scope=f.scope,
                    company=f.ticker,
                    topic=f.topic,
                    event_type=f.event_type,
                    event_title=f.event_title,
                    first_covered_run=run_id,
                    last_updated_run=run_id,
                    run_seq=seq,
                    source_urls=f.source_urls,
                )
            )
        else:
            current = bucket[match_idx]
            merged_urls = list(dict.fromkeys(current.source_urls + f.source_urls))
            # Keep this run's title, not the original -- a matched event is
            # still evolving (e.g. "beat estimates" -> "raises guidance"),
            # and the de-dup context shown to later runs
            # (_covered_events_context) should reflect the latest headline,
            # not silently pin the first one seen. `event_key` is derived
            # from the *first* title, so it deliberately does not move with it.
            bucket[match_idx] = current.model_copy(
                update={
                    "last_updated_run": run_id,
                    "run_seq": seq,
                    "source_urls": merged_urls,
                    "event_title": f.event_title,
                }
            )

    merged = [e for bucket in buckets.values() for e in bucket]
    return prune_to_window(merged, window=window)
