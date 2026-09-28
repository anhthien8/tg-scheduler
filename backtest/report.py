"""Markdown report rendering for backtests."""
from __future__ import annotations

from backtest.analytics import Funnel, compare_variants, two_proportion_z


def _fmt_latency(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def render_report(title: str, funnels: dict[int, Funnel], overall: Funnel) -> str:
    lines: list[str] = []
    lines.append(f"\n# {title}")
    lines.append("\n## Overall funnel")
    lines.append("| Metric | Count | Rate (of sent) |")
    lines.append("| :--- | :---: | :---: |")
    lines.append(f"| Messages sent (success) | {overall.sent} | — |")
    lines.append(f"| Replied (unique leads) | {overall.replied} | {overall.reply_rate}% |")
    lines.append(f"| Converted (intent ≥ threshold) | {overall.converted} | {overall.conversion_rate}% |")
    lines.append(f"| Onboarded | {overall.onboarded} | {overall.onboard_rate}% |")
    lines.append("\n> **Reach is a lower bound.** Telegram has no read receipts, so "
                 "`reach_rate` = reply_rate: a reply proves the message was seen. "
                 "Do not read this as a true open rate.")
    lines.append(f"\n**Speed to reply:** median {_fmt_latency(overall.median_latency())}, "
                 f"p90 {_fmt_latency(overall.p90_latency())} (n={len(overall.latencies)})")

    if funnels:
        lines.append("\n## Variant performance (with 95% Wilson CI)")
        lines.append("| Variant | Sent | Replied | Reply % | 95% CI |")
        lines.append("| :---: | :---: | :---: | :---: | :---: |")
        for row in compare_variants(funnels, metric="replied"):
            ci = f"[{row['ci95_low']}–{row['ci95_high']}]"
            lines.append(f"| {row['variant']} | {row['sent']} | {row['numerator']} | "
                         f"{row['rate_pct']}% | {ci} |")

        ranking = compare_variants(funnels, metric="replied")
        if len(ranking) >= 2:
            best, second = ranking[0], ranking[1]
            test = two_proportion_z(best["numerator"], best["sent"],
                                    second["numerator"], second["sent"])
            verdict = "significant" if test["significant"] else "NOT significant"
            lines.append(f"\n**Top-2 comparison:** variant {best['variant']} vs {second['variant']} — "
                         f"z={test['z']}, p={test['p_value']} → **{verdict}** at α=0.05"
                         + (f" (lift {test['lift_pct']}%)" if test["lift_pct"] is not None else ""))

    lines.append("\n" + "=" * 50 + "\n")
    return "\n".join(lines)
