"""Q3 — API + QA generator: event order, refusal gates, persistence.

Hermetic by construction: Langfuse keys stripped (tracing becomes a no-op),
embeddings replaced by deterministic topic vectors (exact cosines), stream_text
always mocked (the suite may load real .env keys — no network allowed).
"""
import asyncio
import json
import math

import pytest

from app.q3_rag_qa import analyzer, indexer, retrieval
from app.shared import cancel_registry
from app.shared import langfuse_client as lf


def _unit(vec):
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def _topic_vec(topic: str, dim: int = 384):
    h = int.from_bytes(topic.encode(), "big")
    v = [0.0] * dim
    v[h % dim] = 1.0
    v[(h // dim) % dim] = 0.5
    return _unit(v)


def _topic_of(text: str) -> str:
    text = text.lower()
    if "bleu" in text or "wmt" in text or "gpu" in text or "tested" in text:
        return "eval"
    if "breakfast" in text or "egg" in text or "toast" in text:
        return "breakfast"
    return "misc"


def _patch_eval_retrieval(monkeypatch, query_topic="eval"):
    """Chunk AND query vectors come from scripted topics, so retrieval scores
    are exact. query_topic='eval' → retrieval always succeeds (generation-gate
    tests); query_topic=None → queries embed to their own natural topic, so the
    breakfast question genuinely falls below the floor (retrieval-gate test)."""

    async def _fake(texts, input_type="passage"):
        return [
            _topic_vec(query_topic or _topic_of(t)) for t in texts
        ]

    monkeypatch.setattr(retrieval, "embed_texts", _fake)
    monkeypatch.setattr(indexer, "embed_texts", _fake)


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    lf.reset_client()
    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    yield
    lf.reset_client()


def _record(monkeypatch, paper_id="p_qa", text=None):
    from app.q2_paper_inference import storage

    rec = {
        "paper_id": paper_id,
        "owner_id": "demo-user",
        "title": "Attention Is All You Need",
        "authors": ["A. Vaswani", "N. Shazeer"],
        "text": text or (
            "Attention Is All You Need\nA. Vaswani\n\n"
            "1\nTraining\nWe test on WMT 2014 English-to-German with BLEU "
            "scores on 8 NVIDIA Pascal GPUs for 3.5 days.\n\n"
            "2\nResults\nOur model achieves 28.4 BLEU on the translation task.\n"
        ),
    }
    storage.save_paper(rec)
    return rec


def _index(rec, monkeypatch, query_topic=None):
    async def _fake(texts, input_type="passage"):
        return [_topic_vec(_topic_of(t)) for t in texts]

    monkeypatch.setattr(indexer, "embed_texts", _fake)  # chunks keep real topics
    asyncio.run(indexer.build_index(rec))


def _fake_stream(chunks, usage=None):
    async def _stream(messages, *, max_tokens=None, temperature=None):
        for c in chunks:
            yield {"kind": "text", "text": c}
            await asyncio.sleep(0)
        yield {"kind": "usage", "usage": usage or {"input": 100, "output": 20, "total": 120}}

    return _stream


async def _collect(gen):
    return [item async for item in gen]


# --- answer events ------------------------------------------------------------

def test_semantic_answer_citations_first_then_tokens(monkeypatch):
    rec = _record(monkeypatch)
    _index(rec, monkeypatch)
    _patch_eval_retrieval(monkeypatch)  # query → 'eval' topic: retrieval succeeds
    monkeypatch.setattr(analyzer, "stream_text",
                        _fake_stream(["The model was evaluated ",
                                      "with BLEU on WMT 2014 [1]."]))
    _, ev = cancel_registry.create_run()
    events = asyncio.run(_collect(analyzer.answer_events(rec, "How was this tested?", ev)))

    kinds = [e["type"] for e in events]
    assert kinds[0] == "citation"
    assert "token" in kinds
    assert kinds[-1] == "done"
    done = events[-1]["data"]
    assert done["refused"] is False
    assert done["usage"]["total"] == 120
    assert done["citations"]
    assert "Training" in done["citations"][0]["label"] or \
        "Results" in done["citations"][0]["label"]
    answer = "".join(e["data"]["text"] for e in events if e["type"] == "token")
    assert "BLEU" in answer


def test_retrieval_gate_refuses_without_generation(monkeypatch):
    rec = _record(monkeypatch)
    _index(rec, monkeypatch)
    # queries embed to their own topic → breakfast lands below the floor
    called = False

    def _must_not_stream(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("generation must not run below the retrieval floor")

    monkeypatch.setattr(analyzer, "stream_text", _must_not_stream)
    _, ev = cancel_registry.create_run()
    events = asyncio.run(
        _collect(analyzer.answer_events(rec, "What did the authors have for breakfast?", ev))
    )
    done = events[-1]["data"]
    assert done["refused"] is True
    tokens = "".join(e["data"]["text"] for e in events if e["type"] == "token")
    assert tokens == analyzer.REFUSAL_MESSAGE
    assert called is False


def test_generation_gate_not_in_paper_marker_is_buffered(monkeypatch):
    rec = _record(monkeypatch)
    _index(rec, monkeypatch)
    _patch_eval_retrieval(monkeypatch)  # retrieval passes; the MODEL refuses
    # marker arrives split across deltas so the prefix buffer is exercised
    monkeypatch.setattr(analyzer, "stream_text", _fake_stream(["NOT", "_IN_", "PAPER"]))
    _, ev = cancel_registry.create_run()
    events = asyncio.run(
        _collect(analyzer.answer_events(rec, "What energy source powered the GPUs?", ev))
    )
    tokens = [e["data"]["text"] for e in events if e["type"] == "token"]
    joined = "".join(tokens)
    assert "NOT_IN_PAPER" not in joined, "raw marker must never reach the stream"
    assert analyzer.REFUSAL_MESSAGE in joined
    done = events[-1]["data"]
    assert done["refused"] is True


def test_metadata_path_answers_from_record(monkeypatch):
    rec = _record(monkeypatch)
    _index(rec, monkeypatch)
    _patch_eval_retrieval(monkeypatch)
    streamed = []

    def _capture(messages, *, max_tokens=None, temperature=None):
        async def _s():
            streamed.append(messages)
            yield {"kind": "text", "text": "The authors are A. Vaswani and N. Shazeer [1]."}
            yield {"kind": "usage", "usage": None}
        return _s()

    monkeypatch.setattr(analyzer, "stream_text", _capture)
    _, ev = cancel_registry.create_run()
    events = asyncio.run(_collect(analyzer.answer_events(rec, "Who are the authors?", ev)))
    done = events[-1]["data"]
    assert done["refused"] is False
    assert done["citations"][0]["label"] == "Page 1 (title block)"
    # the model saw ONLY the metadata block, not paper chunks (Message objects)
    user_msg = streamed[0][1].content
    assert "A. Vaswani" in user_msg
    assert "28.4 BLEU" not in user_msg


def test_turn_persisted_to_record(monkeypatch):
    from app.q2_paper_inference import storage

    rec = _record(monkeypatch)
    _index(rec, monkeypatch)
    _patch_eval_retrieval(monkeypatch)
    monkeypatch.setattr(analyzer, "stream_text", _fake_stream(["An answer [1]."]))
    _, ev = cancel_registry.create_run()
    asyncio.run(_collect(analyzer.answer_events(rec, "How was this tested?", ev)))
    history = storage.load_paper(rec["paper_id"])["qa_history"]
    assert len(history) == 1
    turn = history[0]
    assert turn["refused"] is False
    assert turn["path"] == "semantic"
    assert turn["usage"]["total"] == 120
    assert turn["citations"]


# --- router endpoints ----------------------------------------------------------

def test_index_endpoints_and_ask_sse(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q3_rag_qa.router import router

    rec = _record(monkeypatch)
    _patch_eval_retrieval(monkeypatch, query_topic=None)  # index build stays offline
    monkeypatch.setattr(analyzer, "stream_text",
                        _fake_stream(["The authors are A. Vaswani and N. Shazeer [1]."]))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get(f"/api/q3/papers/{rec['paper_id']}/index")
    assert r.status_code == 200 and r.json() == {"indexed": False}

    r = client.post(f"/api/q3/papers/{rec['paper_id']}/index")
    assert r.status_code == 200
    body = r.json()
    assert body["chunks"] > 0 and body["has_references"] is False

    r = client.get(f"/api/q3/papers/{rec['paper_id']}/index")
    assert r.json()["indexed"] is True

    r = client.post(f"/api/q3/papers/{rec['paper_id']}/ask",
                    json={"question": "Who are the authors?"})
    assert r.status_code == 200
    events = [json.loads(line[len("data: "):]) for line in r.text.splitlines()
              if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run"
    assert "citation" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["data"]["refused"] is False
    assert events[-1]["data"]["citations"][0]["label"] == "Page 1 (title block)"


def test_ask_on_demand_indexing(monkeypatch):
    """First question on an un-indexed paper builds the index automatically."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q3_rag_qa.router import router

    rec = _record(monkeypatch, paper_id="p_ondemand")
    _patch_eval_retrieval(monkeypatch, query_topic=None)
    monkeypatch.setattr(analyzer, "stream_text", _fake_stream(["Answer [1]."]))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post(f"/api/q3/papers/{rec['paper_id']}/ask",
                    json={"question": "Who are the authors?"})
    assert r.status_code == 200
    status = client.get(f"/api/q3/papers/{rec['paper_id']}/index").json()
    assert status["indexed"] is True


def test_ask_empty_question_422(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q3_rag_qa.router import router

    _record(monkeypatch, paper_id="p_v")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.post("/api/q3/papers/p_v/ask",
                       json={"question": "   "}).status_code == 422


def test_ask_missing_paper_404():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q3_rag_qa.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.post("/api/q3/papers/nope/ask",
                       json={"question": "hi"}).status_code == 404
    assert client.get("/api/q3/papers/nope/index").status_code == 404
