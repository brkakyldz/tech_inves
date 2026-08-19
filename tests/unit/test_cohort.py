from techinves.models import Cohort
from techinves.scoring.cohort import assign_cohort, cohort_membership, effective_groups


def _watchlist(a=14, b=15, c=13):
    wl: dict[str, Cohort] = {}
    for i in range(a):
        wl[f"A{i}"] = Cohort.SOFTWARE_INTERNET
    for i in range(b):
        wl[f"B{i}"] = Cohort.HARDWARE_SEMI_SPACE
    for i in range(c):
        wl[f"C{i}"] = Cohort.IT_SERVICES_INFRA
    return wl


def test_assign_cohort_returns_correct_cohort():
    wl = {"NVDA": Cohort.HARDWARE_SEMI_SPACE}
    assert assign_cohort("NVDA", wl) == Cohort.HARDWARE_SEMI_SPACE


def test_assign_cohort_raises_for_unknown_ticker():
    wl = {"NVDA": Cohort.HARDWARE_SEMI_SPACE}
    try:
        assign_cohort("UNKNOWN", wl)
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_cohort_membership_groups_correctly():
    wl = _watchlist(a=2, b=1, c=0)
    membership = cohort_membership(wl)
    assert set(membership[Cohort.SOFTWARE_INTERNET]) == {"A0", "A1"}
    assert set(membership[Cohort.HARDWARE_SEMI_SPACE]) == {"B0"}


def test_no_merge_when_all_cohorts_meet_minimum():
    wl = _watchlist(a=14, b=15, c=13)
    ticker_to_group, group_extended = effective_groups(wl, min_size=13)
    assert ticker_to_group["A0"] == "A"
    assert ticker_to_group["B0"] == "B"
    assert ticker_to_group["C0"] == "C"
    assert not any(group_extended.values())


def test_undersized_cohort_merges_into_largest_remaining():
    wl = _watchlist(a=5, b=15, c=13)
    ticker_to_group, group_extended = effective_groups(wl, min_size=13)
    # A (5) is undersized, merges into the largest remaining group (B, 15)
    a_group = ticker_to_group["A0"]
    b_group = ticker_to_group["B0"]
    assert a_group == b_group
    assert group_extended[a_group] is True
    # C meets the minimum on its own and is untouched
    assert ticker_to_group["C0"] == "C"
    assert group_extended["C"] is False


def test_every_ticker_ends_up_in_exactly_one_group():
    wl = _watchlist(a=5, b=6, c=13)
    ticker_to_group, _ = effective_groups(wl, min_size=13)
    assert set(ticker_to_group.keys()) == set(wl.keys())
