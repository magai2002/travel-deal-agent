"""
Tracks the estimated USD cost of Claude API calls across one run.

Pricing is a snapshot of Anthropic's list prices (per 1M tokens) - update
PRICING if prices change. This deliberately doesn't account for prompt-cache
read/write rates since nothing in this app uses cache_control yet.
"""
from __future__ import annotations

from dataclasses import dataclass

# (input $/1M tokens, output $/1M tokens)
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
_DEFAULT_PRICING = (3.00, 15.00)  # used if a model isn't in the table above


@dataclass
class CostTracker:
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    calls: int = 0

    def record(self, model: str, usage) -> None:
        input_rate, output_rate = PRICING.get(model, _DEFAULT_PRICING)
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_cost_usd += (
            usage.input_tokens / 1_000_000 * input_rate
            + usage.output_tokens / 1_000_000 * output_rate
        )
        self.calls += 1

    def summary(self) -> str:
        return (
            f"{self.calls} Claude API call(s), "
            f"{self.total_input_tokens}+{self.total_output_tokens} tokens (in+out), "
            f"~${self.total_cost_usd:.4f} estimated"
        )
