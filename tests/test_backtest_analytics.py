"""Unit tests for backtest.analytics — pure math, no DB."""
import math

from backtest.analytics import (
    Funnel,
    compare_variants,
    percentile,
    two_proportion_z,
    wilson_interval,
)


def test_funnel_rates():
    f = Funnel(sent=100, replied=30, converted=9, onboarded=3)
    assert f.reply_rate == 30.0
    assert f.conversion_rate == 9.0
    assert f.onboard_rate == 3.0


def test_funnel_rates_zero_sent():
    f = Funnel()
    assert f.reply_rate == 0.0
    assert f.conversion_rate == 0.0


def test_reach_rate_equals_reply_rate():
    # Documented contract: reach is a lower bound == reply rate.
    f = Funnel(sent=50, replied=10)
    assert f.reach_rate == f.reply_rate


def test_percentile_median_and_p90():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(values, 50) == 5
    assert percentile(values, 90) == 9
    assert percentile(values, 100) == 10


def test_percentile_empty_is_none():
    assert percentile([], 50) is None


def test_median_latency_uses_samples():
    f = Funnel(latencies=[10, 20, 30, 40])
    assert f.median_latency() == 20
    assert f.p90_latency() == 40


def test_wilson_interval_brackets_point_estimate():
    low, high = wilson_interval(30, 100)
    assert low < 30 < high
    assert 0 <= low and high <= 100


def test_wilson_interval_zero_trials():
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_wilson_interval_wider_for_small_n():
    narrow = wilson_interval(300, 1000)
    wide = wilson_interval(3, 10)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_two_proportion_detects_large_difference():
    """With big samples and a big gap, the test must call it significant."""
    res = two_proportion_z(success_a=400, trials_a=1000, success_b=200, trials_b=1000)
    assert res["significant"] is True
    assert res["lift_pct"] == 100.0  # 40% vs 20%
    assert res["p_value"] < 0.05


def test_two_proportion_not_significant_when_similar():
    res = two_proportion_z(200, 1000, 210, 1000)
    assert res["significant"] is False


def test_two_proportion_zero_trials_is_safe():
    res = two_proportion_z(0, 0, 5, 10)
    assert res["significant"] is False
    assert res["z"] == 0.0


def test_compare_variants_ranks_by_rate():
    funnels = {
        0: Funnel(sent=100, replied=10),
        1: Funnel(sent=100, replied=40),
    }
    ranking = compare_variants(funnels, metric="replied")
    assert ranking[0]["variant"] == 1
    assert ranking[0]["rate_pct"] == 40.0
    assert ranking[1]["variant"] == 0


def test_compare_variants_includes_ci():
    ranking = compare_variants({0: Funnel(sent=50, replied=10)}, metric="replied")
    row = ranking[0]
    assert row["ci95_low"] < row["rate_pct"] < row["ci95_high"]


def test_normal_cdf_is_monotonic():
    from backtest.analytics import _normal_cdf
    assert _normal_cdf(-3) < _normal_cdf(0) < _normal_cdf(3)
    assert math.isclose(_normal_cdf(0), 0.5, abs_tol=1e-9)
