"""Q5 — tripwire ledger: threshold math against the Q4 baseline, plus API shape."""

from app.q5_alert import (
    HEALTHY_TURN_MAX_TOKENS,
    RUNAWAY_SESSION_TOKENS,
    RUNAWAY_SESSION_USD,
    evaluate_paper,
    turn_cost_usd,
)


def _usage(total):
    return {"input": total * 3 // 4, "output": total // 4, "total": total}


def _record(turn_totals):
    return {
        "paper_id": "p_q5",
        "qa_history": [
            {"question": f"q{i}", "usage": _usage(t), "refused": False}
            for i, t in enumerate(turn_totals)
        ],
    }


def test_healthy_session_does_not_trip():
    # 10 normal turns (~2k tokens each) — well under both legs.
    ev = evaluate_paper(_record([2_000] * 10))
    assert ev["turns"] == 10
    assert ev["total_tokens"] == 20_000
    assert ev["alert"] is False
    assert ev["trips"] == {"tokens": False, "cost": False}


def test_runaway_session_trips_both_legs():
    # Q4's runaway shape: 10 turns x (12k in + 14k out) ≈ 260k tokens.
    ev = evaluate_paper(_record([26_000] * 10))
    assert ev["total_tokens"] == 260_000 >= RUNAWAY_SESSION_TOKENS
    assert ev["total_usd"] >= RUNAWAY_SESSION_USD
    assert ev["alert"] is True


def test_single_huge_turn_trips_token_leg():
    ev = evaluate_paper(_record([HEALTHY_TURN_MAX_TOKENS * 4]))
    assert ev["trips"]["tokens"] is True
    assert ev["alert"] is True


def test_empty_history_is_quiet():
    ev = evaluate_paper({"paper_id": "p_none", "qa_history": []})
    assert ev["turns"] == 0 and ev["alert"] is False


def test_turn_cost_math_matches_q4_pricing():
    # 1M in + 1M out at $0.02/$0.10 → $0.12.
    assert abs(turn_cost_usd({"input": 1_000_000, "output": 1_000_000}) - 0.12) < 1e-9
    assert turn_cost_usd(None) == 0.0


def test_spend_endpoint_shape(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q2_paper_inference import storage
    from app.q5_alert.router import router

    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    rec = {"paper_id": "p_api", "owner_id": "demo-user", "qa_history": []}
    storage.save_paper(rec)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.get("/api/q5/papers/p_api/spend")
    assert r.status_code == 200
    body = r.json()
    assert body["evaluation"]["alert"] is False
    assert body["config"]["window"] == "1 hour rolling"
    assert client.get("/api/q5/config").status_code == 200
    assert client.get("/api/q5/papers/nope/spend").status_code == 404


# --- the monitor that proves the dashboard alert fires -----------------------

def _monitor():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "q5_alert_monitor.py"
    spec = importlib.util.spec_from_file_location("q5_alert_monitor", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_monitor_ok_below_threshold(monkeypatch, tmp_path):
    mon = _monitor()

    def fake(metric, agg, hours):
        return 1_000 if metric == "totalTokens" else 0.0005

    monkeypatch.setattr(mon, "_metrics_query", fake)
    rec = mon.evaluate()
    assert rec["severity"] == "OK"
    assert rec["trips"] == {"tokens": False, "cost": False}
    log = tmp_path / "m.jsonl"
    mon.notify(rec, str(log))
    assert '"severity": "OK"' in log.read_text()


def test_monitor_fires_and_logs_alert(monkeypatch, tmp_path):
    mon = _monitor()

    def fake(metric, agg, hours):
        return 150_000 if metric == "totalTokens" else 0.001

    monkeypatch.setattr(mon, "_metrics_query", fake)
    rec = mon.evaluate()
    assert rec["severity"] == "ALERT"
    assert rec["trips"]["tokens"] is True  # token leg carries an unpriced model
    log = tmp_path / "m.jsonl"
    mon.notify(rec, str(log))
    assert '"severity": "ALERT"' in log.read_text()


def test_monitor_cost_leg_fires_independently(monkeypatch):
    mon = _monitor()

    def fake(metric, agg, hours):
        return 1_000 if metric == "totalTokens" else 0.05  # few tokens, high cost

    monkeypatch.setattr(mon, "_metrics_query", fake)
    rec = mon.evaluate()
    assert rec["severity"] == "ALERT"
    assert rec["trips"] == {"tokens": False, "cost": True}


def test_monitor_window_is_parameterised():
    mon = _monitor()
    seen = {}

    def fake(metric, agg, hours):
        seen[metric] = hours
        return 0

    mon._metrics_query = fake  # type: ignore[assignment]
    mon.evaluate(24)
    assert seen == {"totalTokens": 24, "totalCost": 24}
