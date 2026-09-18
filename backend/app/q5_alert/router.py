"""Q5 API: evaluate a paper's Q&A spend against the runaway tripwire.

Endpoints (docs/specs/q5-spec.md "API contract"):
  GET /api/q5/papers/{id}/spend    per-paper ledger evaluation (local record data)
  GET /api/q5/config                the alert definition this mirrors in Langfuse
"""
from fastapi import APIRouter, HTTPException

from app.q2_paper_inference import storage
from app.q5_alert import (
    HEALTHY_TURN_MAX_TOKENS,
    PRICE_IN_PER_TOKEN,
    PRICE_OUT_PER_TOKEN,
    RUNAWAY_SESSION_TOKENS,
    RUNAWAY_SESSION_USD,
    evaluate_paper,
)

router = APIRouter(prefix="/api/q5", tags=["q5"])

ALERT_CONFIG = {
    "alert_type": "project-scoped metric alert (Observability → Alerts), NOT account Billing Spend Alert",
    "metric": "totalCost (USD) on observations, filtered to name = answer (Q3 generations)",
    "companion_metric": "totalTokens, same filter — covers priced and unpriced models",
    "window": "1 hour rolling",
    "threshold_usd": RUNAWAY_SESSION_USD,
    "threshold_tokens": RUNAWAY_SESSION_TOKENS,
    "single_turn_tokens": HEALTHY_TURN_MAX_TOKENS * 4,
    "channel": "webhook (automation linked in the dashboard; see proof template)",
    "pricing_basis": {
        "price_in_per_token": PRICE_IN_PER_TOKEN,
        "price_out_per_token": PRICE_OUT_PER_TOKEN,
    },
}


@router.get("/config")
def alert_config() -> dict:
    """The tripwire definition (mirrors the Langfuse dashboard alert)."""
    return ALERT_CONFIG


@router.get("/papers/{paper_id}/spend")
def paper_spend(paper_id: str, window: int = 20) -> dict:
    """Evaluate one paper's recent Q&A spend against the tripwire."""
    record = storage.load_paper(paper_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"config": ALERT_CONFIG, "evaluation": evaluate_paper(record, window=window)}
