"""Token prices for run-cost estimates ($ per 1M tokens: input, output).

Anthropic first-party list prices (claude-api reference, cached 2026-11-03). Moonshot prices are
not public in this repo: set EVAL_PRICE_<MODEL>="in,out" (model id upper-cased, '-'/'.' -> '_'),
e.g. EVAL_PRICE_KIMI_K3_1_PRO_HIGHSPEED="0.6,2.5"; without it the cost of that role is reported as
unknown (tokens are always reported)."""

import os

PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def price(model: str) -> tuple[float, float] | None:
    env = os.environ.get("EVAL_PRICE_" + model.upper().replace("-", "_").replace(".", "_"))
    if env:
        a, b = env.split(",")
        return float(a), float(b)
    return PRICES.get(model)


def run_cost(counts: dict, models: dict[str, str]) -> dict:
    """counts: timing counts (tokens_in_<role>, tokens_out_<role>); models: role -> model id."""
    out, total, known = {}, 0.0, True
    for role, model in models.items():
        tin, tout = counts.get(f"tokens_in_{role}", 0), counts.get(f"tokens_out_{role}", 0)
        p = price(model)
        usd = None if p is None else round((tin * p[0] + tout * p[1]) / 1e6, 4)
        known &= usd is not None or (tin + tout) == 0
        total += usd or 0.0
        out[role] = {"model": model, "tokens_in": tin, "tokens_out": tout, "usd": usd}
    out["usd_total"] = round(total, 4) if known else None
    out["usd_known_part"] = round(total, 4)
    return out
