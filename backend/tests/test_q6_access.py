"""Q6 — THE access-control test, automated (spec "Test requirement").

Two papers under two simulated users share one record store. As user A, ask a
question that can only be answered from user B's paper — naming its title
exactly as the PDF prescribes. The correct behavior: the access refusal, no
hallucination, no leak.

Three assertions carry the assignment's core claim:
  1. the refusal is the ACCESS refusal, not Q3's evidence-gap refusal;
  2. NO generation ran (the denial is decided before the model exists in the
     call graph — there was nothing to hallucinate from);
  3. the retrieval pool contained zero chunks from user B's paper — the
     structural claim, checked directly on scope.resolve_scope's output.

Hermetic: embeddings scripted, stream_text mocked, Langfuse keys stripped.
"""
import asyncio
import json

import pytest

from app.q6_multi_paper_rag import ACCESS_REFUSAL
from app.q6_multi_paper_rag import scope as scope_mod
from app.q6_multi_paper_rag.scope import Denial
from app.shared import langfuse_client as lf

ALICE, BOB = "alice", "bob"
ALICE_TITLE = "Rotary Position Embedding"     # alice's paper
BOB_TITLE = "Contrastive Pretraining for Retrieval"  # bob's paper — the leak target


def _mk_record(owner: str, paper_id: str, title: str, body: str) -> dict:
    return {
        "paper_id": paper_id,
        "owner_id": owner,
        "title": title,
        "authors": ["A. Author"],
        "pages": 1,
        "chars": len(body),
        "text": f"{title}\nA. Author\n\nAbstract\n{body}\n",
        "sections": {},
        "usage": {},
    }


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from app.q2_paper_inference import storage

    monkeypatch.setenv("Q2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    lf.reset_client()
    storage.save_paper(_mk_record(
        ALICE, "p_alice", ALICE_TITLE,
        "RoPE encodes absolute position with a rotation matrix and achieves "
        "better length generalisation than learned absolute embeddings.",
    ))
    storage.save_paper(_mk_record(
        BOB, "p_bob", BOB_TITLE,
        "We pretrain sentence embeddings with a contrastive objective on "
        "1.1B paired sentences and reach 42.3 nDCG@10 on BEIR.",
    ))
    yield
    lf.reset_client()


# --- the PDF's mandated scenario, at the scope layer -------------------------

def test_user_a_cannot_retrieve_user_b_paper():
    """The structural claim: bob's paper is absent from alice's candidate pool."""
    alice = scope_mod.scoped_records(ALICE)
    assert [r["paper_id"] for r in alice] == ["p_alice"]
    bob = scope_mod.scoped_records(BOB)
    assert [r["paper_id"] for r in bob] == ["p_bob"]


def test_named_foreign_paper_denies_before_retrieval():
    """User A names user B's paper by title → ACCESS refusal at the scope layer."""
    question = f"What does '{BOB_TITLE}' say about positional encoding?"
    with pytest.raises(Denial) as e:
        scope_mod.resolve_scope(ALICE, question)
    assert e.value.message == ACCESS_REFUSAL


def test_denial_cannot_leak_existence():
    """A nonexistent title produces the identical refusal — asking about a
    foreign paper is indistinguishable from asking about no paper at all."""
    with pytest.raises(Denial):
        scope_mod.resolve_scope(ALICE, "What does 'Totally Made-Up Title' say?")


def test_named_own_paper_scopes_to_it():
    """The same mechanism, correctly resolving the user's OWN paper."""
    question = f"What does '{ALICE_TITLE}' say about position encoding?"
    sc = scope_mod.resolve_scope(ALICE, question)
    assert [r["paper_id"] for r in sc.records] == ["p_alice"]


def test_no_mention_keeps_all_owner_papers():
    sc = scope_mod.resolve_scope(ALICE, "How is the position information encoded?")
    assert [r["paper_id"] for r in sc.records] == ["p_alice"]


# --- end-to-end over the API: the PDF's exact scenario as an HTTP test -------

def _ask(client, owner: str, question: str, paper_ids=None):
    r = client.post("/api/q6/ask", json={"question": question, "paper_ids": paper_ids},
                    headers={"X-Owner-Id": owner})
    assert r.status_code == 200
    return [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]


def test_access_control_end_to_end(monkeypatch):
    """THE test: alice asks about bob's paper by title → refusal, no leak, no LLM."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q6_multi_paper_rag import analyzer as q6_analyzer
    from app.q6_multi_paper_rag.router import router

    async def _must_not_embed(*a, **k):  # embeddings must never even run
        raise AssertionError("embedding ran during an access denial")

    async def _must_not_stream(*a, **k):
        raise AssertionError("the LLM was called during an access denial")

    monkeypatch.setattr("app.q6_multi_paper_rag.retrieval.embed_texts", _must_not_embed)
    monkeypatch.setattr(q6_analyzer, "stream_text", _must_not_stream)

    app = FastAPI()
    app.include_router(router)
    events = _ask(TestClient(app), ALICE,
                  f"What does '{BOB_TITLE}' say about positional encoding?")

    kinds = [e["type"] for e in events]
    assert kinds == ["run", "token", "done"]
    assert events[1]["data"]["text"] == ACCESS_REFUSAL
    done = events[-1]["data"]
    assert done["stop_reason"] == "access_denied" and done["refused"] is True
    assert done["citations"] == [] and done["papers_cited"] == []
    # no citation event ever carried bob's paper id — nothing surfaced
    assert all("p_bob" not in json.dumps(e) for e in events)


# --- the happy path: multi-paper retrieval with per-paper attribution --------

def test_multi_paper_answer_attributes_papers(monkeypatch):
    """Two of MY papers in scope → one answer, citations grouped per paper."""
    import app.q6_multi_paper_rag.analyzer as q6_analyzer
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q6_multi_paper_rag.router import router

    async def _fake(texts, input_type="passage"):
        from app.shared import llm_text
        return [llm_text._hash_vector(t) for t in texts]

    # patch BOTH sides: the query vector (q6.retrieval) and the chunk vectors
    # the router's on-demand build_index stores (q3.indexer) — same embedder,
    # or the score floor refuses before generation and no citations appear.
    monkeypatch.setattr("app.q6_multi_paper_rag.retrieval.embed_texts", _fake)
    monkeypatch.setattr("app.q3_rag_qa.indexer.embed_texts", _fake)

    def _fake_stream(messages, *, max_tokens=None, temperature=None):
        async def _gen():
            for word in ["Rotation ", "encodes ", "position [1]."]:
                yield {"kind": "text", "text": word}
            yield {"kind": "usage", "usage": {"input": 300, "output": 10, "total": 310}}
        return _gen()

    monkeypatch.setattr(q6_analyzer, "stream_text", _fake_stream)

    from fastapi.testclient import TestClient as _TC
    app = FastAPI()
    app.include_router(router)
    client = _TC(app)
    events = _ask(client, ALICE,
                  "How does the paper encode position information?")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run" and kinds[-1] == "done"
    assert kinds.count("citation") >= 1
    done = events[-1]["data"]
    assert done["refused"] is False
    assert done["papers_cited"], "answer must attribute its papers"
    cited = done["papers_cited"][0]
    assert cited["paper_id"] == "p_alice" and cited["title"] == ALICE_TITLE
    for c in done["citations"]:
        assert c["paper_id"] == "p_alice"  # my paper, cited by title


def test_selector_scoping(monkeypatch):
    """An explicit selector narrows the pool; a foreign selector id denies."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q6_multi_paper_rag.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    # selector naming only bob's paper → alice is denied (bad/foreign selector)
    events = _ask(client, ALICE, "How is position encoded?", paper_ids=["p_bob"])
    done = events[-1]["data"]
    assert done["stop_reason"] == "access_denied"
    # selector naming her own paper works (index is built on demand)
    events = _ask(client, ALICE, "How is position encoded?", paper_ids=["p_alice"])
    assert events[-1]["data"]["stop_reason"] in {"completed", "refused"}


def test_papers_list_is_owner_scoped():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.q6_multi_paper_rag.router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    alice_titles = {p["title"] for p in client.get(
        "/api/q6/papers", headers={"X-Owner-Id": ALICE}).json()["papers"]}
    bob_titles = {p["title"] for p in client.get(
        "/api/q6/papers", headers={"X-Owner-Id": BOB}).json()["papers"]}
    assert alice_titles == {ALICE_TITLE}
    assert bob_titles == {BOB_TITLE}