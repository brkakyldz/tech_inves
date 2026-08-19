from techinves.scoring.normalization import (
    MIN_PEERS_FOR_PERCENTILE,
    compute_metric_percentiles,
    percentile_rank,
    redistribute_weights,
    winsorize,
)


class TestWinsorize:
    def test_clips_extreme_values_to_bounds(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 1000]
        result = winsorize(values, lower_pct=2.5, upper_pct=97.5)
        assert max(result) < 1000
        assert result[-1] == max(result)

    def test_single_value_unchanged(self):
        assert winsorize([42.0]) == [42.0]

    def test_empty_list_unchanged(self):
        assert winsorize([]) == []

    def test_uniform_values_unchanged(self):
        values = [5.0] * 10
        assert winsorize(values) == values


class TestPercentileRank:
    def test_monotonicity(self):
        distribution = [10.0, 20.0, 30.0, 40.0, 50.0]
        ranks = [percentile_rank(v, distribution) for v in distribution]
        assert ranks == sorted(ranks)

    def test_lowest_value_gets_low_percentile(self):
        distribution = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile_rank(10.0, distribution) < 50.0

    def test_highest_value_gets_high_percentile(self):
        distribution = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile_rank(50.0, distribution) > 50.0

    def test_tied_values_get_averaged_rank(self):
        distribution = [10.0, 20.0, 20.0, 20.0, 30.0]
        # 1 below, 3 equal, n=5 -> (1 + 0.5*3)/5*100 = 50
        assert percentile_rank(20.0, distribution) == 50.0

    def test_empty_distribution_returns_midpoint(self):
        assert percentile_rank(10.0, []) == 50.0


class TestComputeMetricPercentiles:
    def test_higher_better_direction(self):
        raw = {"A": 10.0, "B": 20.0, "C": 30.0}
        result = compute_metric_percentiles("higher_better", raw)
        assert result["A"] < result["B"] < result["C"]

    def test_lower_better_direction_inverts_ranking(self):
        raw = {"A": 10.0, "B": 20.0, "C": 30.0}
        result = compute_metric_percentiles("lower_better", raw)
        assert result["A"] > result["B"] > result["C"]

    def test_none_values_stay_none(self):
        raw = {"A": 10.0, "B": None, "C": 30.0}
        result = compute_metric_percentiles("higher_better", raw)
        assert result["B"] is None
        assert result["A"] is not None
        assert result["C"] is not None

    def test_all_none_returns_all_none(self):
        raw = {"A": None, "B": None}
        result = compute_metric_percentiles("higher_better", raw)
        assert result == {"A": None, "B": None}


class TestSingleMemberCohortIsNotNeutral:
    """Section 3.4: an uncomputable metric is disabled and its weight
    redistributed -- a neutral 50 is never assigned. A cohort of one is the
    same situation: there is nothing to rank against.
    """

    def test_single_defined_value_is_not_ranked_as_neutral_fifty(self):
        result = compute_metric_percentiles("higher_better", {"A": 42.0})
        assert result["A"] is None, "n=1 must not be scored as a neutral 50"

    def test_single_defined_value_among_missing_peers_is_also_unranked(self):
        raw = {"A": 42.0, "B": None, "C": None}
        result = compute_metric_percentiles("higher_better", raw)
        assert result == {"A": None, "B": None, "C": None}

    def test_two_defined_values_are_ranked_normally(self):
        result = compute_metric_percentiles("higher_better", {"A": 10.0, "B": 20.0})
        assert result["A"] is not None and result["B"] is not None
        assert result["A"] < result["B"]

    def test_threshold_is_configurable_for_a_stricter_peer_minimum(self):
        raw = {"A": 10.0, "B": 20.0, "C": 30.0}
        assert all(v is None for v in compute_metric_percentiles("higher_better", raw, min_peers=5).values())
        assert all(v is not None for v in compute_metric_percentiles("higher_better", raw, min_peers=3).values())

    def test_default_threshold_excludes_only_the_degenerate_case(self):
        assert MIN_PEERS_FOR_PERCENTILE == 2


class TestRedistributeWeights:
    def test_sums_to_one_for_full_availability(self):
        base = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
        result = redistribute_weights(base, {"a", "b", "c", "d"})
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_sums_to_one_for_partial_availability(self):
        base = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
        result = redistribute_weights(base, {"a", "c"})
        assert abs(sum(result.values()) - 1.0) < 1e-9
        assert result["a"] == result["c"] == 0.5

    def test_sums_to_one_for_single_remaining_metric(self):
        base = {"a": 0.1, "b": 0.9}
        result = redistribute_weights(base, {"a"})
        assert result["a"] == 1.0

    def test_empty_available_returns_zeros(self):
        base = {"a": 0.5, "b": 0.5}
        result = redistribute_weights(base, set())
        assert result == {}
