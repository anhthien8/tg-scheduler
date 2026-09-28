"""Pure analytics for backtesting — no DB, no I/O.

Everything here is deterministic and unit-testable. The replay layer
(backtest.replay) feeds these functions with real rows from SQLite.

Key differences from the legacy backtest_campaign.py:
  - No fabricated "opened = replied". Telegram has no read receipts, so
    open-rate is reported as an explicit lower bound (reaches = replies),
    labelled as such, never as a real open metric.
  - Variant A/B comparison reports Wilson confidence intervals and a
    two-proportion z-test, instead of a fake idx%2 split.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Intent score at/above which a lead counts as "converted" (high intent).
CONVERSION_INTENT_THRESHOLD = 70


@dataclass
class Funnel:
    """Aggregated funnel counters for one cohort (overall or one variant)."""
    sent: int = 0
    replied: int = 0
    converted: int = 0
    onboarded: int = 0
    # Latency samples (seconds) for replied leads, used for median/percentiles.
    latencies: list[float] = field(default_factory=list)

    @property
    def reply_rate(self) -> float:
        return _pct(self.replied, self.sent)

    @property
    def conversion_rate(self) -> float:
        return _pct(self.converted, self.sent)

    @property
    def onboard_rate(self) -> float:
        return _pct(self.onboarded, self.sent)

    @property
    def reach_rate(self) -> float:
        """Lower-bound 'reached' rate: a reply proves the message was read."""
        return _pct(self.replied, self.sent)

    def median_latency(self) -> float | None:
        return percentile(self.latencies, 50)

    def p90_latency(self) -> float | None:
        return percentile(self.latencies, 90)


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Returns None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    if pct <= 0:
        return ordered[0]
    if pct >= 100:
        return ordered[-1]
    # Nearest-rank: ceil(p/100 * N), 1-indexed.
    rank = math.ceil(pct / 100 * len(ordered))
    return ordered[max(rank - 1, 0)]


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion, in percent (0-100).

    More honest than the normal approximation when trials are small — which is
    exactly the regime of early outreach campaigns.
    """
    if trials <= 0:
        return (0.0, 0.0)
    phat = successes / trials
    denom = 1 + z * z / trials
    centre = phat + z * z / (2 * trials)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * trials)) / trials)
    low = max(0.0, (centre - margin) / denom)
    high = min(1.0, (centre + margin) / denom)
    return (round(low * 100, 2), round(high * 100, 2))


def two_proportion_z(success_a: int, trials_a: int, success_b: int, trials_b: int) -> dict:
    """Two-proportion z-test between variants A and B (two-tailed).

    Returns {z, p_value, significant (at 0.05), lift_pct}. Returns
    significant=False and z=0 when either arm has no trials or pooled p is 0.
    """
    if trials_a <= 0 or trials_b <= 0:
        return {"z": 0.0, "p_value": 1.0, "significant": False, "lift_pct": None}

    p_a = success_a / trials_a
    p_b = success_b / trials_b
    pooled = (success_a + success_b) / (trials_a + trials_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / trials_a + 1 / trials_b))
    if se == 0:
        return {"z": 0.0, "p_value": 1.0, "significant": False, "lift_pct": None}

    z = (p_a - p_b) / se
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    lift = None if p_b == 0 else round((p_a - p_b) / p_b * 100, 2)
    return {
        "z": round(z, 3),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "lift_pct": lift,
    }


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via erf (no scipy dependency)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def compare_variants(funnels: dict[int, Funnel], metric: str = "replied") -> list[dict]:
    """Rank variants by a funnel metric with Wilson CIs.

    metric is a Funnel attribute holding the numerator (e.g. "replied",
    "converted", "onboarded"). Returns a list sorted best-first, each entry:
      {variant, sent, numerator, rate_pct, ci95_low, ci95_high}
    """
    rows = []
    for variant, f in funnels.items():
        numerator = getattr(f, metric, 0)
        low, high = wilson_interval(numerator, f.sent)
        rows.append({
            "variant": variant,
            "sent": f.sent,
            "numerator": numerator,
            "rate_pct": _pct(numerator, f.sent),
            "ci95_low": low,
            "ci95_high": high,
        })
    rows.sort(key=lambda r: r["rate_pct"], reverse=True)
    return rows
