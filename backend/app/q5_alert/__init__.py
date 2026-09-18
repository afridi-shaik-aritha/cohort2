"""Q5 — per-paper token/cost ledger for the runaway-spend alert tripwire.

Langfuse Cloud (this project's plan) has no Alerts/Monitors API surface — the
alert itself is configured in the Langfuse dashboard (see
docs/q5-alert-proof-template.md for the exact click path). What CAN live in
code is the tripwire logic: given the usage this system already persists per
Q&A turn (paper record `qa_history[].usage`), decide whether a paper's recent
spend is abnormal relative to the Q4 healthy baseline. The /q5 page surfaces
this evaluation; the Langfuse dashboard alert is the production tripwire and
this module is its auditable, testable mirror.
"""

from __future__ import annotations

# Q4 healthy baseline, measured on live Q3 traces (docs/q4-writeup-template.md):
# normal turns cost hundreds to a few thousand tokens; the references answer is
# the most expensive legit turn at ~5.4k tokens. The Q4 runaway session costs
# ~10x the healthy paper-day (~$0.0015 → ~$0.016).
HEALTHY_TURN_MAX_TOKENS = 6_000  # above the priciest legit single turn
RUNAWAY_SESSION_TOKENS = 100_000  # ~10x a heavy healthy paper-day, per Q4 math
RUNAWAY_SESSION_USD = 0.008  # ~5x the Q4 healthy paper-day ($0.0015)

# gpt-oss-20b via OpenRouter (Q4 basis, conservative of the two sources).
PRICE_IN_PER_TOKEN = 0.02 / 1_000_000
PRICE_OUT_PER_TOKEN = 0.10 / 1_000_000

__all__ = [
    "HEALTHY_TURN_MAX_TOKENS",
    "RUNAWAY_SESSION_TOKENS",
    "RUNAWAY_SESSION_USD",
    "PRICE_IN_PER_TOKEN",
    "PRICE_OUT_PER_TOKEN",
    "turn_cost_usd",
    "evaluate_paper",
]


def turn_cost_usd(usage: dict | None) -> float:
    """Estimated USD for one turn from its {input, output} token counts."""
    if not usage:
        return 0.0
    try:
        return float(usage.get("input", 0) or 0) * PRICE_IN_PER_TOKEN + float(
            usage.get("output", 0) or 0
        ) * PRICE_OUT_PER_TOKEN
    except (TypeError, ValueError):
        return 0.0


def evaluate_paper(record: dict, *, window: int = 20) -> dict:
    """Evaluate a paper's recent Q&A spend against the Q4-derived tripwire.

    Returns a JSON-serializable dict: turn/window totals, estimated cost, the
    threshold comparison, and whether EITHER the token or the cost leg trips.
    Pure function of the stored record — no network, no Langfuse calls.
    """
    history = (record or {}).get("qa_history") or []
    recent = history[-window:] if window > 0 else []
    turns = len(recent)
    total_tokens = 0
    total_usd = 0.0
    max_turn_tokens = 0
    for turn in recent:
        usage = turn.get("usage") or {}
        try:
            total = int(usage.get("total", 0) or 0)
        except (TypeError, ValueError):
            total = 0
        total_tokens += total
        max_turn_tokens = max(max_turn_tokens, total)
        total_usd += turn_cost_usd(usage)
    token_trip = total_tokens >= RUNAWAY_SESSION_TOKENS or max_turn_tokens >= HEALTHY_TURN_MAX_TOKENS * 4
    cost_trip = total_usd >= RUNAWAY_SESSION_USD
    return {
        "paper_id": (record or {}).get("paper_id"),
        "turns": turns,
        "window": window,
        "total_tokens": total_tokens,
        "total_usd": round(total_usd, 6),
        "max_turn_tokens": max_turn_tokens,
        "thresholds": {
            "healthy_turn_max_tokens": HEALTHY_TURN_MAX_TOKENS,
            "runaway_session_tokens": RUNAWAY_SESSION_TOKENS,
            "runaway_session_usd": RUNAWAY_SESSION_USD,
        },
        "trips": {"tokens": token_trip, "cost": cost_trip},
        "alert": bool(token_trip or cost_trip),
    }
