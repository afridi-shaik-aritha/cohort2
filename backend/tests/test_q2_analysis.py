"""Q2 — analyzer: section order, token tagging, cancellation, protocol shapes."""
import asyncio

import pytest

import app.q2_paper_inference.analyzer as analyzer_mod
from app.q2_paper_inference.analyzer import MAX_INPUT_CHARS, analyze_events, strategy_for


def _record(text=None):
    text = text or "Body text about the method and results. " * 40
    return {
        "paper_id": "p_test",
        "owner_id": "demo-user",
        "title": "T",
        "authors": ["A"],
        "pages": 3,
        "chars": len(text),
        "text": text,
        "created_at": "now",
        "sections": {},
        "usage": {},
        "extraction": {"strategy": "single_pass", "warnings": []},
    }


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """The analyzer persists each section as it finishes — keep tests out of
    the real data dir, and keep tracing off so flush() can't block on network
    (keys may be configured in backend/.env). Tracing itself is covered in
    test_q2_langfuse.py plus the live end-to-end check."""
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


@pytest.mark.anyio
async def test_four_sections_in_order(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    events = [e async for e in analyze_events(_record(), cancel_ev=asyncio.Event())]
    keys = [e["data"]["key"] for e in events if e["type"] == "section_start"]
    assert keys == ["technical", "intuition", "prerequisites", "summary"]
    assert [e["type"] for e in events].count("section_done") == 4
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["stop_reason"] == "completed"
    assert all("section" in e["data"] for e in events if e["type"] == "token")


@pytest.mark.anyio
async def test_cancel_stops_generation(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    ev = asyncio.Event()
    out = []
    agen = analyze_events(_record(), cancel_ev=ev)
    try:
        async for e in agen:
            out.append(e)
            if e["type"] == "section_done":
                ev.set()
    finally:
        # close the abandoned generator so its tracing context managers exit
        # cleanly instead of leaking a suspended generator into the next test
        await agen.aclose()
    assert out[-1]["data"]["stop_reason"] == "cancelled"
    assert len([e for e in out if e["type"] == "section_done"]) == 1


@pytest.mark.anyio
async def test_strategy_thresholds(monkeypatch):
    assert strategy_for("x" * 100) == "single_pass"
    assert strategy_for("x" * (MAX_INPUT_CHARS + 1)) == "map_reduce"


@pytest.mark.anyio
async def test_map_reduce_runs_digests_then_sections(monkeypatch):
    """Map-reduce end-to-end with a controlled fake stream (the mock provider's
    sleeps make this path timing-fragile under the anyio test loop)."""
    async def fake_stream(messages, *, max_tokens=None, temperature=None):
        for word in ["digest", "content ", "about ", "attention."]:
            yield {"kind": "text", "text": word}
        yield {"kind": "usage", "usage": {"input": 10, "output": 4, "total": 14}}

    monkeypatch.setattr(analyzer_mod, "stream_text", fake_stream)
    big = ("Sentence about attention and BLEU scores. " * 30 + "\n") * 40  # > 40k chars
    events = [
        e async for e in analyze_events(_record(text=big), cancel_ev=asyncio.Event())
    ]
    types = [e["type"] for e in events]
    assert types.count("section_done") == 4
    assert types[-1] == "done"
    assert events[-1]["data"]["stop_reason"] == "completed"
    # every section's generation saw usage reported by the fake stream
    dones = [e for e in events if e["type"] == "section_done"]
    assert all(d["data"]["usage"] is not None for d in dones)
