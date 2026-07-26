"""
Turns today's list of flagged deals into one short, readable notification
instead of N separate raw price alerts.

This is the part worth highlighting in an interview: it's not just
"if price < threshold: send message" — the model is doing the ranking and
summarization step, and this is also the natural place to grow into a real
planning agent later (deciding which routes/dates are worth checking given
a request budget, rather than a fixed loop over every route).

Uses Haiku by default — this is a cheap summarization task, not one that
needs a frontier model, and that's a deliberate cost/capability trade-off
worth being able to explain.
"""
from __future__ import annotations

import json
import os

import anthropic

from src.agent.cost import CostTracker

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

_SYSTEM = """You write short, friendly push-notification text about cheap \
travel deals for one person. Be concise (2-4 sentences total for the whole \
digest, even with multiple deals). Mention the route, price, and why it's \
notable (e.g. "40% below usual"). No markdown, no emoji spam (one is fine). \
If there are multiple deals, rank the best one first."""


def build_digest(deals: list[dict], cost_tracker: CostTracker | None = None) -> str:
    """
    deals: list of dicts like
      {"route": "Brussels -> Paris", "price_eur": 24, "reason": "35% below avg",
       "depart_date": "2026-08-14", "url": "..."}
    """
    if not deals:
        return ""

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(deals, indent=2)}],
    )
    if cost_tracker is not None:
        cost_tracker.record(MODEL, response.usage)
    return "".join(block.text for block in response.content if block.type == "text")


if __name__ == "__main__":
    sample = [
        {
            "route": "Brussels -> Paris",
            "price_eur": 22,
            "reason": "38% below 180d average",
            "depart_date": "2026-08-14",
            "url": "https://example.com/booking",
        }
    ]
    print(build_digest(sample))
