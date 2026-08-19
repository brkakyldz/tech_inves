"""Cohort assignment and the minimum-size / "extended cohort" merge rule --
report_scoring_metadology.md Section 2.
"""

from __future__ import annotations

from techinves.config import COHORT_MIN_SIZE
from techinves.models import Cohort


def assign_cohort(ticker: str, watchlist: dict[str, Cohort]) -> Cohort:
    if ticker not in watchlist:
        raise KeyError(f"{ticker} is not in the watchlist")
    return watchlist[ticker]


def cohort_membership(watchlist: dict[str, Cohort]) -> dict[Cohort, list[str]]:
    membership: dict[Cohort, list[str]] = {}
    for ticker, cohort in watchlist.items():
        membership.setdefault(cohort, []).append(ticker)
    return membership


def effective_groups(
    watchlist: dict[str, Cohort], min_size: int = COHORT_MIN_SIZE
) -> tuple[dict[str, str], dict[str, bool]]:
    """Returns (ticker -> group_id, group_id -> extended_flag).

    group_id is normally just the cohort code ("A"/"B"/"C"). If a cohort's
    membership falls below `min_size`, it is iteratively merged into the
    next-largest remaining group ("extended cohort" per Section 2) until
    every group meets the minimum or only one group is left. Every ticker in
    a merged group is flagged extended=True.
    """
    membership: dict[str, list[str]] = {
        c.value: list(tickers) for c, tickers in cohort_membership(watchlist).items()
    }
    extended_ids: set[str] = set()

    while True:
        undersized = [gid for gid, members in membership.items() if len(members) < min_size]
        if not undersized or len(membership) <= 1:
            break
        smallest = min(undersized, key=lambda gid: len(membership[gid]))
        remaining = {gid: members for gid, members in membership.items() if gid != smallest}
        target = max(remaining, key=lambda gid: len(remaining[gid]))

        merged_id = f"{smallest}+{target}"
        merged_members = membership[smallest] + membership[target]
        del membership[smallest]
        del membership[target]
        membership[merged_id] = merged_members
        extended_ids.add(merged_id)

    ticker_to_group: dict[str, str] = {}
    group_extended: dict[str, bool] = {}
    for gid, members in membership.items():
        is_extended = gid in extended_ids
        group_extended[gid] = is_extended
        for ticker in members:
            ticker_to_group[ticker] = gid

    return ticker_to_group, group_extended
