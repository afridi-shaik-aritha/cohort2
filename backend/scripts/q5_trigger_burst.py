"""Q5 deliberate trigger: burst of expensive Q3 turns in a short window.

Simulates the Q4 runaway pattern (the same expensive generation repeated, many
times, in one window) using the real Q3 pipeline and the live provider, so the
resulting metric is genuine Langfuse data — not a synthetic number.

Usage: python scripts/q5_trigger_burst.py [paper_id] [n_turns] [question]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

paper_id = sys.argv[1] if len(sys.argv) > 1 else "p_9c3022cf"
n_turns = int(sys.argv[2]) if len(sys.argv) > 2 else 20
question = sys.argv[3] if len(sys.argv) > 3 else "What are the references?"

client = TestClient(app)
total = 0
for i in range(n_turns):
    started = time.time()
    with client.stream(
        "POST", f"/api/q3/papers/{paper_id}/ask", json={"question": question}
    ) as r:
        body = "".join(r.iter_text())
    done = [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data:")][-1]
    usage = done["data"].get("usage") or {}
    total += usage.get("total", 0)
    print(
        f"[{i + 1}/{n_turns}] {done['data'].get('stop_reason')} "
        f"tokens={usage.get('total')} cumulative={total} "
        f"({time.time() - started:.1f}s)",
        flush=True,
    )
print(f"BURST COMPLETE cumulative_tokens={total}", flush=True)