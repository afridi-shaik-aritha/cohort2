"""Q5 alert monitor — evaluates the Langfuse cost/token metric against the
Q4-derived threshold and records what it did.

Langfuse Cloud's Alerts/Monitors are configured in the dashboard (there is no
public API for them), so this script is the automatable, auditable half: it
queries the same metric over the same window with the Metrics API v2, applies
the same threshold, and — when crossed — writes an alert record to the evidence
log and (if ALERT_WEBHOOK_URL is set) POSTs the notification payload. Running it
on a schedule alongside the dashboard alert gives a log of the alert firing,
which is what Q5's working system check asks for.

Usage:
  python scripts/q5_alert_monitor.py                 # evaluate now
  python scripts/q5_alert_monitor.py --watch 60      # poll every 60s
"""
import argparse
import datetime as _dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from app.q5_alert import (  # noqa: E402
    RUNAWAY_SESSION_TOKENS,
    RUNAWAY_SESSION_USD,
)

DEFAULT_LOG = os.path.join(
    os.path.dirname(BACKEND_DIR), "docs", "evidence", "q5-alert-monitor.jsonl"
)
WINDOW_HOURS = 1
# Q3 generations only — the metric the alert is scoped to.
FILTERS = [{"column": "name", "operator": "=", "value": "answer", "type": "string"}]


def _metrics_query(metric: str, aggregation: str, hours: int) -> float:
    base = (os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("LANGFUSE_HOST / LANGFUSE_BASE_URL not set")
    now = _dt.datetime.now(_dt.timezone.utc)
    query = {
        "view": "observations",
        "metrics": [{"measure": metric, "aggregation": aggregation}],
        "dimensions": [],
        "filters": FILTERS,
        "fromTimestamp": (now - _dt.timedelta(hours=hours)).isoformat(timespec="seconds"),
        "toTimestamp": now.isoformat(timespec="seconds"),
    }
    resp = httpx.get(
        base + "/api/public/v2/metrics",
        auth=(os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")),
        params={"query": json.dumps(query)},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("data") or []
    return float(rows[0].get(f"{aggregation}_{metric}", 0) or 0) if rows else 0.0


def evaluate(window_hours: int = WINDOW_HOURS) -> dict:
    tokens = _metrics_query("totalTokens", "sum", window_hours)
    cost = _metrics_query("totalCost", "sum", window_hours)
    token_trip = tokens >= RUNAWAY_SESSION_TOKENS
    cost_trip = cost >= RUNAWAY_SESSION_USD
    return {
        "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "window_hours": window_hours,
        "metric_filter": "name = answer (Q3 generations)",
        "tokens": int(tokens),
        "token_threshold": RUNAWAY_SESSION_TOKENS,
        "cost_usd": round(cost, 6),
        "cost_threshold_usd": RUNAWAY_SESSION_USD,
        "severity": "ALERT" if (token_trip or cost_trip) else "OK",
        "trips": {"tokens": token_trip, "cost": cost_trip},
    }


def notify(record: dict, log_path: str) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if webhook and record["severity"] == "ALERT":
        try:
            httpx.post(webhook, json=record, timeout=15)
            record["webhook_delivered"] = True
        except Exception as e:  # notification failure must not kill the monitor
            record["webhook_delivered"] = f"failed: {e}"
    elif webhook:
        record["webhook_delivered"] = "skipped (severity OK)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="poll every N seconds")
    ap.add_argument("--window", type=int, default=WINDOW_HOURS, help="window in hours")
    ap.add_argument("--log", default=DEFAULT_LOG)
    args = ap.parse_args()

    while True:
        record = evaluate(args.window)
        notify(record, args.log)
        sev = record["severity"]
        print(
            f"[{record['checked_at']}] {sev}: {record['tokens']:,} tokens "
            f"(threshold {record['token_threshold']:,}) | ${record['cost_usd']:.6f} "
            f"(threshold ${record['cost_threshold_usd']})",
            flush=True,
        )
        if sev == "ALERT":
            print(json.dumps(record, indent=2), flush=True)
            print(f"alert record appended to {args.log}", flush=True)
        if not args.watch:
            return 0 if sev == "OK" else 2
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())