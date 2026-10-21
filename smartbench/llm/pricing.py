"""Indicative provider pricing for run-level cost estimates.

Prices are **USD per 1M tokens** and are indicative only: providers change
them, and a configured model may not be in this table. Unknown models return
``None`` instead of a made-up number. Two environment variables override the
table for any model::

    SMARTBENCH_PRICE_INPUT_PER_M=0.27
    SMARTBENCH_PRICE_OUTPUT_PER_M=1.10
"""

from __future__ import annotations

import math
import os

_PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
}

_INPUT_ENV = "SMARTBENCH_PRICE_INPUT_PER_M"
_OUTPUT_ENV = "SMARTBENCH_PRICE_OUTPUT_PER_M"


def estimate_cost(
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> float | None:
    """Return estimated USD cost, or ``None`` when the model is unknown."""
    if not model:
        return None
    overrides = (_env_price(_INPUT_ENV), _env_price(_OUTPUT_ENV))
    if overrides[0] is not None and overrides[1] is not None:
        input_price, output_price = overrides
    else:
        prices = _PRICES_PER_MILLION.get(model)
        if prices is None:
            return None
        input_price, output_price = prices
    cost = (
        max(0, prompt_tokens) * input_price
        + max(0, completion_tokens) * output_price
    ) / 1_000_000
    return round(cost, 6)


def _env_price(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value
