"""
Decides whether an observed price counts as a "deal" for a given route.

Two independent triggers, either one is enough to flag:
  1. percent_below_avg: price is X% below the rolling average for that route
     (adapts over time as you build up history — this is the interesting
     part for the AI-engineer story, since it's a live-updating baseline
     rather than a static number).
  2. hard_cap_eur: an absolute floor that always flags, useful before you
     have enough history for a meaningful average, or as a "never miss this"
     safety net.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import Route
from src.storage.influx import rolling_average


@dataclass
class DealEvaluation:
    is_deal: bool
    reason: str
    observed_price: float
    baseline_avg: float | None


def evaluate(route: Route, observed_price: float) -> DealEvaluation:
    if observed_price <= route.hard_cap_eur:
        return DealEvaluation(
            is_deal=True,
            reason=f"below hard cap (€{route.hard_cap_eur})",
            observed_price=observed_price,
            baseline_avg=None,
        )

    avg = rolling_average(route.name, route.threshold.lookback_days)
    if avg is None:
        # No history yet — can't evaluate percent-below-average.
        return DealEvaluation(
            is_deal=False,
            reason="no price history yet for percent-based comparison",
            observed_price=observed_price,
            baseline_avg=None,
        )

    if route.threshold.type == "percent_below_avg":
        drop_pct = (avg - observed_price) / avg * 100
        if drop_pct >= route.threshold.value:
            return DealEvaluation(
                is_deal=True,
                reason=f"{drop_pct:.0f}% below {route.threshold.lookback_days}d average (€{avg:.0f})",
                observed_price=observed_price,
                baseline_avg=avg,
            )

    return DealEvaluation(
        is_deal=False,
        reason=f"within normal range (avg €{avg:.0f})",
        observed_price=observed_price,
        baseline_avg=avg,
    )
